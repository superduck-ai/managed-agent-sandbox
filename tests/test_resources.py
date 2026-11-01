import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resources_install', ROOT / 'image/resources/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ResourceTests(unittest.TestCase):
    def test_corrupt_download_preserves_existing_file_and_removes_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'corrupt model')
            target = root / 'assets/model.bin'
            target.parent.mkdir()
            target.write_bytes(b'existing model')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                installer.download(source.as_uri(), target, hashlib.sha256(b'valid model').hexdigest())
            self.assertEqual(target.read_bytes(), b'existing model')
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_verified_cache_needs_no_network(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'model.bin'
            target.write_bytes(b'verified model')
            with patch.object(installer.urllib.request, 'urlopen', side_effect=AssertionError('network used')):
                installer.download('https://invalid.example/model', target, installer.digest(target))

    def test_resource_contract_for_both_architectures(self):
        for machine, suffix in [('x86_64', 'AMD64'), ('aarch64', 'ARM64')]:
            with self.subTest(machine=machine), patch.object(installer.os, 'uname') as uname:
                uname.return_value.machine = machine
                values = installer.contract(ROOT / 'versions.env')
                self.assertEqual(values['CHROMIUM_URL'], values['CHROMIUM_URL_' + suffix])
                self.assertIn('/' + values['CHROMIUM_REVISION'] + '/', values['CHROMIUM_URL'])
                for name in ['CHROMIUM_SHA256', 'WHISPER_MODEL_SHA256', 'WHISPER_CONFIG_SHA256', 'WHISPER_TOKENIZER_SHA256', 'WHISPER_VOCABULARY_SHA256', 'WHISPER_README_SHA256']:
                    self.assertRegex(values[name], r'^[0-9a-f]{64}$')
                for prefix in ['GH', 'D2', 'TYPST']:
                    self.assertRegex(values[prefix + '_SHA256'], r'^[0-9a-f]{64}$')
                    self.assertIn(values[prefix + '_VERSION'], values[prefix + '_URL'])

    def test_browser_caches_share_resources_without_overwriting_an_existing_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            browser = root / 'browsers'
            name = 'chromium_headless_shell-1194'
            (browser / name).mkdir(parents=True)
            homes = {}
            for account in ['root', 'user', 'claude']:
                home = root / account
                home.mkdir()
                homes[account] = SimpleNamespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
            values = {'CHROMIUM_REVISION': '1194'}
            with patch.object(installer.pwd, 'getpwnam', side_effect=homes.__getitem__):
                installer.register_browser_caches(browser, values)
                installer.register_browser_caches(browser, values)
                for home in homes.values():
                    link = Path(home.pw_dir) / '.cache/ms-playwright' / name
                    self.assertTrue(link.is_symlink())
                    self.assertEqual(link.resolve(), browser / name)
                existing = Path(homes['root'].pw_dir) / '.cache/ms-playwright' / name
                existing.unlink()
                existing.mkdir()
                (existing / 'marker').write_text('existing browser')
                with self.assertRaises(FileExistsError):
                    installer.register_browser_caches(browser, values)
                self.assertEqual((existing / 'marker').read_text(), 'existing browser')



if __name__ == '__main__':
    unittest.main()
