"""Publication contracts: exact quantities, safe paths and related populations."""
from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from compute.src.catalog import validate_catalog, validate_relationships
from compute.src import publish
from compute.src.archive import verify_archive


def item(name, body):
    return {
        'id': name, 'source': f'data/{name}.csv', 'path': f'web/public/data/{name}.csv',
        'sha256': hashlib.sha256(body).hexdigest(), 'kind': 'derived-exhibit',
        'coverage': {'kind': 'pinned-window', 'description': 'Known test records'},
        'units': 'NEAR decimal strings', 'reproduction': {'command': 'test fixture', 'mode': 'derive'},
        'download': None,
        'schema': {'format': 'csv', 'columns': ['receipt', 'amount'], 'rows': 1, 'primaryKey': ['receipt']},
    }


class CatalogueTests(unittest.TestCase):
    def test_bundle_contains_evidence_and_no_old_application_copy(self):
        record = verify_archive()
        self.assertTrue(record['members'])
        self.assertFalse(any(name.startswith(('src/', 'scripts/', 'server/', 'tests/')) for name in record['members']))
        self.assertTrue(all(name.startswith(('data/', 'public/data/')) for name in record['members']))
        self.assertFalse(any(name.endswith(('.py', '.pyc', '.ts', '.tsx', '.css', '.html')) for name in record['members']))

    def test_catalogue_rejects_duplicate_identity_and_escaping_paths(self):
        row = item('one', b'receipt,amount\nr1,1\n')
        catalogue = {'schemaVersion': 1, 'datasets': [row]}
        validate_catalog(catalogue)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            validate_catalog({'schemaVersion': 1, 'datasets': [row, deepcopy(row)]})
        for invalid in ('../outside.csv', '/outside.csv', 'web/../outside.csv'):
            bad = deepcopy(catalogue); bad['datasets'][0]['path'] = invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'Unsafe'):
                validate_catalog(bad)

    def test_publication_validates_all_inputs_before_writing_any_output(self):
        first = b'receipt,amount\nr1,1\n'; second = b'receipt,amount\nr2,2\n'
        entries = [item('one', first), item('two', second)]
        entries[1]['schema']['columns'] = ['wrong', 'headers']
        with TemporaryDirectory() as folder:
            root = Path(folder); bundle = root / 'evidence.zip'
            with ZipFile(bundle, 'w') as archive:
                archive.writestr(entries[0]['source'], first)
                archive.writestr(entries[1]['source'], second)
            prior = root / entries[0]['path']; prior.parent.mkdir(parents=True); prior.write_bytes(b'previous publication')
            manifest = {'members': {entry['source']: entry['sha256'] for entry in entries}}
            with patch.object(publish, 'PROJECT', root), patch.object(publish, 'ARCHIVE', bundle), patch.object(publish, 'verify_archive', return_value=manifest), patch.object(publish, 'load_catalog', return_value={'datasets': entries}):
                with self.assertRaisesRegex(ValueError, 'CSV columns'):
                    publish.publish()
            self.assertEqual(prior.read_bytes(), b'previous publication')
            self.assertFalse((root / entries[1]['path']).exists())

    def test_publication_preserves_atomic_and_decimal_strings_byte_for_byte(self):
        body = b'receipt,amount\nr1,0.000000000000000000000001\n'
        entry = item('exact', body)
        with TemporaryDirectory() as folder:
            root = Path(folder); bundle = root / 'evidence.zip'
            with ZipFile(bundle, 'w') as archive:
                archive.writestr(entry['source'], body)
            manifest = {'members': {entry['source']: entry['sha256']}}
            with patch.object(publish, 'PROJECT', root), patch.object(publish, 'ARCHIVE', bundle), patch.object(publish, 'verify_archive', return_value=manifest), patch.object(publish, 'load_catalog', return_value={'datasets': [entry]}):
                self.assertEqual(publish.publish(), 1)
            self.assertEqual((root / entry['path']).read_bytes(), body)

    def test_subset_relationship_checks_identity_not_only_overlap_count(self):
        left = {'id': 'left', 'population': {'key': ['receipt']}, 'relationships': [{'type': 'receipt-key-subset', 'dataset': 'right', 'sharedKeys': 1}]}
        right = {'id': 'right', 'population': {'key': ['receipt']}}
        rows = {'left': [{'receipt': 'r1'}, {'receipt': 'r2'}], 'right': [{'receipt': 'r1'}, {'receipt': 'r3'}]}
        with self.assertRaisesRegex(ValueError, 'not the declared subset'):
            validate_relationships({'datasets': [left, right]}, rows)
