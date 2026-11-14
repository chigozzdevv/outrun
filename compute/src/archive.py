"""Open a data-only working copy of the preserved evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
from tempfile import TemporaryDirectory
from zipfile import ZipFile

PROJECT = Path(__file__).resolve().parents[2]
ARCHIVE = PROJECT / 'data/evidence/2026-11-02/sources.zip'
MANIFEST = PROJECT / 'data/evidence/2026-11-02/manifest.json'
# The archive exceeds GitHub's file limit, so it is attached to this release instead of the repository.
RELEASE_TAG = 'evidence-2026-11-02'


def manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive() -> dict:
    record = manifest()
    if record.get('schemaVersion') != 2:
        raise ValueError('Unsupported evidence manifest')
    if any(not name.startswith(('data/', 'public/data/')) or name.endswith(('.py', '.pyc', '.ts', '.tsx', '.css', '.html')) for name in record['members']):
        raise ValueError('Evidence bundle contains application code')
    if not ARCHIVE.is_file():
        raise FileNotFoundError(f'Evidence archive is missing: {ARCHIVE}. Run `npm run evidence:download` first.')
    if file_digest(ARCHIVE) != record['sha256']:
        raise ValueError('Evidence archive does not match its recorded SHA-256.')
    with ZipFile(ARCHIVE) as bundle:
        names = [entry.filename for entry in bundle.infolist() if not entry.is_dir()]
        if len(names) != len(set(names)) or set(names) != set(record['members']):
            raise ValueError('Evidence archive members differ from its manifest.')
    return record


def extract(destination: Path | str) -> Path:
    """Extract and hash-check original names and bytes in an isolated directory."""
    record = verify_archive()
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with ZipFile(ARCHIVE) as bundle:
        for name, expected in record['members'].items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError(f'Unsafe archive member: {name}')
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with bundle.open(name) as source, target.open('wb') as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected:
                raise ValueError(f'Evidence member changed: {name}')
    return destination


def materialize() -> Path:
    """Reuse an evidence workspace; the full replay uses an isolated copy."""
    work = PROJECT / '.cache/evidence'
    marker = work / '.archive.json'
    expected = manifest()['sha256']
    if marker.is_file() and json.loads(marker.read_text()).get('sha256') == expected:
        return work
    if work.exists() and any(work.iterdir()):
        raise ValueError('Evidence cache belongs to another edition; clear this cache before rebuilding.')
    cache = PROJECT / '.cache'
    cache.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix='compute-', dir=cache) as temporary:
        staged = extract(Path(temporary) / 'work')
        (staged / '.archive.json').write_text(json.dumps({'sha256': expected}) + '\n')
        if work.exists():
            work.rmdir()
        shutil.move(staged, work)
    return work


def release_url() -> str:
    """Locate the release asset from THESIS_EVIDENCE_URL or the GitHub clone's origin."""
    import os
    import re
    import subprocess
    configured = os.environ.get('THESIS_EVIDENCE_URL')
    if configured:
        return configured
    try:
        origin = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=PROJECT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        origin = ''
    match = re.search(r'github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$', origin)
    if not match:
        raise ValueError('Set THESIS_EVIDENCE_URL to the sources.zip release asset, or run this command in a clone of the GitHub repository.')
    return f'https://github.com/{match[1]}/{match[2]}/releases/download/{RELEASE_TAG}/sources.zip'


def download() -> Path:
    """Fetch the evidence archive and keep it only when it matches the recorded SHA-256."""
    import os
    from urllib.request import Request, urlopen
    expected = manifest()['sha256']
    if ARCHIVE.is_file() and file_digest(ARCHIVE) == expected:
        return ARCHIVE
    url = release_url()
    partial = ARCHIVE.with_name(ARCHIVE.name + '.part')
    print(f'Downloading {url}', flush=True)
    try:
        with urlopen(Request(url, headers={'User-Agent': 'intent-thesis-evidence'}), timeout=60) as response, partial.open('wb') as output:
            shutil.copyfileobj(response, output, 1024 * 1024)
        if file_digest(partial) != expected:
            raise ValueError('Downloaded evidence archive does not match its recorded SHA-256.')
        os.replace(partial, ARCHIVE)
    finally:
        partial.unlink(missing_ok=True)
    return ARCHIVE


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['--download']:
        print(f'Evidence archive verified: {download().relative_to(PROJECT)}')
    else:
        print(f'Evidence workspace ready: {materialize()}')
