import base64
import asyncio
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

import httpx
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from common import input_file, output_file, publish, run, staging, write_json
from media import ffmpeg, probe, probe_source


class Provider(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    base_url: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=255)
    api_key_env: str = Field(pattern=r'^[A-Z][A-Z0-9_]{0,100}$')
    response_format: str | None = None


def configuration():
    path = Path(os.environ.get('OMA_TOOLS_CONFIG', '/root/.secrets/oma-tools.json'))
    if not path.is_file():
        return {}
    if path.stat().st_size > 64 << 10:
        raise ValueError('Provider configuration is too large')
    try:
        return TypeAdapter(dict[str, Provider]).validate_json(path.read_text())
    except ValueError:
        raise ValueError('Invalid provider configuration; expected named base_url, model and api_key_env entries') from None


def provider(name):
    item = configuration().get(name)
    if item is None:
        raise ValueError(f'{name} provider is not configured; set its base_url, model and api_key_env in OMA_TOOLS_CONFIG')
    parsed = urlsplit(item.base_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'})):
        raise ValueError('Provider requires HTTPS, or loopback HTTP for local inference')
    key = os.environ.get(item.api_key_env)
    if not key:
        raise ValueError(f'{name} provider secret environment variable is missing')
    return item, {'Authorization': 'Bearer ' + key}


def request(name, endpoint, *, body=None, data=None, files=None, binary=False):
    item, headers = provider(name)

    async def execute():
        async with asyncio.timeout(180):
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), follow_redirects=False, trust_env=False) as client:
                async with client.stream('POST', item.base_url.rstrip('/') + '/' + endpoint, headers=headers, json=body, data=data, files=files) as response:
                    if response.status_code >= 300:
                        raise ValueError(f'{name} provider returned HTTP {response.status_code}; check model access and configuration')
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > (64 << 20):
                            raise ValueError('Provider response exceeds 64 MiB')
                        chunks.append(chunk)
                    content = b''.join(chunks)
                    return content if binary else json.loads(content)

    try:
        return asyncio.run(execute())
    except httpx.HTTPError:
        raise ValueError(f'{name} provider transport failed; check connection and timeout') from None
    except TimeoutError:
        raise ValueError(f'{name} provider exceeded the 180 second deadline') from None


def stamp(seconds):
    value = round(seconds * 1000)
    return f'{value // 3600000:02}:{value // 60000 % 60:02}:{value // 1000 % 60:02},{value % 1000:03}'


def validate_transcript(value):
    if not isinstance(value, dict) or not isinstance(value.get('text'), str):
        raise ValueError('Transcription response must contain text')
    rows = value.get('segments', [])
    if not isinstance(rows, list) or len(rows) > 20000:
        raise ValueError('Invalid transcription segments')
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('text'), str):
            raise ValueError('Invalid transcription segment')
        start, end = row.get('start'), row.get('end')
        if any(type(x) not in {int, float} or not math.isfinite(x) for x in [start, end]) or not 0 <= start <= end <= 1801:
            raise ValueError('Invalid transcription timestamp')
    return {'text': value['text'], 'language': value.get('language'), 'segments': rows, **({'words': value['words']} if isinstance(value.get('words'), list) else {})}


def transcribe(input_path, output_path, engine='local', language=None, word_timestamps=False, subtitle_path=None, text_path=None):
    source = input_file(input_path)
    destination = output_file(output_path, {'.json'})
    subtitle = output_file(subtitle_path, {'.srt'}) if subtitle_path else None
    plain = output_file(text_path, {'.txt'}) if text_path else None
    if language is not None and not re.fullmatch(r'[a-z]{2,3}', language):
        raise ValueError('language must be an ISO language code such as zh or en')
    if engine not in {'local', 'remote'}:
        raise ValueError('engine must be local or remote')
    if engine == 'remote':
        item, _ = provider('asr')
    else:
        model = Path(os.environ.get('OMA_LOCAL_ASR_MODEL', '/opt/oma-models/whisper-tiny'))
        if not (model / 'model.bin').is_file():
            raise ValueError('Local ASR model is missing; install the pinned model or configure OMA_LOCAL_ASR_MODEL')
    duration = float(probe(str(source)).get('format', {}).get('duration', 0))
    if not 0 < duration <= 1800:
        raise ValueError('Transcription requires audio/video between 0 and 1800 seconds')
    with tempfile.TemporaryDirectory(prefix='oma-transcribe-') as directory:
        work = Path(directory)
        audio = work / 'audio.wav'
        run([*ffmpeg([source]), '-vn', '-ac', '1', '-ar', '16000', audio])
        if engine == 'local':
            result = work / 'result.json'
            command = [sys.executable, Path(__file__).with_name('transcription_worker.py'), audio, result, '--model', model]
            if language:
                command.extend(['--language', language])
            if word_timestamps:
                command.append('--words')
            run(command, timeout=300)
            data = json.loads(result.read_text())
        else:
            if audio.stat().st_size > 25 << 20:
                raise ValueError('Remote transcription audio exceeds 25 MiB; split the recording first')
            fields = {'model': item.model, 'response_format': 'verbose_json'}
            if language:
                fields['language'] = language
            fields['timestamp_granularities[]'] = 'word' if word_timestamps else 'segment'
            with audio.open('rb') as stream:
                data = request('asr', 'audio/transcriptions', data=fields, files={'file': ('audio.wav', stream, 'audio/wav')})
        value = {**validate_transcript(data), 'engine': engine, 'source': str(source)}
        if subtitle and not value['segments']:
            raise ValueError('Provider returned no segment timestamps; SRT cannot be generated')
        published = []
        try:
            result = write_json(value, destination)
            published.append(destination)
            if plain:
                with staging(plain) as temporary:
                    temporary.write_text(value['text'], encoding='utf-8')
                    publish(temporary, plain)
                    published.append(plain)
                result['text_path'] = str(plain)
            if subtitle:
                with staging(subtitle) as temporary:
                    temporary.write_text('\n\n'.join(f'{i + 1}\n{stamp(s["start"])} --> {stamp(s["end"])}\n{s["text"].strip()}' for i, s in enumerate(value['segments'])) + '\n')
                    publish(temporary, subtitle)
                    published.append(subtitle)
                result['subtitle_path'] = str(subtitle)
            return {**result, 'engine': engine, 'text': value['text'], 'segments': len(value['segments'])}
        except BaseException:
            for path in published:
                path.unlink(missing_ok=True)
            raise


