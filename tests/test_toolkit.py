import ast
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
import zipfile
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLKIT = ROOT / 'image/toolkit'


class ToolkitSourceTests(unittest.TestCase):
    def test_all_python_sources_parse(self):
        for source in TOOLKIT.rglob('*.py'):
            with self.subTest(source=source.relative_to(ROOT)):
                ast.parse(source.read_text(encoding='utf-8'), filename=str(source))

    def test_dependency_files_pin_every_package(self):
        for name in ['office', 'runtime']:
            requirements = (TOOLKIT / name / 'requirements.txt').read_text().splitlines()
            self.assertTrue(requirements)
            for line in requirements:
                self.assertRegex(line, r'^[A-Za-z0-9_-]+==[0-9][A-Za-z0-9.]*$')

    def test_assets_cover_each_declared_architecture_and_pin_bytes(self):
        assets = json.loads((TOOLKIT / 'runtime/assets.json').read_text())
        versions = (ROOT / 'versions.env').read_text()
        platforms = re.search(r'^SANDBOX_PLATFORMS="([^"]+)"', versions, re.M).group(1).split()
        for platform in platforms:
            arch = platform.split('/')[1]
            self.assertEqual({item['binary'] for item in assets[arch]}, {'d2', 'typst', 'rclone', 'terraform-ls'})
            for item in assets[arch]:
                self.assertTrue(item['url'].startswith('https://'))
                self.assertRegex(item['sha256'], r'^[a-f0-9]{64}$')
        self.assertRegex(assets['whisper']['revision'], r'^[a-f0-9]{40}$')
        for item in assets['whisper']['files']:
            self.assertIn(assets['whisper']['revision'], item['url'])
            self.assertRegex(item['sha256'], r'^[a-f0-9]{64}$')

    def test_agent_example_has_local_services_and_26_tools(self):
        config = json.loads((TOOLKIT / 'runtime/agent-config.json').read_text())
        self.assertEqual({item['url'] for item in config['mcp_servers']}, {'http://127.0.0.1:8765/mcp', 'http://127.0.0.1:8766/mcp'})
        self.assertEqual(sum(len(item['configs']) for item in config['tools']), 26)

    def test_skill_package_is_complete_and_has_no_generated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([sys.executable, str(TOOLKIT / 'office/package.py'), directory], check=True, capture_output=True)
            archive = Path(directory) / 'oma-office.skill'
            expected = (Path(directory) / 'SHA256SUMS').read_text().split()[0]
            self.assertEqual(expected, hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertLess(archive.stat().st_size, 8 * 1024 * 1024)
            with zipfile.ZipFile(archive) as package:
                self.assertIsNone(package.testzip())
                self.assertIn('oma-office/SKILL.md', package.namelist())
                self.assertIn('oma-office/scripts/check_metadata.py', package.namelist())
                self.assertTrue(all(name.startswith('oma-office/') and '__pycache__' not in name for name in package.namelist()))


if __name__ == '__main__':
    unittest.main()
