from dataclasses import dataclass
import math
from pathlib import Path
import re
import subprocess
import sys
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as ET
from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Emu, Pt


def number(value):
    if not re.fullmatch(r'-?\d+(?:\.\d+)?', str(value)):
        raise ValueError('Expected a plain decimal number')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Expected a finite number')
    return result


def px(value):
    return Emu(round(number(value) * 9525))


def attributes(element, supported):
    extra = set(element.attrib) - set(supported.split())
    if extra:
        raise ValueError(f'Unsupported {element.tag} attributes: {", ".join(sorted(extra))}')


def parse_xml(content, root):
    if len(content) > 1 << 20:
        raise ValueError('XML document exceeds 1 MiB')
    element = ET.fromstring(content)
    if element.tag != root:
        raise ValueError(f'Expected <{root}> root')
    if sum(1 for _ in element.iter()) > 2000:
        raise ValueError('XML contains too many elements')
    return element


@dataclass
class Design:
    width: float
    height: float
    colors: dict[str, str]
    fonts: dict[str, tuple[str, float]]

    def color(self, value):
        value = self.colors.get(value, value)
        if not re.fullmatch(r'#[0-9A-Fa-f]{6}', value):
            raise ValueError('Colors must be defined tokens or #RRGGBB')
        return RGBColor.from_string(value[1:])

    def font(self, element):
        name = element.get('font', 'body')
        if name not in self.fonts and re.fullmatch(r'[a-z][a-z0-9-]*', name):
            raise ValueError('Undefined font token')
        family, size = self.fonts.get(name, (name, 18))
        if family == 'Noto Sans SC':
            family = 'Noto Sans CJK SC'
        size = number(element.get('size', str(size)))
        if not 0 < size <= 512:
            raise ValueError('Font size must be between 0 and 512 pixels')
        return family, size


def read_design(content):
    root = parse_xml(content, 'design')
    attributes(root, '')
    sizes = root.findall('size')
    if len(sizes) != 1:
        raise ValueError('Design requires exactly one size')
    attributes(sizes[0], 'width height')
    width, height = number(sizes[0].get('width', '0')), number(sizes[0].get('height', '0'))
    if not (0 < width <= 4096 and 0 < height <= 4096):
        raise ValueError('Canvas dimensions must be between 0 and 4096 pixels')
    colors, fonts = {}, {}
    for child in root:
        if len(child) or (child.text or '').strip():
            raise ValueError('Design entries must be empty elements')
        if child.tag == 'size':
            continue
        name = child.get('name', '')
        if not re.fullmatch(r'[a-z][a-z0-9-]*', name):
            raise ValueError('Invalid design token name')
        if child.tag == 'color':
            attributes(child, 'name value')
            if name in colors:
                raise ValueError('Duplicate color token')
            value = child.get('value', '')
            if not re.fullmatch(r'#[0-9A-Fa-f]{6}', value):
                raise ValueError('Design colors require #RRGGBB')
            colors[name] = value
        elif child.tag == 'font':
            attributes(child, 'name family size')
            if name in fonts or not child.get('family') or number(child.get('size', '0')) <= 0:
                raise ValueError('Invalid or duplicate font token')
            fonts[name] = (child.get('family'), number(child.get('size')))
        else:
            raise ValueError(f'Unsupported design element: {child.tag}')
    if not colors or not fonts:
        raise ValueError('Design requires at least one color and one font')
    return Design(width, height, colors, fonts)


COMMON = 'id x y width height rotation'
TEXT = 'font size color align anchor wrap autofit line-height space-before space-after padding'


def bounds(element, design):
    x, y, width, height = [number(element.get(key, '0')) for key in ['x', 'y', 'width', 'height']]
    if min(x, x + width) < 0 or min(y, y + height) < 0 or max(x, x + width) > design.width or max(y, y + height) > design.height:
        raise ValueError(f'Element {element.get("id", element.tag)} exceeds the canvas')
    if width <= 0 or height <= 0:
        if element.tag != 'shape' or element.get('type') != 'line' or (width == 0 and height == 0):
            raise ValueError('Elements require positive dimensions, except line endpoints')
    return x, y, width, height


