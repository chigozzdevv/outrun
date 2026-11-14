#!/usr/bin/env python3
"""Replay the modular research against frozen evidence in an isolated workspace."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from compute.src.paths import ROOT, CODE_ROOT
from compute.src.archive import extract

VERIFIED = Path('data/verified')
TREASURY = VERIFIED / 'treasury-outflows-2026-11-02'
# Collection and mutable work queues stay outside this frozen replay suite.
STUDIES = [
    'compute.src.treasury.balances',
    'compute.src.routing.interfaces',
    'compute.src.verifier.balances',
    'compute.src.withdrawals.reconcile verify',
    'compute.src.withdrawals.timing',
    'compute.src.collectors.exchanges',
    'compute.src.collectors.flows',
    'compute.src.collectors.boundaries',
    'compute.src.collectors.balances',
    'compute.src.collectors.opening',
    'compute.src.collectors.assets',
    'compute.src.collectors.bridge',
    'compute.src.collectors.custody',
    'compute.src.collectors.census --verify',
    'compute.src.collectors.metadata --verify',
    'compute.src.collectors.inventory --verify',
    'compute.src.collectors.report verify',
    'compute.src.collectors.indexed verify',
    'compute.src.collectors.checkpoint verify',
    'compute.src.collectors.checkpoint verify --priority-2000',
    'compute.src.collectors.funding derive',
    'compute.src.collectors.headers --scope stable',
    'compute.src.collectors.headers --scope acquisition',
    'compute.src.activity.withdrawals',
    'compute.src.activity.accounts',
    'compute.src.activity.transfers',
    'compute.src.activity.destinations',
    'compute.src.treasury.outflows.trace',
    'compute.src.treasury.outflows.analyze',
    'compute.src.treasury.outflows.rewards --bridge-only',
    'compute.src.treasury.outflows.follow',
    'compute.src.treasury.controls',
    'compute.src.treasury.inflows --verify',
    'compute.src.treasury.intermediary --verify',
    'compute.src.treasury.largest --verify',
    'compute.src.treasury.funding --verify',
    'compute.src.treasury.recurring --limit 200 --verify',
    'compute.src.build.results',
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay(module, args, audit_file):
    data_roots = (ROOT / 'data', ROOT / 'public/data')
    read, written = set(), set()

    def audit(event, details):
        if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto', 'urllib.Request'):
            raise RuntimeError(f'Network access in offline study {module}')
        if event == 'subprocess.Popen':
            program, argv = details[:2]
            expected = CODE_ROOT / 'compute/src/verify/signatures.mjs'
            if Path(program).name != 'node' or len(argv) != 2 or Path(argv[1]).resolve() != expected:
                raise RuntimeError(f'Unexpected subprocess in offline study {module}: {argv!r}')
        if event != 'open' or not isinstance(details[0], (str, bytes)):
            return
        path = Path(os.fsdecode(details[0])).absolute()
        if path.suffix in ('.py', '.pyc') or not any(path.is_relative_to(base) for base in data_roots):
            return
        files = written if details[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT) else read
        files.add(path.relative_to(ROOT).as_posix())

    sys.addaudithook(audit)
    sys.argv = [module, *args]
    runpy.run_module(module, run_name='__main__')
    audit_file.write_text(json.dumps({'read': sorted(read), 'written': sorted(written)}))


def check_identities(root):
    def load(name):
        return json.loads((root / VERIFIED / name).read_text())
    withdrawals = load('withdrawal-rpc-2026-11-02/summary.json')
    conversions = load('withdrawal-rpc-2026-11-02/conversion-summary.json')
    times = load('withdrawal-rpc-2026-11-02/receipt-block-times.json')
    assert withdrawals['directlyVerified'] == withdrawals['indexedCandidates'] == 794
    assert not withdrawals['missingRPCTransactions'] and not withdrawals['rpcMismatches']
    assert conversions['uniqueSignedIntents'] == conversions['signedWithdrawals'] == withdrawals['directlyVerified']
    assert times['resolvedBothHeaders'] == times['differentHashes'] == withdrawals['differentIndexedAndRpcBlockHashes']
    assert times['windowDisagreements'] == 0
    first_day = load('collector-history-2026-11-02/first-day-wnear-bridge.json')
    acquisition = load('collector-history-2026-11-02/acquisition-summary.json')['cohorts']
    first_receipts = sum(r['receivingReceipts'] for r in first_day['rows'])
    assert all(int(r['unexplainedNetRaw']) == 0 for r in first_day['rows'])
    assert first_receipts == acquisition['firstDay']['receiptVerifiedReceipts'] == 574
    assert acquisition['distinctUnion']['awaitingCanonicalReceipts'] == 0
    funding = load('collector-history-2026-11-02/stable-funding-summary.json')
    bridge, trace = funding['bridge'], funding['chronologicalTrace']
    assert bridge['tokenDecimals'] == 6 and bridge['totalCreditEvents'] == funding['independentlyVerifiedPayerSignatures'] == 252
    assert bridge['openingRaw'] + bridge['grossCreditRaw'] - bridge['grossDebitRaw'] == bridge['closingRaw']
    assert trace['whollySwapFundedGrossUSDTSpendRaw'] == 1_505_360_736
    assert int(trace['identifiedSwapFundedNativeDeliveryRaw']) == 747_307_949_764_020_935_708_713_660
    assert sum(int(row['identifiedSwapFundedDeliveryRaw']) for row in trace['payments']) == int(trace['identifiedSwapFundedNativeDeliveryRaw'])
    cohort = load('paid-activity-2026-11-02/summary.json')
    assert cohort['candidateAccounts'] == cohort['baselineVerifiedAccounts'] + cohort['candidatesWithoutMatchingFee'] == 186
    assert cohort['verifiedInBothFollowupWindows'] + cohort['noQualifyingIndexEventInEitherWindow'] == cohort['baselineVerifiedAccounts'] == 183
    assert all(row['unknown'] == 0 and row['verified-repeat'] == cohort['verifiedInBothFollowupWindows'] for row in cohort['cohortOutcomes'].values())
    native = load('treasury-outflows-2026-11-02/gas-rewards-bridge.json')['reconciliation']
    assert int(native['measuredGroupChangeYocto']) == int(native['indexedExternalTransfersYocto']) + int(native['adjustmentsYocto'])
    assert int(native['unexplainedYocto']) == 0
    article = load('onchain-results-2026-11-02.json')
    history = json.loads(gzip.decompress((root / TREASURY / 'account-history.json.gz').read_bytes()))
    hashes = [row['transaction_hash'] for wallet in history.values() for row in wallet['rows']]
    assert len(hashes) == 6484 and len(set(hashes)) == article['treasury']['distinctIndexedTransactions'] == 6481
    proposals = load('treasury-outflows-2026-11-02/summary.json')['verifiedFrontendTransferProposals']
    paid = [p for p in proposals if p['statusAtClosingBlock'] == 'Approved' and p['successfulTransfer']]
    assert article['treasury']['verifiedFrontendTransferProposals'] == len(paid) == 15
    origins = load('treasury-inflow-origins-2026-11-02/page-summary.json')
    manifest = load('treasury-inflow-origins-2026-11-02/release-sources.json')
    assert origins['archivalRPCRows'] + origins['indexedTreeRows'] == len(manifest['transactions']) == origins['frozenReceipts'] == 2578
    for name, expected in article['sourceDigests'].items():
        assert digest(root / VERIFIED / name) == expected, f'Article source digest mismatch: {name}'
    return article['sourceDigests']

def main(show_digests):
    with TemporaryDirectory(prefix='intent-thesis-verify-') as directory:
        stage = Path(directory)
        extract(stage)
        ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '*.tmp', '*.part')
        shutil.copytree(CODE_ROOT / 'compute', stage / 'compute', ignore=ignore)
        dependencies = CODE_ROOT / 'node_modules'
        if dependencies.is_dir():
            (stage / 'node_modules').symlink_to(dependencies, target_is_directory=True)
        frozen_roots = ('data', 'public/data')
        baseline = {p.relative_to(stage).as_posix(): digest(p)
                    for name in frozen_roots for p in (stage / name).rglob('*') if p.is_file()}
        public_before = {name: value for name, value in baseline.items()
                         if name.startswith('public/data/') and name.endswith('.csv')}
        read, written, supporting_files = set(), set(), set()
        audit_file = stage / 'audit.json'
        environment = dict(os.environ, THESIS_EVIDENCE_ROOT=str(stage))
        for study in STUDIES:
            module, *args = study.split()
            command = [sys.executable, '-B', '-m', 'compute.src.verify.studies',
                       '--replay', str(audit_file), module, *args]
            result = subprocess.run(command, cwd=stage, env=environment,
                                    capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f'{study} failed\n{result.stdout[-1500:]}\n{result.stderr}')
            seen = json.loads(audit_file.read_text())
            read.update(seen['read'])
            written.update(seen['written'])
            if module != 'compute.src.build.results':
                supporting_files.update(seen['read'])
                supporting_files.update(seen['written'])
            print(f'{study} passed', flush=True)
        article_sources = check_identities(stage)
        for name in article_sources:
            assert (VERIFIED / name).as_posix() in supporting_files, f'Article source lacks a separate offline study: {name}'
        for name in sorted(written):
            if name not in baseline or not (stage / name).is_file() or digest(stage / name) != baseline[name]:
                raise ValueError(f'Reconstructed artifact differs from the frozen bytes: {name}')
        files = {name: digest(stage / name) for name in sorted(read | written) if (stage / name).is_file()}
        published = {name for name in files if name.startswith('public/data/') and name.endswith('.csv')}
        for name in sorted(published):
            assert files[name] == public_before[name], f'Published CSV changed during replay: {name}'
        print(json.dumps({'studies': len(STUDIES), 'sourceAndExhibitFilesRead': len(read & files.keys()),
                          'rewrittenArtifactsCompared': len(written), 'publishedCsvsHashChecked': len(published),
                          'articleSourceDigests': article_sources,
                          **({'files': files} if show_digests else {})}, indent=2))


if __name__ == '__main__':
    if len(sys.argv) >= 4 and sys.argv[1] == '--replay':
        replay(sys.argv[3], sys.argv[4:], Path(sys.argv[2]))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--digests', action='store_true', help='print hashes for evidence and exhibit files read during replay')
        main(parser.parse_args().digests)
