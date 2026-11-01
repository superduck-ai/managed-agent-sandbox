import importlib.metadata
import json
import os
import shlex
from pathlib import Path
import subprocess
import tempfile
import wave

from faster_whisper import WhisperModel
import numpy as np
from playwright.sync_api import sync_playwright

from install import contract, digest


def verify_shells():
    probe = """from playwright.sync_api import sync_playwright
from faster_whisper import WhisperModel
with sync_playwright() as driver:
    browser = driver.chromium.launch(headless=True, args=['--no-sandbox'])
    browser.close()
model = WhisperModel('/opt/oma-models/whisper-small', device='cpu', compute_type='int8', cpu_threads=2, local_files_only=True)
"""
    login = """import os
assert os.environ.get('PLAYWRIGHT_BROWSERS_PATH') == '/opt/oma-tools-browsers', 'login shell lost Chromium path'
assert os.environ.get('OMA_LOCAL_ASR_MODEL') == '/opt/oma-models/whisper-small', 'login shell lost Whisper path'
"""
    for account, home in [('root', '/root'), ('user', '/home/user'), ('claude', '/home/claude')]:
        for flags in ['-c', '-lc']:
            script = (login if flags == '-lc' else '') + probe
            result = subprocess.run(['sudo', '-n', '-H', '-u', account, 'env', '-i', 'HOME=' + home, 'PATH=/home/user/.local/share/oma/python/bin:/usr/local/bin:/usr/bin:/bin', '/bin/bash', flags, 'python3 -c ' + shlex.quote(script)], capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise RuntimeError(f'{account} {flags} resources failed: {result.stderr[-2000:]}')
    print('Sanitized non-login/login-shell Chromium and offline Whisper passed for root, user and claude')


def verify():
    verify_shells()
    values = contract('/etc/oma-sandbox-versions.env')
    for package, key in [('playwright', 'PLAYWRIGHT_VERSION'), ('faster-whisper', 'FASTER_WHISPER_VERSION'), ('requests', 'REQUESTS_VERSION'), ('av', 'AV_VERSION')]:
        if importlib.metadata.version(package) != values[key]:
            raise ValueError(f'Unexpected {package} version')
    registry = Path(__import__('playwright').__file__).parent / 'driver/package/browsers.json'
    browsers = json.loads(registry.read_text())['browsers']
    browser = next(item for item in browsers if item['name'] == 'chromium-headless-shell')
    if browser['revision'] != values['CHROMIUM_REVISION']:
        raise ValueError('Playwright and Chromium revision mismatch')
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as driver:
        instance = driver.chromium.launch(headless=True, args=['--no-sandbox'])
        try:
            page = instance.new_page()
            page.route('**/*', lambda route: route.abort())
            page.set_content("""<form><input id="name"><button type="button" onclick="document.querySelector('output').textContent=document.querySelector('input').value">确认</button></form><output></output>""")
            page.locator('#name').fill('沙箱浏览器验证')
            page.get_by_role('button').click()
            if page.locator('output').inner_text() != '沙箱浏览器验证':
                raise ValueError('Browser form result mismatch')
            image = Path(directory) / 'browser.png'
            page.screenshot(path=str(image))
            if image.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
                raise ValueError('Browser screenshot missing')
        finally:
            instance.close()
    model_path = Path(os.environ['OMA_LOCAL_ASR_MODEL'])
    for name, key in [('model.bin', 'MODEL'), ('config.json', 'CONFIG'), ('tokenizer.json', 'TOKENIZER'), ('vocabulary.txt', 'VOCABULARY'), ('README.md', 'README')]:
        if digest(model_path / name) != values['WHISPER_' + key + '_SHA256']:
            raise ValueError(f'Whisper checksum mismatch: {name}')
    model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=2, local_files_only=True)
    with tempfile.TemporaryDirectory() as directory:
        audio = Path(directory) / 'silence.wav'
        with wave.open(str(audio), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(np.zeros(16000, dtype='<i2').tobytes())
        segments, _ = model.transcribe(str(audio), language='zh', beam_size=1, vad_filter=False, condition_on_previous_text=False)
        list(segments)
    print('Chromium form/screenshot and offline Whisper inference passed')


if __name__ == '__main__':
    verify()