def set_font(font, element, design):
    family, size = design.font(element)
    font.name = family
    font.size = Pt(size * 0.75)
    font.color.rgb = design.color(element.get('color', '#000000'))
    properties = font._rPr
    for tag in ['a:ea', 'a:cs']:
        child = properties.find(tag, properties.nsmap)
        if child is None:
            child = OxmlElement(tag)
            properties.append(child)
        child.set('typeface', family)


def add_runs(paragraph, element, design, inherited, marks=()):
    style = {**inherited, **element.attrib}

    def append(text, active):
        if not text:
            return
        run = paragraph.add_run()
        run.text = text
        set_font(run.font, Element('span', style), design)
        run.font.bold = 'strong' in active
        run.font.italic = 'em' in active
        run.font.underline = 'u' in active

    append(element.text, marks)
    for child in element:
        if child.tag not in {'strong', 'em', 'u', 'span'}:
            raise ValueError(f'Unsupported inline element: {child.tag}')
        attributes(child, 'font size color')
        add_runs(paragraph, child, design, style, (*marks, child.tag))
        append(child.tail, marks)


def fill_text(frame, element, design):
    frame.clear()
    frame.word_wrap = element.get('wrap', 'true') == 'true'
    if element.get('wrap', 'true') not in {'true', 'false'}:
        raise ValueError('wrap must be true or false')
    frame.vertical_anchor = {'top': MSO_ANCHOR.TOP, 'middle': MSO_ANCHOR.MIDDLE, 'bottom': MSO_ANCHOR.BOTTOM}[element.get('anchor', 'top')]
    frame.auto_size = {'none': MSO_AUTO_SIZE.NONE, 'shrink': MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE}[element.get('autofit', 'none')]
    padding = [number(v) for v in element.get('padding', '0').split()]
    if len(padding) == 1:
        padding *= 4
    elif len(padding) == 2:
        padding *= 2
    if len(padding) != 4 or min(padding) < 0:
        raise ValueError('padding requires 1, 2 or 4 non-negative numbers')
    frame.margin_top, frame.margin_right, frame.margin_bottom, frame.margin_left = [px(v) for v in padding]
    blocks = list(element) if any(child.tag in {'p', 'ul', 'ol'} for child in element) else [element]
    if blocks != [element] and (element.text or '').strip():
        raise ValueError('Text cannot mix direct text with paragraphs')
    paragraphs = []
    for block in blocks:
        if block.tag in {'ul', 'ol'}:
            attributes(block, '')
            for index, item in enumerate(block):
                if item.tag != 'li':
                    raise ValueError('Lists require li children')
                paragraphs.append((item, '• ' if block.tag == 'ul' else f'{index + 1}. '))
        else:
            paragraphs.append((block, ''))
    for index, (block, prefix) in enumerate(paragraphs):
        if block is not element:
            attributes(block, TEXT)
        style = {**element.attrib, **block.attrib}
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = {'left': PP_ALIGN.LEFT, 'center': PP_ALIGN.CENTER, 'right': PP_ALIGN.RIGHT, 'justify': PP_ALIGN.JUSTIFY}[style.get('align', 'left')]
        paragraph.line_spacing = number(style.get('line-height', '1.2'))
        paragraph.space_before = Pt(number(style.get('space-before', '0')) * 0.75)
        paragraph.space_after = Pt(number(style.get('space-after', '0')) * 0.75)
        set_font(paragraph.font, Element('p', style), design)
        if prefix:
            paragraph.add_run().text = prefix
        add_runs(paragraph, block, design, style)


