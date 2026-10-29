import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'tools/check_local_readiness.py'


class ReadinessTests(unittest.TestCase):
    def test_manifest_contains_versions_not_credentials(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'backend/app').mkdir(parents=True);(p/'frontend').mkdir();(p/'config').mkdir()
            (p/'backend/app/daily_api.py').write_text('# fixture')
            (p/'frontend/package.json').write_text('{}')
            canary='test-private-canary-not-to-be-exported'
            (p/'config/auth.local.json').write_text(json.dumps({'password':canary}))
            r=subprocess.run([sys.executable,str(SCRIPT),'--project',str(p)],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            report=list((p/'docs').glob('environment_manifest_*.json'));self.assertEqual(len(report),1)
            content=report[0].read_text();self.assertNotIn(canary,content+r.stdout+r.stderr)
            self.assertTrue(json.loads(content)['local_auth_config_exists'])
            self.assertEqual(json.loads((p/'config/auth.local.json').read_text())['password'],canary)

    def test_wrong_project_does_not_create_report(self):
        with tempfile.TemporaryDirectory() as t:
            r=subprocess.run([sys.executable,str(SCRIPT),'--project',t],capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0);self.assertFalse((Path(t)/'docs').exists())
