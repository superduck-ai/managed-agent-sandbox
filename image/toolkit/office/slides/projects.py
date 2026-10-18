from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import base64
import fcntl
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from pypdf import PdfReader

from rendering import compile_deck, preview


DEFAULT_DESIGN = '''<design><size width="1280" height="720"/>
<color name="primary" value="#14B8A6"/><color name="bg-page" value="#0F172A"/>
<color name="bg-card" value="#1E293B"/><color name="text-main" value="#FFFFFF"/>
<color name="text-muted" value="#94A3B8"/><color name="divider" value="#334155"/>
<font name="display" family="Noto Sans CJK SC" size="48"/>
<font name="heading" family="Noto Sans CJK SC" size="32"/>
<font name="body" family="Noto Sans CJK SC" size="20"/></design>'''


@dataclass
class Page:
    id: str
    title: str
    summary: str
    note: str = ''


@dataclass
class Deck:
    project_id: str
    title: str
    pages: list[Page]
    versions: dict[str, dict[str, str]] = field(default_factory=dict)


def slide_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9_]{1,80}', value) or value in {'design', 'project', 'assets', 'versions'}:
        raise ValueError('Slide id must contain 1-80 lowercase letters, digits or underscores')
    return value


def nonempty(value, label, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{label} must contain 1-{maximum} characters')
    return value


def atomic_json(path, value):
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temporary.open('x') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Slides:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, project_id):
        if not isinstance(project_id, str) or not re.fullmatch(r'deck-[a-f0-9]{32}', project_id):
            raise ValueError('Unknown or invalid project_id')
        directory = (self.root / project_id).resolve(strict=True)
        if directory.parent != self.root:
            raise ValueError('Project path escapes this Session')
        return directory

    @contextmanager
    def locked(self, project_id):
        directory = self.directory(project_id)
        lock_root = Path(tempfile.gettempdir()) / 'oma-slides-locks'
        lock_root.mkdir(exist_ok=True, mode=0o700)
        lock_name = hashlib.sha256(str(directory).encode()).hexdigest()
        with (lock_root / lock_name).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            document = json.loads((directory / 'project.json').read_text())
            deck = Deck(document['project_id'], document['title'], [Page(**p) for p in document['pages']], document['versions'])
            yield directory, deck

    def save(self, directory, deck):
        atomic_json(directory / 'project.json', asdict(deck))

    def initialize(self, title, outline, generate_mode='pptx', template_id=None, **options):
        nonempty(title, 'title', 255)
        if generate_mode != 'pptx':
            raise ValueError('This local service supports pptx mode; HTML and AI image modes are unavailable')
        if template_id not in {None, 'deck-stage-basic'}:
            raise ValueError('Only the local deck-stage-basic theme is available; Manus official templates are not installed')
        if not 1 <= len(outline) <= 50:
            raise ValueError('Outline must contain 1-50 slides')
        pages = [Page(slide_id(p['id']), nonempty(p['title'], 'slide title', 255), nonempty(p['summary'], 'summary')) for p in outline]
        if len({p.id for p in pages}) != len(pages):
            raise ValueError('Slide ids must be unique')
        project_id = 'deck-' + uuid.uuid4().hex
        directory = self.root / project_id
        directory.mkdir()
        try:
            (directory / 'assets').mkdir()
            (directory / 'versions').mkdir()
            (directory / 'design.xml').write_text(DEFAULT_DESIGN)
            deck = Deck(project_id, title, pages)
            self.save(directory, deck)
            return self.describe(directory, deck)
        except BaseException:
            shutil.rmtree(directory)
            raise

    def describe(self, directory, deck):
        return {
            'project_id': deck.project_id, 'title': deck.title,
            'project_dir': str(directory), 'generate_mode': 'pptx',
            'resource_uri': 'oma-slides://deck/' + deck.project_id,
            'slides': [{'id': p.id, 'title': p.title, 'summary': p.summary, 'note': p.note,
                        'file_path': str(directory / (p.id + '.xml')),
                        'state': 'edited' if (directory / (p.id + '.xml')).is_file() else 'edit_pending'} for p in deck.pages],
            'instructions': 'Write complete slide XML files in project_dir. present validates and renders all pages. After editing content or notes, present again before exporting the new version.',
        }

    def attach(self, resource_uri):
        if not resource_uri.startswith('oma-slides://deck/'):
            raise ValueError('Only oma-slides://deck/<project_id> resources in this Session are supported')
        with self.locked(resource_uri.removeprefix('oma-slides://deck/')) as (directory, deck):
            return self.describe(directory, deck)

    def target(self, deck, item):
        identifier, number = item.get('slide_id'), item.get('slide_number')
        by_id = next((p for p in deck.pages if p.id == identifier), None) if identifier else None
        by_number = deck.pages[number - 1] if type(number) is int and 1 <= number <= len(deck.pages) else None
        if identifier and not by_id or number is not None and not by_number:
            raise ValueError('Unknown slide id or out-of-range slide number')
        if by_id and by_number and by_id.id != by_number.id:
            raise ValueError('slide_id and slide_number refer to different slides')
        page = by_id or by_number
        if page is None:
            raise ValueError('Provide slide_id or slide_number')
        return page

    def notes(self, project_id, notes):
        if not 1 <= len(notes) <= 50:
            raise ValueError('notes must contain 1-50 entries')
        with self.locked(project_id) as (directory, deck):
            targets = set()
            for item in notes:
                page = self.target(deck, item)
                if page.id in targets or len(item['note']) > 20000:
                    raise ValueError('Duplicate note target or note exceeds 20000 characters')
                if not (directory / (page.id + '.xml')).is_file():
                    raise ValueError('Write the slide content before its notes')
                targets.add(page.id)
                page.note = item['note']
            self.save(directory, deck)
            return self.describe(directory, deck)

    def organize(self, project_id, operation, slide_data):
        if not 1 <= len(slide_data) <= 50:
            raise ValueError('slide_data must contain 1-50 entries')
        with self.locked(project_id) as (directory, deck):
            if operation == 'add':
                for item in slide_data:
                    identifier = slide_id(item.get('slide_id'))
                    if any(p.id == identifier for p in deck.pages) or (directory / (identifier + '.xml')).exists():
                        raise ValueError('New slide id already exists')
                    page = Page(identifier, nonempty(item.get('page_title'), 'page_title', 255), nonempty(item.get('summary'), 'summary'))
                    position = item.get('position', len(deck.pages) + 1)
                    if type(position) is not int or not 1 <= position <= len(deck.pages) + 1:
                        raise ValueError('Invalid insertion position')
                    deck.pages.insert(position - 1, page)
                if len(deck.pages) > 50:
                    raise ValueError('Deck cannot exceed 50 slides')
            elif operation in {'delete', 'reorder'}:
                targets = [self.target(deck, item) for item in slide_data]
                ids = {p.id for p in targets}
                if len(ids) != len(targets):
                    raise ValueError('Duplicate target slides')
                if operation == 'reorder':
                    if ids != {p.id for p in deck.pages}:
                        raise ValueError('Reorder must include every active slide exactly once')
                    deck.pages = targets
                else:
                    deck.pages = [p for p in deck.pages if p.id not in ids]
                    if not deck.pages:
                        raise ValueError('Cannot delete every slide')
            else:
                raise ValueError('operation must be add, delete or reorder')
            self.save(directory, deck)
            return self.describe(directory, deck)

    def snapshot(self, directory, deck):
        pending = [p.id + '.xml' for p in deck.pages if not (directory / (p.id + '.xml')).is_file()]
        if pending:
            raise ValueError('Cannot present: edit_pending slides: ' + ', '.join(pending))
        files = {}
        for path in [directory / 'design.xml', *[directory / (p.id + '.xml') for p in deck.pages], *sorted((directory / 'assets').rglob('*'))]:
            if path.is_dir():
                continue
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(directory):
                raise ValueError('Project files cannot link outside this deck')
            limit = 20 << 20 if path.is_relative_to(directory / 'assets') else 1 << 20
            if resolved.stat().st_size > limit:
                raise ValueError('Project file exceeds its size limit')
            files[str(path.relative_to(directory))] = path.read_bytes()
        if sum(map(len, files.values())) > 50 << 20:
            raise ValueError('Project exceeds 50 MiB')
        digest = hashlib.sha256(json.dumps({'title': deck.title, 'pages': [asdict(p) for p in deck.pages]}, sort_keys=True, ensure_ascii=False).encode())
        for name, content in sorted(files.items()):
            digest.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
        return 'v-' + digest.hexdigest()[:24], files

    def present(self, project_id, title=None):
        with self.locked(project_id) as (directory, deck):
            if title is not None:
                deck.title = nonempty(title, 'title', 255)
            version_id, files = self.snapshot(directory, deck)
            if version_id not in deck.versions:
                self.build(directory, deck, version_id, files)
            self.save(directory, deck)
            return {**self.describe(directory, deck), 'status': 'ready', 'version_id': version_id, **deck.versions[version_id]}

    def build(self, directory, deck, version_id, files):
        with tempfile.TemporaryDirectory(prefix='oma-slides-build-') as temporary:
            work = Path(temporary)
            snapshot = work / 'source'
            for name, content in files.items():
                target = snapshot / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            pages = []
            for page in deck.pages:
                content = files[page.id + '.xml'].replace(str(directory).encode(), str(snapshot).encode())
                pages.append((page.id, content, page.note))
            pptx = work / 'presentation.pptx'
            compile_deck(files['design.xml'], pages, snapshot, pptx)
            pdf = preview(pptx, work / 'preview')
            if len(PdfReader(pdf).pages) != len(deck.pages):
                raise ValueError('PDF page count differs from the outline; check for overflow')
            if self.snapshot(directory, deck)[0] != version_id:
                raise ValueError('Source changed during rendering; present again after edits finish')
            destination = directory / 'versions' / version_id
            if destination.exists():
                shutil.rmtree(destination)
            destination.mkdir()
            try:
                shutil.copyfile(pptx, destination / 'presentation.pptx')
                shutil.copyfile(pdf, destination / 'presentation.pdf')
                images = sorted((work / 'preview').glob('page-*.png'))
                if len(images) != len(deck.pages):
                    raise ValueError('Preview page count differs from the outline')
                markup = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>', html.escape(deck.title), '</title><style>body{margin:0;background:#111827;color:white;font:16px sans-serif}header{padding:24px}figure{margin:24px auto;max-width:1280px}img{width:100%;display:block}figcaption{padding:12px}details{white-space:pre-wrap;padding:12px}</style><header><h1>', html.escape(deck.title), '</h1></header>']
                for index, image in enumerate(images):
                    shutil.copyfile(image, destination / f'page-{index + 1}.png')
                    markup.extend(['<figure><img alt="', html.escape(deck.pages[index].title, quote=True), '" src="data:image/png;base64,', base64.b64encode(image.read_bytes()).decode(), '"><figcaption>', html.escape(deck.pages[index].title), '</figcaption><details><summary>演讲备注</summary>', html.escape(deck.pages[index].note), '</details></figure>'])
                (destination / 'preview.html').write_text(''.join(markup) + '</html>')
                deck.versions[version_id] = {'pptx_path': str(destination / 'presentation.pptx'), 'pdf_path': str(destination / 'presentation.pdf'), 'preview_path': str(destination / 'preview.html')}
            except BaseException:
                shutil.rmtree(destination)
                raise

    def export(self, project_id, format, version_id=None, output_dir=None):
        if format not in {'pdf', 'pptx'}:
            raise ValueError('format must be pdf or pptx')
        with self.locked(project_id) as (directory, deck):
            if version_id is None:
                version_id = self.snapshot(directory, deck)[0]
            if version_id not in deck.versions:
                raise ValueError('This version has not been presented; call present after editing content or notes')
            path = Path(deck.versions[version_id][format + '_path'])
            if not path.resolve(strict=True).is_relative_to(directory):
                raise ValueError('Export path escapes this deck')
            if output_dir is not None:
                output = Path(output_dir)
                if not output.is_absolute() or not output.resolve().is_relative_to(directory):
                    raise ValueError('output_dir must be an absolute directory under this project')
                output.mkdir(parents=True, exist_ok=True)
                destination = output / (version_id + '.' + format)
                if destination.exists():
                    if destination.read_bytes() != path.read_bytes():
                        raise ValueError('Output file already exists with different content')
                else:
                    shutil.copyfile(path, destination)
                path = destination
            return {'file_name': path.name, 'file_path': str(path), 'format': format, 'project_id': project_id, 'status': 'ready', 'version_id': version_id}
