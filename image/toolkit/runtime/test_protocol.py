import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from common import OUTPUTS


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.errors = tempfile.TemporaryFile()
        environment = {key: value for key, value in os.environ.items() if key != 'PLAYWRIGHT_BROWSERS_PATH'}
        cls.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('server.py'))], stdout=cls.errors, stderr=cls.errors, env=environment)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen('http://127.0.0.1:8766/health', timeout=1):
                    return
            except OSError:
                if cls.process.poll() is not None:
                    cls.errors.seek(0)
                    raise RuntimeError('Workbench startup failed: ' + cls.errors.read().decode()[-2000:])
                time.sleep(0.05)
        cls.process.terminate()
        cls.process.wait(timeout=10)
        raise TimeoutError('Workbench readiness timed out')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=15)
        cls.errors.close()

    def rpc(self, method, parameters=None, host=None):
        request = urllib.request.Request('http://127.0.0.1:8766/mcp', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': parameters or {}}).encode(), headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
        if host:
            request.add_header('Host', host)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)['result']

    def call(self, name, arguments):
        return self.rpc('tools/call', {'name': name, 'arguments': arguments})

    def test_invalid_host_and_local_path_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.rpc('tools/list', host='untrusted.invalid')
        self.assertEqual(result.exception.code, 421)
        result = self.call('media_probe', {'input_path': '/etc/passwd'})
        self.assertTrue(result['isError'])
        self.assertIn('/mnt/user-data', result['content'][0]['text'])

    def test_catalog_and_missing_provider(self):
        initialized = self.rpc('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'toolkit-protocol', 'version': '1'}})
        self.assertEqual(initialized['serverInfo']['name'], 'workbench')
        tools = {t['name'] for t in self.rpc('tools/list')['tools']}
        self.assertEqual(len(tools), 19)
        self.assertTrue({'media_transform', 'speech_transcribe', 'video_analyze', 'browser_upload', 'browser_tabs', 'diagram_render'}.issubset(tools))
        status = self.call('tools_status', {})['structuredContent']
        self.assertTrue(status['local_tools']['ffmpeg'])
        self.assertTrue(status['local_asr_model'])
        self.assertEqual(status['browser'], {'state': 'available', 'check': 'launched_and_closed'})
        self.assertIn('readable', status['office_skill'])
        self.assertEqual(status['providers']['vision'], 'not_configured')
        missing = self.call('video_analyze', {'input_path': '/etc/passwd', 'prompt': 'test', 'output_path': '/mnt/user-data/outputs/missing-analysis.json'})
        self.assertTrue(missing['isError'])
        self.assertIn('not configured', missing['content'][0]['text'])

    def test_browser_and_missing_provider(self):
        OUTPUTS.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.toolkit-protocol-', dir=OUTPUTS) as directory:
            root = Path(directory)
            source = root / 'page.html'
            source.write_text('<!doctype html><meta charset="utf-8"><title>协议验证</title><h1>真实浏览器</h1>')
            opened = self.call('browser_open_file', {'input_path': str(source)})
            self.assertEqual(opened['structuredContent']['title'], '协议验证')
            image = root / 'page.png'
            screenshot = self.call('browser_screenshot', {'output_path': str(image)})
            self.assertFalse(screenshot.get('isError', False), screenshot)
            self.assertTrue(image.read_bytes().startswith(b'\x89PNG'))
            self.call('browser_tabs', {'operation': 'new'})
            tabs = self.call('browser_tabs', {'operation': 'list'})['structuredContent']['tabs']
            self.assertEqual(len(tabs), 2)
            self.call('browser_tabs', {'operation': 'switch', 'index': 1})
            viewed = self.call('browser_view', {})
            self.assertIn('真实浏览器', viewed['structuredContent']['text'])
            missing = self.call('video_analyze', {'input_path': str(source), 'prompt': 'test', 'output_path': str(root / 'analysis.json')})
            self.assertTrue(missing['isError'])
            self.assertIn('not configured', missing['content'][0]['text'])
            self.assertFalse((root / 'analysis.json').exists())
            self.assertTrue(self.call('browser_close', {})['structuredContent']['closed'])


if __name__ == '__main__':
    unittest.main()
