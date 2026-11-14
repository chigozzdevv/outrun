"""Publish the catalogue's frozen datasets only after every input is verified."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import os
from tempfile import NamedTemporaryFile
from zipfile import ZipFile

from compute.src.archive import ARCHIVE, PROJECT, verify_archive
from compute.src.catalog import load_catalog, validate_content, validate_relationships


def verify_exports() -> int:
    record = verify_archive()
    catalogue = load_catalog()
    rows = {}
    for item in catalogue['datasets']:
        target = PROJECT / item['path']
        if not target.is_file():
            raise ValueError(f'Missing publication: {item["path"]}')
        content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != item['sha256']:
            raise ValueError(f'Publication differs from its evidence: {item["path"]}')
        if record['members'].get(item['source']) != item['sha256']:
            raise ValueError(f'Publication source is not recorded: {item["source"]}')
        parsed = validate_content(item, content)
        if parsed is not None:
            rows[item['id']] = parsed
    validate_relationships(catalogue, rows)
    return len(catalogue['datasets'])


def publish(from_workspace: bool = False) -> int:
    record = verify_archive()
    catalogue = load_catalog()
    if from_workspace:
        from compute.src.paths import ROOT
    ready, rows = {}, {}
    with ZipFile(ARCHIVE) as bundle:
        for item in catalogue['datasets']:
            if record['members'].get(item['source']) != item['sha256']:
                raise ValueError(f'Publication source is not recorded: {item["source"]}')
            content = (ROOT / item['source']).read_bytes() if from_workspace else bundle.read(item['source'])
            if hashlib.sha256(content).hexdigest() != item['sha256']:
                raise ValueError(f'Rebuilt publication differs from the frozen edition: {item["source"]}')
            parsed = validate_content(item, content)
            if parsed is not None:
                rows[item['id']] = parsed
            ready[item['path']] = content
    validate_relationships(catalogue, rows)
    # No destination is touched until every dataset and relationship has passed.
    for relative, content in ready.items():
        target = PROJECT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(dir=target.parent, prefix='.' + target.name, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return verify_exports()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--restore', action='store_true')
    mode.add_argument('--from-workspace', action='store_true')
    args = parser.parse_args()
    count = publish(args.from_workspace) if args.restore or args.from_workspace else verify_exports()
    print(f'{count} datasets match their evidence, schemas and recorded relationships.')