def style_shape(shape, element, design):
    if element.get('type') != 'line':
        if element.get('fill', 'none') == 'none':
            shape.fill.background()
        else:
            shape.fill.solid()
            shape.fill.fore_color.rgb = design.color(element.get('fill'))
    if element.get('stroke'):
        shape.line.color.rgb = design.color(element.get('stroke'))
        shape.line.width = Pt(number(element.get('stroke-width', '1')) * 0.75)
    else:
        shape.line.fill.background()


def add_shape(slide, element, design, box):
    attributes(element, COMMON + ' type fill stroke stroke-width arrow-start arrow-end')
    if len(element) or (element.text or '').strip():
        raise ValueError('Shapes cannot contain text; use a text element')
    x, y, width, height = box
    shape_type = element.get('type', '')
    if shape_type == 'line':
        shape = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, px(x), px(y), px(x + width), px(y + height))
        if element.get('stroke') is None:
            element.set('stroke', '#000000')
        for attribute, tag in [('arrow-start', 'a:headEnd'), ('arrow-end', 'a:tailEnd')]:
            value = element.get(attribute, 'none')
            if value not in {'none', 'arrow'}:
                raise ValueError('Line arrows must be none or arrow')
            if value == 'arrow':
                end = OxmlElement(tag)
                end.set('type', 'triangle')
                shape.line._get_or_add_ln().append(end)
    else:
        mapping = {'rect': MSO_SHAPE.RECTANGLE, 'ellipse': MSO_SHAPE.OVAL, 'arrow': MSO_SHAPE.RIGHT_ARROW, 'chevron': MSO_SHAPE.CHEVRON, 'diamond': MSO_SHAPE.DIAMOND, 'triangle': MSO_SHAPE.ISOSCELES_TRIANGLE, 'hexagon': MSO_SHAPE.HEXAGON}
        if shape_type not in mapping:
            raise ValueError(f'Unsupported shape type: {shape_type}')
        shape = slide.shapes.add_shape(mapping[shape_type], *[px(v) for v in box])
    style_shape(shape, element, design)
    return shape


def add_image(slide, element, project, box):
    attributes(element, COMMON + ' src fit')
    source = Path(element.get('src', ''))
    if not source.is_absolute():
        raise ValueError('Image src requires an absolute project-local path')
    source = source.resolve(strict=True)
    if not source.is_relative_to(project.resolve()):
        raise ValueError('Copy images into the deck assets directory before use')
    if source.stat().st_size > 20 << 20:
        raise ValueError('Image exceeds 20 MiB')
    x, y, width, height = box
    fit = element.get('fit', 'cover')
    if fit == 'cover':
        return add_cover(slide, source, box)
    if fit == 'contain':
        with Image.open(source) as image:
            scale = min(width / image.width, height / image.height)
            actual_width, actual_height = image.width * scale, image.height * scale
        x += (width - actual_width) / 2
        y += (height - actual_height) / 2
        width, height = actual_width, actual_height
    elif fit != 'stretch':
        raise ValueError('fit must be cover, contain or stretch')
    return slide.shapes.add_picture(str(source), px(x), px(y), px(width), px(height))


def add_cover(slide, source, box):
    x, y, width, height = box
    picture = slide.shapes.add_picture(str(source), px(x), px(y), px(width), px(height))
    with Image.open(source) as image:
        ratio = image.width / image.height
    if ratio > width / height:
        picture.crop_left = picture.crop_right = (1 - width / height / ratio) / 2
    else:
        picture.crop_top = picture.crop_bottom = (1 - ratio / (width / height)) / 2
    return picture


def add_icon(slide, element, design, box):
    attributes(element, COMMON + ' name color')
    if element.get('name') != 'circle-check':
        raise ValueError('Only circle-check is supported; use a local PNG for other icons')
    x, y, width, height = box
    size = min(width, height)
    x += (width - size) / 2
    y += (height - size) / 2
    circle = slide.shapes.add_shape(MSO_SHAPE.OVAL, px(x), px(y), px(size), px(size))
    circle.fill.background()
    circle.line.color.rgb = design.color(element.get('color', '#000000'))
    circle.line.width = Pt(1)
    for x1, y1, x2, y2 in [(0.25, 0.52, 0.43, 0.7), (0.43, 0.7, 0.76, 0.3)]:
        line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, px(x + x1 * size), px(y + y1 * size), px(x + x2 * size), px(y + y2 * size))
        line.line.color.rgb = circle.line.color.rgb
        line.line.width = Pt(1)
    return circle


