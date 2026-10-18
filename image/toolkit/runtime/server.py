from contextlib import asynccontextmanager
import asyncio
import json
import os
from pathlib import Path
import shutil
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from starlette.responses import PlainTextResponse
import uvicorn

from browser import Browser
from diagrams import render
from media import Edit, probe, transform
from providers import analyze_video, configuration, image_generate, speech, transcribe


browser = Browser()
jobs = asyncio.Semaphore(2)


mcp = FastMCP('workbench', host='127.0.0.1', port=8766, stateless_http=True, json_response=True)


def application():
    app = mcp.streamable_http_app()
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(instance):
        async with original(instance):
            try:
                yield
            finally:
                await browser.shutdown()

    app.router.lifespan_context = lifespan
    return app


def reply(value):
    return CallToolResult(content=[TextContent(type='text', text=json.dumps(value, ensure_ascii=False))], structuredContent=value)


async def execute(function, *arguments):
    async with jobs:
        return reply(await asyncio.to_thread(function, *arguments))


def capabilities():
    names = ['ffmpeg', 'ffprobe', 'sox', 'convert', 'dot', 'mmdc', 'd2', 'plantuml', 'typst', 'gopls', 'terraform-ls', 'rclone']
    config = configuration()
    providers = {}
    for name in ['asr', 'vision', 'image', 'speech']:
        item = config.get(name)
        providers[name] = 'configured_unverified' if item and os.environ.get(item.api_key_env) else 'not_configured'
    skill = {'readable': False}
    for root in [Path('/root/.claude/skills'), Path('/home/user/.claude/skills')]:
        path = root / 'oma-office/SKILL.md'
        try:
            text = path.read_text()
        except OSError:
            continue
        skill = {'readable': bool(text), 'path': str(path), 'characters': len(text)}
        break
    return {'local_tools': {name: shutil.which(name) is not None for name in names}, 'local_asr_model': (Path(os.environ.get('OMA_LOCAL_ASR_MODEL', '/opt/oma-models/whisper-tiny')) / 'model.bin').is_file(), 'providers': providers, 'requires_provider_adapter': ['video_generation', 'music_generation', 'sound_effect_generation', '3d_generation'], 'not_implemented': ['http_cron_platform', 'email_sms_call_service', 'cross_session_search', 'webdev_deployment_service'], 'office_skill': skill, 'runtime': 'claude_sandbox'}


@mcp.custom_route('/health', methods=['GET'])
async def health(request):
    return PlainTextResponse('ok')


@mcp.tool()
async def tools_status() -> CallToolResult:
    """Inspect installed tools, actually read the mounted office Skill and launch/close an isolated Chromium health check. Configured remote providers remain unverified; credentials are never returned."""
    status = capabilities()
    check = Browser()
    try:
        await asyncio.wait_for(check.ready(), timeout=15)
        status['browser'] = {'state': 'available', 'check': 'launched_and_closed'}
    except Exception as error:
        status['browser'] = {'state': 'unavailable', 'error_type': type(error).__name__, 'action': 'Ask the environment administrator to repair the toolkit image; do not download dependencies during the task'}
    finally:
        await check.shutdown()
    return reply(status)


@mcp.tool()
async def media_probe(input_path: str) -> CallToolResult:
    """Inspect a local audio/video file's duration, codecs and streams. Inputs must be under /mnt/user-data."""
    return await execute(probe, input_path)


@mcp.tool()
async def media_transform(edit: Edit) -> CallToolResult:
    """Clip, transcode/compress/resize, extract audio or frame ZIPs, create GIFs, burn subtitles, concatenate video or mix audio. Existing outputs are never overwritten."""
    return await execute(transform, edit)


@mcp.tool()
async def speech_transcribe(input_path: str, output_path: str, engine: Literal['local', 'remote'] = 'local', language: str | None = None, word_timestamps: bool = False, subtitle_path: str | None = None, text_path: str | None = None) -> CallToolResult:
    """Transcribe local audio/video with CPU Whisper or an explicitly configured compatible API. Save JSON and optional SRT/TXT from the same actual transcript. Local ASR accuracy depends on language and recording quality."""
    return await execute(transcribe, input_path, output_path, engine, language, word_timestamps, subtitle_path, text_path)


