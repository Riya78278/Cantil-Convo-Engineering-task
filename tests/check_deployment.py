"""Hosting configuration tests without private data or external accounts."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.runtime import configure


class Deployment(unittest.TestCase):
    def test_local_configuration_is_preserved(self):
        with patch.dict(os.environ, {'PGDATABASE': 'local', 'PGHOST': '/tmp/local'}, clear=True):
            configure()
            self.assertEqual(os.environ['PGDATABASE'], 'local')
            self.assertEqual(os.environ['PGHOST'], '/tmp/local')
            self.assertEqual(os.environ['PGCONNECT_TIMEOUT'], '10')

    def test_hosted_url_stays_in_environment(self):
        url = 'postgresql://example:syn%40thetic@localhost:5433/example?sslmode=require&channel_binding=require'
        with patch.dict(os.environ, {'DATABASE_URL': url, 'PGCONNECT_TIMEOUT': '20'}, clear=True):
            configure()
            self.assertEqual(os.environ['PGDATABASE'], 'example')
            self.assertEqual(os.environ['PGHOST'], 'localhost')
            self.assertEqual(os.environ['PGPORT'], '5433')
            self.assertEqual(os.environ['PGPASSWORD'], 'syn@thetic')
            self.assertEqual(os.environ['PGSSLMODE'], 'require')
            self.assertEqual(os.environ['PGCHANNELBINDING'], 'require')
            self.assertEqual(os.environ['PGCONNECT_TIMEOUT'], '20')

    def test_rejects_invalid_database_url(self):
        for value in ['https://example/db','postgresql:///db','postgresql://example/',
                      'postgresql://example/db?unsupported=yes']:
            with patch.dict(os.environ, {'DATABASE_URL':value},clear=True), self.assertRaises(ValueError):
                configure()

    def test_invalid_port_fails_before_database_setup(self):
        env = dict(os.environ, PORT='70000')
        result = subprocess.run([sys.executable, '-B', '-m', 'scripts.start'], cwd=ROOT,
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PORT must be between', result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
