import json
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import PlainTextResponse

from projects import DEFAULT_DESIGN, Slides


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Outline(Input):
    id: str = Field(pattern=r'^[a-z0-9_]{1,80}$')
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=1000)


class Note(Input):
    note: str = Field(max_length=20000)
    slide_id: str | None = None
    slide_number: int | None = Field(default=None, ge=1)


class Change(Input):
    slide_id: str | None = None
    slide_number: int | None = Field(default=None, ge=1)
    position: int | None = Field(default=None, ge=1)
    page_title: str | None = None
    summary: str | None = None


service = Slides(Path('/mnt/user-data/outputs/slides'))
mcp = FastMCP('slides', host='127.0.0.1', port=8765, stateless_http=True, json_response=True)


def reply(value):
    return CallToolResult(content=[TextContent(type='text', text=json.dumps(value, ensure_ascii=False))], structuredContent=value)


@mcp.custom_route('/health', methods=['GET'])
async def health(request):
    return PlainTextResponse('ok')


@mcp.tool()
def get_template(template_id: str) -> CallToolResult:
    """Get the installed local deck-stage-basic theme. Manus official templates are unavailable."""
    if template_id != 'deck-stage-basic':
        raise ValueError('Unknown local template. Only deck-stage-basic is installed.')
    return reply({'template_id': template_id, 'generate_mode': 'pptx', 'design_xml': DEFAULT_DESIGN, 'description': 'Editable 1280x720 dark navy and teal theme; compose pages with slide XML.'})


@mcp.tool()
def init_slides(title: str, outline: list[Outline], generate_mode: Literal['pptx'], template_id: str | None = None, deck_topic: str | None = None, style_request: str | None = None) -> CallToolResult:
    """Create an editable PPTX deck. Write design.xml and registered <id>.xml files using file tools, then write notes and present. No AI images or HTML mode."""
    result = service.initialize(title, [p.model_dump() for p in outline], generate_mode, template_id)
    if style_request:
        result['style_request'] = style_request
        result['instructions'] += ' Apply the requested style when authoring design.xml and slide XML.'
    return reply(result)


@mcp.tool()
def organize_slides(project_id: str, operation: Literal['add', 'delete', 'reorder'], slide_data: list[Change]) -> CallToolResult:
    """Add, delete or reorder slides. Add requires slide_id/page_title/summary. Reorder requires every active slide exactly once."""
    return reply(service.organize(project_id, operation, [p.model_dump(exclude_none=True) for p in slide_data]))


@mcp.tool()
def write_notes(project_id: str, notes: list[Note]) -> CallToolResult:
    """Write or clear speaker notes. Notes are stored in the exported PPTX after the next present."""
    return reply(service.notes(project_id, [p.model_dump(exclude_none=True) for p in notes]))


@mcp.tool()
def present(project_id: str, title: str | None = None) -> CallToolResult:
    """Reject missing/invalid pages, compile editable PPTX with notes, render PDF and PNG pages, and create an HTML preview. Re-present after any edit. May take up to several minutes."""
    return reply(service.present(project_id, title))


@mcp.tool()
def export(project_id: str, format: Literal['pdf', 'pptx'], version_id: str | None = None, output_dir: str | None = None) -> CallToolResult:
    """Return a completed version's downloadable file path. Usually omit output_dir; if supplied, it must be inside this project_dir. Current source must have been presented; historical version_id exports stay immutable."""
    return reply(service.export(project_id, format, version_id, output_dir))


@mcp.tool()
def attach_slides(resource_uri: str) -> CallToolResult:
    """Reopen an oma-slides://deck/<project_id> from this Session's persisted output files. Manus URIs and other Session resources are unavailable."""
    return reply(service.attach(resource_uri))


if __name__ == '__main__':
    mcp.run(transport='streamable-http')