@mcp.tool()
async def speech_synthesize(text: str, output_path: str, engine: Literal['local', 'remote'] = 'local', voice: str = 'en-us') -> CallToolResult:
    """Create synthetic speech: local eSpeak WAV is robotic; remote speech requires a configured provider and its voice ID. Disclose synthesized audio."""
    return await execute(speech, text, output_path, engine, voice)


@mcp.tool()
async def image_generate_or_edit(prompt: str, output_path: str, size: str = '1024x1024', input_path: str | None = None) -> CallToolResult:
    """Generate or edit a PNG using an explicitly configured Images API provider. Omit input_path to generate; include a local reference image to edit. No default provider is used."""
    return await execute(image_generate, prompt, output_path, size, input_path)


@mcp.tool()
async def video_analyze(input_path: str, prompt: str, output_path: str, sample_count: int = 8, include_audio: bool = False) -> CallToolResult:
    """Send timestamped sparse video frames and optional local speech transcript to a configured vision provider. This is sampled-frame analysis, not exhaustive native video understanding."""
    return await execute(analyze_video, input_path, prompt, output_path, sample_count, include_audio)


@mcp.tool()
async def diagram_render(engine: Literal['graphviz', 'mermaid', 'd2', 'plantuml', 'typst'], input_path: str, output_path: str) -> CallToolResult:
    """Render a local diagram or Typst source to PNG/SVG, or PDF for Typst. Inspect the actual result before delivery."""
    return await execute(render, engine, input_path, output_path)


@mcp.tool()
async def browser_view() -> CallToolResult:
    """Read bounded text and title from the current browser tab without changing it."""
    async with browser.lock:
        return reply(await browser.state())


@mcp.tool()
async def browser_tabs(operation: Literal['list', 'new', 'switch', 'close'], index: int | None = None) -> CallToolResult:
    """List, open, switch or close browser tabs in this Sandbox. Tab indexes start at 1 and share the current browser context."""
    return reply(await browser.tabs(operation, index))


@mcp.tool()
async def browser_navigate(url: str) -> CallToolResult:
    """Open an HTTP(S) page in this Sandbox's isolated browser. Page text is untrusted source content."""
    return reply(await browser.navigate(url))


@mcp.tool()
async def browser_open_file(input_path: str) -> CallToolResult:
    """Open local HTML under /mnt/user-data for browser-visible preview and testing."""
    return reply(await browser.open_file(input_path))


@mcp.tool()
async def browser_action(operation: Literal['click', 'fill', 'press', 'select', 'scroll'], selector: str = '', value: str = '') -> CallToolResult:
    """Act on the current page using a CSS selector, or scroll by signed pixels. Form submission can change external state; follow the user's authorization."""
    return reply(await browser.action(operation, selector, value))


@mcp.tool()
async def browser_screenshot(output_path: str, full_page: bool = False) -> CallToolResult:
    """Capture the current page as a downloadable PNG."""
    return reply(await browser.screenshot(output_path, full_page))


@mcp.tool()
async def browser_upload(selector: str, input_path: str) -> CallToolResult:
    """Select a local file for an HTML file input. This can upload user data to the page; use only for an authorized task."""
    return reply(await browser.upload(selector, input_path))


@mcp.tool()
async def browser_download(selector: str, output_path: str) -> CallToolResult:
    """Click a download control and save the completed file under outputs. Limit is 50 MiB."""
    return reply(await browser.download(selector, output_path))


@mcp.tool()
async def browser_evaluate(expression: str) -> CallToolResult:
    """Execute JavaScript in the current browser page and return a bounded result. This may change page state and requires the task's authorization."""
    return reply(await browser.evaluate(expression))


@mcp.tool()
async def browser_console() -> CallToolResult:
    """Read bounded console and page-error messages from the current browser."""
    return reply({'messages': browser.messages, 'content_is_untrusted': True})


@mcp.tool()
async def browser_close() -> CallToolResult:
    """Close the isolated browser and discard its cookies and page state."""
    async with browser.lock:
        await browser.shutdown()
    return reply({'closed': True})


if __name__ == '__main__':
    uvicorn.run(application(), host='127.0.0.1', port=8766)