def add_table(slide, element, design, box):
    if 'text-color' in element.attrib:
        raise ValueError('Table text color uses color="text-main"; text-color is a chart attribute')
    attributes(element, COMMON + ' ' + TEXT + ' columns fill header-fill')
    rows = element.findall('tr')
    if not rows or len(rows) != len(element):
        raise ValueError('Table requires tr rows')
    columns = len(rows[0])
    if columns < 1 or any(len(row) != columns for row in rows):
        raise ValueError('Basic tables require equal column counts; merged cells are not supported')
    table = slide.shapes.add_table(len(rows), columns, *[px(v) for v in box])
    weights = [number(v) for v in element.get('columns', ','.join(['1'] * columns)).split(',')]
    if len(weights) != columns or min(weights) <= 0:
        raise ValueError('Invalid column weights')
    for column, weight in zip(table.table.columns, weights):
        column.width = px(box[2] * weight / sum(weights))
    for row_index, row in enumerate(rows):
        attributes(row, 'fill ' + TEXT)
        for column, cell in enumerate(row):
            if cell.tag != 'td':
                raise ValueError('Table rows require td cells')
            attributes(cell, 'fill ' + TEXT)
            style = {**element.attrib, **row.attrib, **cell.attrib}
            style.setdefault('color', design.colors.get('text-main', '#000000'))
            target = table.table.cell(row_index, column)
            fill = cell.get('fill', row.get('fill', element.get('header-fill') if row_index == 0 else element.get('fill')))
            if fill:
                target.fill.solid()
                target.fill.fore_color.rgb = design.color(fill)
            text = Element('text', style)
            text.text = cell.text
            text.extend(list(cell))
            fill_text(target.text_frame, text, design)
            required = target.text_frame.margin_top + target.text_frame.margin_bottom
            for paragraph in target.text_frame.paragraphs:
                size = max([paragraph.font.size or 0, *[run.font.size or 0 for run in paragraph.runs]])
                required += size * max(1.0, paragraph.line_spacing) * max(1, len(paragraph.text.splitlines()))
                required += (paragraph.space_before or 0) + (paragraph.space_after or 0)
            available = table.table.rows[row_index].height
            if required > available + px(1):
                raise ValueError(f'Table row {row_index + 1} needs height at least {math.ceil(required / 9525)}px, but has {available / 9525:.1f}px; increase table height or reduce font size')
    return table


