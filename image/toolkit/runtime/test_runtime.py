import asyncio
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
import zipfile

from PIL import Image

from browser import Browser
from common import OUTPUTS, input_file, run
from diagrams import render
from media import Edit, probe, transform
from providers import analyze_video, image_generate, speech, transcribe


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        OUTPUTS.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='.toolkit-test-', dir=OUTPUTS)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def video(self):
        path = self.root / 'source.mp4'
        run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', path])
        return path

    def test_rejected_paths_outputs_and_invalid_media_have_no_result(self):
        with self.assertRaises(ValueError):
            input_file('/etc/passwd')
        symlink = self.root / 'outside'
        symlink.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            input_file(str(symlink))
        source = self.root / 'bad.mp4'
        source.write_text('invalid video')
        target = self.root / 'result.mp4'
        with self.assertRaises(ValueError):
            transform(Edit(action='clip', input_path=str(source), output_path=str(target)))
        self.assertFalse(target.exists())
        target.write_text('preserve existing')
        with self.assertRaises(ValueError):
            transform(Edit(action='clip', input_path=str(source), output_path=str(target)))
        self.assertEqual(target.read_text(), 'preserve existing')
        with self.assertRaises(ValueError):
            Edit(action='clip', input_path=str(source), output_path=str(target), width=321)

    def test_timeout_stops_spawned_process(self):
        import subprocess
        import sys
        pid_path = self.root / 'child.pid'
        code = 'import subprocess,time,pathlib; p=subprocess.Popen(["sleep","60"]);pathlib.Path(' + repr(str(pid_path)) + ').write_text(str(p.pid));time.sleep(60)'
        with self.assertRaises(subprocess.TimeoutExpired):
            run([sys.executable, '-c', code], timeout=0.3)
        status = Path('/proc') / pid_path.read_text() / 'status'
        if status.exists():
            self.assertIn('State:\tZ', status.read_text())

    def test_real_media_edits_and_subtitle_render(self):
        source = self.video()
        self.assertEqual(len(probe(str(source))['streams']), 2)
        caption = self.root / 'caption.srt'
        caption.write_text('1\n00:00:00,000 --> 00:00:02,500\n中文媒体工具验证\n')
        jobs = [('clip', 'clip.mp4', {'start': 0.5, 'duration': 1, 'width': 320}), ('transcode', 'small.webm', {'width': 320, 'crf': 35}), ('audio', 'audio.wav', {}), ('frames', 'frames.zip', {'fps': 1, 'max_frames': 2}), ('gif', 'preview.gif', {'fps': 5, 'duration': 1, 'width': 320}), ('subtitle', 'captioned.mp4', {'subtitle_path': str(caption)}), ('concat', 'joined.mp4', {'additional_paths': [str(source)]})]
        for action, name, options in jobs:
            with self.subTest(action=action):
                result = transform(Edit(action=action, input_path=str(source), output_path=str(self.root / name), **options))
                self.assertGreater(result['bytes'], 0)
        clip = probe(str(self.root / 'clip.mp4'))
        self.assertAlmostEqual(float(clip['format']['duration']), 1, delta=0.15)
        self.assertEqual(next(s['width'] for s in clip['streams'] if s['codec_type'] == 'video'), 320)
        joined = probe(str(self.root / 'joined.mp4'))
        self.assertAlmostEqual(float(joined['format']['duration']), 6, delta=0.2)
        self.assertTrue(any(s['codec_type'] == 'audio' for s in joined['streams']))
        with zipfile.ZipFile(self.root / 'frames.zip') as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(json.loads(archive.read('frames.json'))['count'], 2)
        mixed = transform(Edit(action='mix', input_path=str(self.root / 'audio.wav'), output_path=str(self.root / 'mixed.wav'), additional_paths=[str(self.root / 'audio.wav')]))
        self.assertGreater(mixed['bytes'], 1000)

    def test_cpu_transcription_word_timestamps_and_srt(self):
        audio = self.root / 'speech.wav'
        speech('Hello world. This is a speech recognition test.', str(audio))
        result = transcribe(str(audio), str(self.root / 'transcript.json'), language='en', word_timestamps=True, subtitle_path=str(self.root / 'transcript.srt'), text_path=str(self.root / 'transcript.txt'))
        self.assertIn('test', result['text'].lower())
        data = json.loads((self.root / 'transcript.json').read_text())
        self.assertTrue(data['segments'])
        self.assertEqual((self.root / 'transcript.txt').read_text(), data['text'])
        self.assertTrue(any(s.get('words') for s in data['segments']))
        self.assertIn('-->', (self.root / 'transcript.srt').read_text())

    @patch.dict(os.environ)
    def test_five_renderers_produce_real_files(self):
        os.environ.pop('PLAYWRIGHT_BROWSERS_PATH', None)
        examples = [('graphviz', '.dot', 'digraph G { a [label="创建"]; b [label="交付"]; a -> b; }', '.png'), ('mermaid', '.mmd', 'flowchart LR\nA[创建] --> B[交付]', '.png'), ('d2', '.d2', 'a: 创建\nb: 交付\na -> b', '.png'), ('plantuml', '.puml', '@startuml\nAlice -> Bob: 中文验证\n@enduml', '.png'), ('typst', '.typ', '#set text(font: "Noto Sans CJK SC")\n= 中文排版验证\n工具已生成可读文件。', '.pdf')]
        for engine, suffix, content, extension in examples:
            with self.subTest(engine=engine):
                source = self.root / (engine + suffix)
                source.write_text(content)
                result = render(engine, str(source), str(self.root / (engine + extension)))
                self.assertGreater(result['bytes'], 100)
                if extension == '.png':
                    with Image.open(result['file_path']) as image:
                        self.assertGreater(image.width, 50)
        text = run(['pdftotext', self.root / 'typst.pdf', '-']).decode()
        self.assertIn('中文排版验证', text)

    @patch.dict(os.environ)
    def test_browser_real_form_upload_download_and_cleanup(self):
        os.environ.pop('PLAYWRIGHT_BROWSERS_PATH', None)
        fixture = self.root / 'page.html'
        fixture.write_text('<!doctype html><meta charset="utf-8"><title>浏览器验证</title><input id="name"><button id="go" onclick="document.querySelector(\'#result\').textContent=\'完成:\'+document.querySelector(\'#name\').value">提交</button><p id="result"></p><input id="file" type="file"><a id="download" download="hello.txt" href="data:text/plain,hello">下载</a><script>console.log("browser-ready")</script>')
        upload = self.root / 'upload.txt'
        upload.write_text('authorized local file')

        async def exercise():
            browser = Browser()
            try:
                with self.assertRaises(ValueError):
                    await browser.navigate('file:///etc/passwd')
                state = await browser.open_file(str(fixture))
                self.assertEqual(state['title'], '浏览器验证')
                await browser.action('fill', '#name', 'OMA')
                state = await browser.action('click', '#go')
                self.assertIn('完成:OMA', state['text'])
                await browser.upload('#file', str(upload))
                selected = await browser.evaluate('document.querySelector("#file").files[0].name')
                self.assertEqual(selected['result'], 'upload.txt')
                await browser.screenshot(str(self.root / 'browser.png'))
                await browser.download('#download', str(self.root / 'download.txt'))
                self.assertEqual((self.root / 'download.txt').read_text(), 'hello')
                self.assertTrue(any(m['text'] == 'browser-ready' for m in browser.messages))
            finally:
                await browser.shutdown()
            self.assertIsNone(browser.browser)

        asyncio.run(exercise())

    def test_provider_contracts_and_missing_credentials(self):
        with patch.dict(os.environ, {'OMA_TOOLS_CONFIG': str(self.root / 'missing.json')}):
            with self.assertRaisesRegex(ValueError, 'not configured'):
                image_generate('test image', str(self.root / 'missing.png'))
        source = self.video()
        audio = self.root / 'response.wav'
        speech('This is a provider protocol fixture.', str(audio))
        buffer = io.BytesIO()
        Image.new('RGB', (64, 64), '#14B8A6').save(buffer, format='PNG')
        png = base64.b64encode(buffer.getvalue()).decode()
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(handler):
                body = handler.rfile.read(int(handler.headers['Content-Length']))
                requests.append((handler.path, body, handler.headers.get('Authorization')))
                handler.send_response(200)
                handler.end_headers()
                if handler.path.endswith('audio/speech'):
                    handler.wfile.write(audio.read_bytes())
                else:
                    data = {'data': [{'b64_json': png}]} if '/images/' in handler.path else {'choices': [{'message': {'content': 'Contract fixture result'}}]} if '/chat/' in handler.path else {'text': 'fixture transcript', 'language': 'en', 'segments': [{'start': 0, 'end': 1, 'text': 'fixture transcript'}]}
                    handler.wfile.write(json.dumps(data).encode())

            def log_message(handler, format, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = self.root / 'providers.json'
            config.write_text(json.dumps({name: {'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'fixture-model', 'api_key_env': 'OMA_FIXTURE_SECRET'} for name in ['asr', 'speech', 'image', 'vision']}))
            with patch.dict(os.environ, {'OMA_TOOLS_CONFIG': str(config), 'OMA_FIXTURE_SECRET': 'fixture-secret'}):
                image_generate('fixture prompt', str(self.root / 'generated.png'))
                image_generate('edit fixture', str(self.root / 'edited.png'), input_path=str(self.root / 'generated.png'))
                speech('fixture speech', str(self.root / 'remote.wav'), engine='remote', voice='fixture-voice')
                transcribe(str(audio), str(self.root / 'remote.json'), engine='remote')
                analyze_video(str(source), 'fixture video', str(self.root / 'analysis.json'), sample_count=2)
            self.assertEqual(len(requests), 5)
            self.assertTrue(all(r[2] == 'Bearer fixture-secret' for r in requests))
            self.assertIn(b'timestamp_granularities', requests[3][1])
            vision = json.loads(requests[4][1])
            self.assertEqual(sum(c['type'] == 'image_url' for c in vision['messages'][0]['content']), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_browser_http_navigation(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler):
                handler.send_response(200)
                handler.send_header('Content-Type', 'text/html; charset=utf-8')
                handler.end_headers()
                handler.wfile.write('<!doctype html><title>本地网页验证</title><h1>HTTP 导航成功</h1>'.encode())

            def log_message(handler, format, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        async def exercise():
            browser = Browser()
            try:
                result = await browser.navigate(f'http://127.0.0.1:{server.server_port}/')
                self.assertEqual(result['title'], '本地网页验证')
                self.assertIn('HTTP 导航成功', result['text'])
            finally:
                await browser.shutdown()

        try:
            asyncio.run(exercise())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
