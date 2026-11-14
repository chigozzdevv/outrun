"""Dataset identities, coverage, units and reproduction metadata for this edition."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path, PurePosixPath
import re

PROJECT = Path(__file__).resolve().parents[2]
CATALOG = PROJECT / 'data/evidence/2026-11-02/catalog.json'


def safe_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('A dataset path must be a string')
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in ('..', '.') for part in value.split('/')) or '\\' in value:
        raise ValueError(f'Unsafe dataset path: {value}')
    return value


def validate_catalog(record: dict) -> dict:
    if record.get('schemaVersion') != 1 or not isinstance(record.get('datasets'), list) or not record['datasets']:
        raise ValueError('Unsupported dataset catalogue')
    identities, sources, outputs, urls = set(), set(), set(), set()
    for item in record['datasets']:
        name = item.get('id')
        if not isinstance(name, str) or not name or name in identities:
            raise ValueError(f'Duplicate or missing dataset identity: {name}')
        identities.add(name)
        source, output = safe_path(item['source']), safe_path(item['path'])
        if source in sources or output in outputs:
            raise ValueError(f'Duplicate dataset source or publication: {name}')
        if not source.startswith(('data/', 'public/data/')) or not output.startswith(('data/evidence/', 'web/public/data/')):
            raise ValueError(f'Unsupported dataset location: {name}')
        sources.add(source); outputs.add(output)
        if not re.fullmatch(r'[a-f0-9]{64}', item.get('sha256', '')):
            raise ValueError(f'Missing dataset digest: {name}')
        for key in ('kind', 'units'):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f'Missing {key}: {name}')
        if not item.get('coverage', {}).get('kind') or not item['coverage'].get('description'):
            raise ValueError(f'Missing temporal coverage: {name}')
        if not item.get('reproduction', {}).get('command') or item['reproduction'].get('mode') not in ('derive', 'verify', 'copy'):
            raise ValueError(f'Missing reproduction method: {name}')
        schema = item.get('schema', {})
        if schema.get('format') not in ('csv', 'json'):
            raise ValueError(f'Missing dataset format: {name}')
        if schema['format'] == 'csv':
            columns = schema.get('columns', [])
            if not columns or len(columns) != len(set(columns)) or not isinstance(schema.get('rows'), int) or schema['rows'] < 0:
                raise ValueError(f'Invalid CSV schema: {name}')
            if not set(schema.get('primaryKey', [])).issubset(columns):
                raise ValueError(f'Unknown CSV primary-key column: {name}')
        download = item.get('download')
        if download is not None:
            url = download.get('url', '')
            if url in urls or output != 'web/public' + url or not url.startswith('/data/') or '..' in url:
                raise ValueError(f'Invalid or duplicate download URL: {name}')
            if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+){0,2}\.csv', download.get('filename', '')):
                raise ValueError(f'Invalid download filename: {name}')
            if not download.get('title') or not isinstance(download.get('listed'), bool):
                raise ValueError(f'Missing download presentation: {name}')
            urls.add(url)
    for item in record['datasets']:
        for relation in item.get('relationships', []):
            if relation.get('dataset') not in identities or relation['dataset'] == item['id']:
                raise ValueError(f'Unknown related dataset: {item["id"]}')
            if relation.get('type') not in ('receipt-key-subset', 'receipt-key-overlap', 'earlier-edition'):
                raise ValueError(f'Unknown dataset relationship: {item["id"]}')
    return record


def load_catalog() -> dict:
    return validate_catalog(json.loads(CATALOG.read_text()))


def validate_content(item: dict, content: bytes) -> list[dict] | None:
    """Check structure without converting exact decimal or atomic strings."""
    schema = item['schema']
    if schema['format'] == 'json':
        json.loads(content)
        return None
    reader = csv.DictReader(io.StringIO(content.decode('utf-8'), newline=''))
    rows = list(reader)
    if reader.fieldnames != schema['columns'] or len(rows) != schema['rows']:
        raise ValueError(f'CSV columns or row count differ from catalogue: {item["id"]}')
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f'Malformed CSV row: {item["id"]}')
    key = schema.get('primaryKey', [])
    if key:
        identities = [tuple(row[column] for column in key) for row in rows]
        if any(not all(identity) for identity in identities) or len(identities) != len(set(identities)):
            raise ValueError(f'Duplicate or empty CSV primary key: {item["id"]}')
    return rows


def validate_relationships(record: dict, rows: dict[str, list[dict]]) -> None:
    datasets = {item['id']: item for item in record['datasets']}
    for item in record['datasets']:
        for relation in item.get('relationships', []):
            if relation['type'] == 'earlier-edition':
                continue
            target = datasets[relation['dataset']]
            left = {tuple(row[column] for column in item['population']['key']) for row in rows[item['id']]}
            right = {tuple(row[column] for column in target['population']['key']) for row in rows[target['id']]}
            if len(left & right) != relation['sharedKeys']:
                raise ValueError(f'Receipt overlap differs from catalogue: {item["id"]}')
            if relation['type'] == 'receipt-key-subset' and not left.issubset(right):
                raise ValueError(f'Receipt population is not the declared subset: {item["id"]}')