def add_chart(slide, element, design, box):
    attributes(element, COMMON + ' type font text-size text-color categories color legend data-labels')
    chart_type = {'bar': XL_CHART_TYPE.BAR_CLUSTERED, 'column': XL_CHART_TYPE.COLUMN_CLUSTERED, 'line': XL_CHART_TYPE.LINE, 'area': XL_CHART_TYPE.AREA, 'pie': XL_CHART_TYPE.PIE, 'donut': XL_CHART_TYPE.DOUGHNUT}.get(element.get('type'))
    if chart_type is None:
        raise ValueError('Unsupported chart type')
    legend = element.get('legend', 'none')
    positions = {'top': XL_LEGEND_POSITION.TOP, 'bottom': XL_LEGEND_POSITION.BOTTOM, 'left': XL_LEGEND_POSITION.LEFT, 'right': XL_LEGEND_POSITION.RIGHT}
    if legend != 'none' and legend not in positions:
        raise ValueError('Chart legend must be none, top, bottom, left or right; use legend="bottom" to display it')
    data = CategoryChartData()
    categories = element.get('categories', '').split(',')
    if not any(categories):
        raise ValueError('Chart requires categories')
    data.categories = categories
    colors = element.get('color', '').split(',')
    for series in element:
        if series.tag != 'series':
            raise ValueError('Chart requires series children')
        attributes(series, 'name values color')
        values = [number(v) for v in series.get('values', '').split(',')]
        if len(values) != len(categories):
            raise ValueError('Chart series length differs from categories')
        data.add_series(series.get('name', ''), values)
    if not len(element) or (element.get('type') in {'pie', 'donut'} and len(element) != 1):
        raise ValueError('Invalid number of chart series')
    chart_shape = slide.shapes.add_chart(chart_type, *[px(v) for v in box], data)
    chart = chart_shape.chart
    font_element = Element('text', {'font': element.get('font', 'body'), 'size': element.get('text-size', '16'), 'color': element.get('text-color', '#666666')})
    set_font(chart.font, font_element, design)
    chart.has_legend = legend != 'none'
    if chart.has_legend:
        chart.legend.position = positions[legend]
    chart.plots[0].has_data_labels = element.get('data-labels', 'false') == 'true'
    for index, series in enumerate(chart.series):
        color = element[index].get('color') or colors[index % len(colors)]
        targets = list(series.points) if element.get('type') in {'pie', 'donut'} else [series]
        for point_index, target in enumerate(targets):
            target.format.fill.solid()
            target.format.fill.fore_color.rgb = design.color(colors[point_index % len(colors)] if len(targets) > 1 else color)
    return chart_shape


def compile_deck(design_content, pages, project, destination):
    design = read_design(design_content)
    deck = Presentation()
    deck.slide_width, deck.slide_height = px(design.width), px(design.height)
    for slide_id, content, note in pages:
        root = parse_xml(content, 'slide')
        attributes(root, 'id')
        if root.get('id', slide_id) != slide_id:
            raise ValueError('Slide root id differs from the registered slide')
        if (root.text or '').strip():
            raise ValueError('Slide requires elements, not direct text')
        backgrounds = root.findall('background')
        if len(backgrounds) != 1:
            raise ValueError(f'{slide_id} requires exactly one background')
        attributes(backgrounds[0], 'color')
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = design.color(backgrounds[0].get('color', ''))
        ids = set()
        for element in root:
            if element.tag == 'background':
                continue
            identifier = element.get('id')
            if identifier and identifier in ids:
                raise ValueError('Duplicate element id')
            ids.add(identifier)
            box = bounds(element, design)
            if element.tag == 'text':
                attributes(element, COMMON + ' ' + TEXT)
                shape = slide.shapes.add_textbox(*[px(v) for v in box])
                fill_text(shape.text_frame, element, design)
            elif element.tag == 'shape':
                shape = add_shape(slide, element, design, box)
            elif element.tag == 'image':
                shape = add_image(slide, element, project, box)
            elif element.tag == 'icon':
                shape = add_icon(slide, element, design, box)
            elif element.tag == 'table':
                shape = add_table(slide, element, design, box)
            elif element.tag == 'chart':
                shape = add_chart(slide, element, design, box)
            else:
                raise ValueError(f'Unsupported slide element: {element.tag}')
            shape.rotation = number(element.get('rotation', '0'))
        slide.notes_slide.notes_text_frame.text = note
    deck.save(destination)


def command(arguments, timeout=150):
    process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        raise
    if process.returncode:
        raise RuntimeError('Office conversion failed: ' + (stdout + stderr)[-2000:])


def preview(pptx, directory):
    directory.mkdir()
    command([sys.executable, str(Path(__file__).resolve().parents[1] / 'render.py'), str(pptx), str(directory), '--format', 'pdf'])
    pdf = directory / (pptx.stem + '.pdf')
    command(['pdftoppm', '-png', '-scale-to', '1600', str(pdf), str(directory / 'page')])
    return pdf