def speech(text, output_path, engine='local', voice='en-us'):
    if not 1 <= len(text) <= 10000:
        raise ValueError('Speech text requires 1-10000 characters')
    destination = output_file(output_path, {'.wav'} if engine == 'local' else {'.wav', '.mp3'})
    with staging(destination) as temporary:
        if engine == 'local':
            if not re.fullmatch(r'[a-zA-Z0-9_+-]{1,50}', voice):
                raise ValueError('Invalid eSpeak voice name')
            text_path = temporary.with_suffix('.txt')
            text_path.write_text(text)
            run(['espeak-ng', '-v', voice, '-s', '145', '-f', text_path, '-w', temporary], timeout=30)
        elif engine == 'remote':
            item, _ = provider('speech')
            content = request('speech', 'audio/speech', body={'model': item.model, 'input': text, 'voice': voice, 'response_format': destination.suffix[1:]}, binary=True)
            temporary.write_bytes(content)
        else:
            raise ValueError('engine must be local or remote')
        probe_source(temporary)
        return {**publish(temporary, destination), 'engine': engine, 'disclosure': 'Synthetic speech; not a recording of a human speaker'}


def image_generate(prompt, output_path, size='1024x1024', input_path=None):
    if not 1 <= len(prompt) <= 10000 or not re.fullmatch(r'\d{3,4}x\d{3,4}', size):
        raise ValueError('Provide a prompt and explicit image dimensions')
    item, _ = provider('image')
    destination = output_file(output_path, {'.png'})
    fields = {'model': item.model, 'prompt': prompt, 'size': size, 'n': 1}
    if item.response_format:
        fields['response_format'] = item.response_format
    if input_path:
        source = input_file(input_path, 10 << 20)
        with source.open('rb') as stream:
            result = request('image', 'images/edits', data=fields, files={'image': (source.name, stream, 'image/png')})
    else:
        result = request('image', 'images/generations', body=fields)
    rows = result.get('data', [])
    if not rows or not isinstance(rows[0].get('b64_json'), str):
        raise ValueError('Image provider must return data[0].b64_json; URL-only responses are not supported')
    data = base64.b64decode(rows[0]['b64_json'], validate=True)
    with Image.open(io.BytesIO(data)) as image, staging(destination) as temporary:
        image.convert('RGB').save(temporary, format='PNG')
        return publish(temporary, destination)


def analyze_video(input_path, prompt, output_path, sample_count=8, include_audio=False):
    item, _ = provider('vision')
    source = input_file(input_path)
    destination = output_file(output_path, {'.json'})
    if not 1 <= sample_count <= 16 or not 1 <= len(prompt) <= 10000:
        raise ValueError('Use 1-16 frames and a prompt of 1-10000 characters')
    duration = float(probe(str(source)).get('format', {}).get('duration', 0))
    if not 0 < duration <= 1800:
        raise ValueError('Video duration must be between 0 and 1800 seconds')
    with tempfile.TemporaryDirectory(prefix='oma-video-analysis-') as directory:
        work = Path(directory)
        content = [{'type': 'text', 'text': prompt + '\nFrames are sparse samples, not the complete motion. Identify uncertainty and cite the provided timestamps.'}]
        times = []
        for index in range(sample_count):
            second = duration * (index + 0.5) / sample_count
            times.append(second)
            frame = work / f'{index}.jpg'
            run([*ffmpeg([source]), '-ss', str(second), '-frames:v', '1', '-vf', 'scale=640:-2', frame], timeout=30)
            content.extend([{'type': 'text', 'text': f'Frame at {second:.3f}s'}, {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(frame.read_bytes()).decode()}}])
        if include_audio:
            transcript_path = destination.with_name(destination.stem + '-transcript.json')
            transcript = transcribe(str(source), str(transcript_path))
            content.append({'type': 'text', 'text': 'Local speech transcript: ' + transcript['text']})
        result = request('vision', 'chat/completions', body={'model': item.model, 'messages': [{'role': 'user', 'content': content}]})
        try:
            text = result['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            raise ValueError('Vision provider returned an invalid chat completion') from None
        if not isinstance(text, str):
            raise ValueError('Vision response content must be text')
        return {**write_json({'analysis': text, 'source': str(source), 'sample_times_seconds': times, 'audio_included': include_audio, 'method': 'sampled-frames'}, destination), 'analysis': text}
