import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'tools' / 'inspect_storage.py'
spec = importlib.util.spec_from_file_location('audit', SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / 'warehouse_inventory'
        self.storage = self.project / 'data' / 'warehouses'
        self.backups = self.project.parent / 'warehouse_inventory_backups'
        self.write(self.project / 'backend/app/database.py', b'# fixture')

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def report(self, storage=None):
        return audit.build_report(self.project, storage or self.storage, self.backups)

    def test_exact_counts_and_no_nested_double_counting(self):
        tenant = self.storage / ('a' * 32)
        self.write(tenant / 'captures/photo.jpg', b'12345')
        self.write(tenant / 'data/reviews/one.json', b'{}')
        self.write(tenant / 'data/warehouse.db', b'1234567')
        self.write(tenant / 'data/warehouse.db-wal', b'12')
        self.write(self.project / 'data/warehouse.db', b'legacy')
        self.write(self.project / 'captures/old.png', b'old')
        self.write(self.backups / 'full/one.wbackup', b'1234567890')
        before = {str(p): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        r = self.report()
        w = r['warehouses'][0]
        self.assertEqual(w['bytes'], 16)
        self.assertEqual(w['categories']['images']['file_count'], 1)
        self.assertEqual(w['categories']['reviews']['bytes'], 2)
        self.assertEqual(w['categories']['sqlite']['bytes'], 9)
        self.assertEqual(r['legacy']['data']['bytes'], 6)
        self.assertEqual(r['legacy']['captures']['bytes'], 3)
        self.assertEqual(r['backups']['bytes'], 10)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.project.rglob('*') if p.is_file()})

    def test_external_storage_not_mixed_with_legacy(self):
        external = self.project.parent / 'separate'
        self.write(external / ('b' * 32) / 'captures/b.jpg', b'xx')
        self.write(self.project / 'data/warehouse.db', b'old')
        r = self.report(external)
        self.assertEqual(r['warehouse_total_bytes'], 2)
        self.assertEqual(r['legacy']['data']['bytes'], 3)

    def test_empty_paths_are_explicit(self):
        r = self.report()
        self.assertEqual(r['warehouses'], [])
        self.assertFalse(r['backups']['exists'])
        self.assertFalse(r['evidence_references_checked'])
        self.assertFalse(r['quota_enforced'])

    def test_no_symlink_follow_or_recursive_cycle(self):
        tenant = self.storage / ('a' * 32)
        self.write(tenant / 'captures/image.jpg', b'xx')
        (tenant / 'loop').symlink_to(tenant, target_is_directory=True)
        r = self.report()
        self.assertEqual(r['warehouse_total_bytes'], 2)
        self.assertFalse(r['complete'])
        self.assertEqual(r['warehouses'][0]['warning_count'], 1)

    def test_unknown_tenant_entries_not_silently_counted(self):
        self.write(self.storage / 'not-a-warehouse/file.jpg', b'x')
        r = self.report()
        self.assertFalse(r['complete'])
        self.assertTrue(r['listing_warnings'])

    def test_windows_junction_attribute_detected(self):
        class Info:
            st_mode = 0o40755
            st_file_attributes = 0x400
        self.assertTrue(audit.unsafe(Info()))

    def test_non_regular_file_not_opened(self):
        tenant = self.storage / ('a' * 32)
        tenant.mkdir(parents=True)
        os.mkfifo(tenant / 'pipe')
        r = self.report()
        self.assertFalse(r['complete'])
        self.assertEqual(r['warehouses'][0]['file_count'], 0)

    def test_cli_custom_environment_and_reports_only(self):
        external = self.project.parent / 'external'
        self.write(external / ('a' * 32) / 'captures/one.png', b'abc')
        self.write(self.project / 'config/auth.local.json', b'PRIVATE_SECRET_DO_NOT_READ')
        env = dict(os.environ, WAREHOUSE_STORAGE_ROOT=str(external), PYTHONIOENCODING='utf-8')
        for _ in range(2):
            p = subprocess.run([sys.executable, str(SCRIPT), '--project', str(self.project)],
                               env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertNotIn('PRIVATE_SECRET', p.stdout)
        reports = list((self.project / 'docs').glob('storage_audit_*.json'))
        self.assertEqual(len(reports), 2)
        r = json.loads(reports[0].read_text())
        self.assertEqual(r['warehouse_total_bytes'], 3)
        self.assertNotIn('PRIVATE_SECRET', reports[0].read_text())

    def test_wrong_project_fails_without_creating_dirs(self):
        missing = self.project.parent / 'absent'
        with self.assertRaises(ValueError):
            audit.build_report(missing, self.storage, self.backups)
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
