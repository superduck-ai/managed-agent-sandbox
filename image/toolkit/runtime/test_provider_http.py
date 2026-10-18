import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from common import OUTPUTS, input_file, publish, staging
from providers import configuration, image_generate, request


class ProviderHTTPTests(unittest.TestCase):
    def setUp(self):
        OUTPUTS.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix='.provider-http-', dir=OUTPUTS)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_path_and_exclusive_publish_boundaries(self):
        with self.assertRaises(ValueError):
            input_file('/etc/passwd')
        path = self.root / 'output.txt'
        with staging(path) as temporary:
            temporary.write_text('first')
            publish(temporary, path)
        with staging(path) as temporary:
            temporary.write_text('second')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                publish(temporary, path)
        self.assertEqual(path.read_text(), 'first')

    def test_invalid_config_redacts_unknown_secret_field(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps({'image': {'api_key': 'must-not-appear-in-error'}}))
        with patch.dict(os.environ, {'OMA_TOOLS_CONFIG': str(config)}):
            with self.assertRaises(ValueError) as error:
                configuration()
        self.assertNotIn('must-not-appear', str(error.exception))

    def test_real_http_multipart_image_and_error_contracts(self):
        buffer = io.BytesIO()
        Image.new('RGB', (64, 64), '#14B8A6').save(buffer, format='PNG')
        encoded = base64.b64encode(buffer.getvalue()).decode()
        calls = []
        fail = [False]

        class Handler(BaseHTTPRequestHandler):
            def do_POST(handler):
                body = handler.rfile.read(int(handler.headers['Content-Length']))
                calls.append((handler.path, body, handler.headers.get('Authorization')))
                if fail[0]:
                    handler.send_response(401)
                    handler.end_headers()
                    handler.wfile.write(b'provider echoed must-not-appear-in-error')
                    return
                handler.send_response(200)
                handler.end_headers()
                if '/images/' in handler.path:
                    value = {'data': [{'b64_json': encoded}]}
                elif '/audio/transcriptions' in handler.path:
                    value = {'text': 'fixture text', 'segments': []}
                else:
                    value = {'choices': [{'message': {'content': 'fixture visual result'}}]}
                handler.wfile.write(json.dumps(value).encode())

            def log_message(handler, format, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = self.root / 'config.json'
            config.write_text(json.dumps({name: {'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'fixture-model', 'api_key_env': 'OMA_HTTP_FIXTURE_KEY'} for name in ['image', 'asr', 'vision']}))
            with patch.dict(os.environ, {'OMA_TOOLS_CONFIG': str(config), 'OMA_HTTP_FIXTURE_KEY': 'fixture-key'}):
                output = self.root / 'image.png'
                image_generate('a fixture image', str(output))
                with Image.open(output) as image:
                    self.assertEqual(image.size, (64, 64))
                image_generate('edit fixture', str(self.root / 'edited.png'), input_path=str(output))
                result = request('asr', 'audio/transcriptions', data={'model': 'fixture-model', 'response_format': 'verbose_json'}, files={'file': ('audio.wav', b'fixture file bytes', 'audio/wav')})
                self.assertEqual(result['text'], 'fixture text')
                self.assertIn(b'filename="audio.wav"', calls[-1][1])
                self.assertIn(b'verbose_json', calls[-1][1])
                self.assertEqual(calls[0][2], 'Bearer fixture-key')
                self.assertEqual(json.loads(calls[0][1])['prompt'], 'a fixture image')
                fail[0] = True
                with self.assertRaises(ValueError) as error:
                    image_generate('failure', str(self.root / 'failed.png'))
                self.assertIn('HTTP 401', str(error.exception))
                self.assertNotIn('must-not-appear', str(error.exception))
                self.assertFalse((self.root / 'failed.png').exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
