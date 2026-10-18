import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('server.py'))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=1):
                    return
            except OSError:
                if cls.process.poll() is not None:
                    raise RuntimeError('MCP server exited before readiness')
                time.sleep(0.05)
        cls.process.terminate()
        cls.process.wait(timeout=10)
        raise TimeoutError('MCP server did not become ready')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)

    def rpc(self, method, params=None, host=None):
        request = urllib.request.Request('http://127.0.0.1:8765/mcp', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}).encode(), headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
        if host:
            request.add_header('Host', host)
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def test_invalid_host_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.rpc('tools/list', host='untrusted.invalid')
        self.assertEqual(result.exception.code, 421)

    def test_initialize_catalog_and_real_tool_errors(self):
        initialized = self.rpc('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'office-test', 'version': '1'}})
        self.assertEqual(initialized['result']['serverInfo']['name'], 'slides')
        tools = self.rpc('tools/list')['result']['tools']
        self.assertEqual({tool['name'] for tool in tools}, {'get_template', 'init_slides', 'organize_slides', 'write_notes', 'present', 'export', 'attach_slides'})
        invalid = self.rpc('tools/call', {'name': 'get_template', 'arguments': {'template_id': 'unknown'}})
        self.assertTrue(invalid['result']['isError'])
        created = self.rpc('tools/call', {'name': 'init_slides', 'arguments': {'title': '协议验收', 'generate_mode': 'pptx', 'outline': [{'id': 'first', 'title': '第一页', 'summary': '协议测试'}]}})
        self.assertFalse(created['result'].get('isError', False))
        project = created['result']['structuredContent']
        pending = self.rpc('tools/call', {'name': 'present', 'arguments': {'project_id': project['project_id']}})
        self.assertTrue(pending['result']['isError'])
        self.assertIn('edit_pending', pending['result']['content'][0]['text'])


if __name__ == '__main__':
    unittest.main()
