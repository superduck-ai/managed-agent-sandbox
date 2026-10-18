import concurrent.futures
import json
from pathlib import Path
import re
import tempfile
import unittest
import zipfile

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.enum.chart import XL_LEGEND_POSITION
from pypdf import PdfReader

from projects import Slides
from rendering import compile_deck, read_design


def text_page(identifier, title):
    return f'''<slide id="{identifier}"><background color="bg-page"/>
<text x="60" y="60" width="1160" height="100" font="heading" color="text-main">{title}</text>
<shape type="rect" x="60" y="200" width="1160" height="300" fill="bg-card"/>
<text x="90" y="230" width="1100" height="220" font="body" color="text-main"><p>中文内容与可编辑元素</p><p space-before="20"><strong>真实内容</strong>：文件、备注和预览均须通过检查。</p></text></slide>'''


def make_sample(service):
    result = service.initialize('中文办公演示', [{'id': key, 'title': title, 'summary': '本地能力验证'} for key, title in [('cover', '办公工具演示'), ('workflow', '从创建到交付'), ('results', '交付与验收')]])
    root = Path(result['project_dir'])
    for page in result['slides']:
        Path(page['file_path']).write_text(text_page(page['id'], page['title']))
    image = Image.new('RGB', (800, 300), '#0F172A')
    draw = ImageDraw.Draw(image)
    for index, height in enumerate([80, 150, 220]):
        draw.rectangle((100 + index * 200, 260 - height, 230 + index * 200, 260), fill='#14B8A6')
    image.save(root / 'assets' / 'illustration.png')
    (root / 'results.xml').write_text(f'''<slide id="results"><background color="bg-page"/>
<text x="60" y="60" width="1160" height="80" font="heading" color="text-main">交付与验收</text>
<icon name="circle-check" x="60" y="180" width="30" height="30" color="primary"/>
<image x="650" y="180" width="550" height="240" src="{root}/assets/illustration.png" fit="contain"/>
<table x="60" y="240" width="520" height="200" font="body" color="text-main" header-fill="primary" fill="bg-card"><tr><td>文件</td><td>状态</td></tr><tr><td>PPTX/PDF</td><td>已生成</td></tr></table>
<chart type="column" x="60" y="470" width="700" height="200" font="body" text-color="text-main" categories="创建,检查,交付" color="primary"><series name="步骤" values="1,2,3"/></chart>
</slide>''')
    service.notes(result['project_id'], [{'slide_id': p['id'], 'note': '演讲备注：' + p['title']} for p in result['slides']])
    return result


class SlidesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.service = Slides(Path(self.temporary.name) / 'slides')

    def test_invalid_initialization_has_no_project(self):
        bad = [([], 'pptx'), ([{'id': '../a', 'title': '标题', 'summary': '摘要'}], 'pptx'), ([{'id': 'design', 'title': '标题', 'summary': '摘要'}], 'pptx'), ([{'id': 'a', 'title': '标题', 'summary': '摘要'}], 'image')]
        for outline, mode in bad:
            with self.subTest(outline=outline, mode=mode), self.assertRaises(ValueError):
                self.service.initialize('演示', outline, mode)
        self.assertEqual(list(self.service.root.iterdir()), [])

    def test_missing_and_invalid_pages_reject_delivery(self):
        result = self.service.initialize('演示', [{'id': 'a', 'title': '标题', 'summary': '摘要'}])
        with self.assertRaisesRegex(ValueError, 'edit_pending'):
            self.service.present(result['project_id'])
        path = Path(result['project_dir']) / 'a.xml'
        path.write_text('<slide><background color="#FFFFFF"/><unknown width="10" height="10"/></slide>')
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.service.present(result['project_id'])
        with self.assertRaises(ValueError):
            self.service.export(result['project_id'], 'pptx')
        self.assertEqual(list((path.parent / 'versions').iterdir()), [])

    def test_invalid_chart_legend_has_actionable_error(self):
        result = make_sample(self.service)
        path = Path(result['project_dir']) / 'results.xml'
        path.write_text(path.read_text().replace('<chart type="column"', '<chart legend="true" type="column"'))
        with self.assertRaisesRegex(ValueError, 'legend.*bottom'):
            self.service.present(result['project_id'])

    def test_documented_chart_example_compiles_to_editable_chart(self):
        guide = Path(__file__).parents[1] / 'skills/oma-office/references/slides.md'
        match = re.search(r'```xml\n(<chart\b.*?</chart>)\n```', guide.read_text(), re.S)
        self.assertIsNotNone(match)
        result = self.service.initialize('示例校验', [{'id': 'chart', 'title': '图表', 'summary': '文档中的原生图表示例'}])
        root = Path(result['project_dir'])
        content = '<slide id="chart"><background color="bg-page"/>' + match.group(1) + '</slide>'
        output = Path(self.temporary.name) / 'example.pptx'
        compile_deck((root / 'design.xml').read_bytes(), [('chart', content.encode(), '')], root, output)
        slide = Presentation(output).slides[0]
        chart = next(shape.chart for shape in slide.shapes if shape.has_chart)
        self.assertEqual(len(chart.series), 2)
        self.assertEqual(chart.legend.position, XL_LEGEND_POSITION.BOTTOM)

    def test_table_too_small_for_font_rejects_delivery(self):
        result = make_sample(self.service)
        path = Path(result['project_dir']) / 'results.xml'
        path.write_text(path.read_text().replace('width="520" height="200"', 'width="520" height="10"'))
        with self.assertRaisesRegex(ValueError, 'Table row.*height'):
            self.service.present(result['project_id'])

    def test_table_without_color_uses_theme_text_color(self):
        result = make_sample(self.service)
        root = Path(result['project_dir'])
        path = root / 'results.xml'
        path.write_text(path.read_text().replace('font="body" color="text-main" header-fill=', 'font="body" header-fill='))
        presented = self.service.present(result['project_id'])
        slide = Presentation(presented['pptx_path']).slides[2]
        table = next(shape.table for shape in slide.shapes if shape.has_table)
        actual = table.cell(0, 0).text_frame.paragraphs[0].runs[0].font.color.rgb
        design = read_design((root / 'design.xml').read_bytes())
        self.assertEqual(actual, design.color('text-main'))

    def test_organize_rejects_partial_and_duplicate_targets_atomically(self):
        result = make_sample(self.service)
        identifier = result['project_id']
        manifest = Path(result['project_dir']) / 'project.json'
        original = manifest.read_bytes()
        for operation, items in [('reorder', [{'slide_id': 'cover'}]), ('delete', [{'slide_id': 'cover'}, {'slide_id': 'cover'}]), ('add', [{'slide_id': 'new', 'page_title': '新页', 'summary': '摘要'}, {'slide_id': 'cover', 'page_title': '重复', 'summary': '摘要'}])]:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.service.organize(identifier, operation, items)
            self.assertEqual(manifest.read_bytes(), original)
        changed = self.service.organize(identifier, 'reorder', [{'slide_id': p} for p in ['results', 'cover', 'workflow']])
        self.assertEqual([p['id'] for p in changed['slides']], ['results', 'cover', 'workflow'])
        self.service.organize(identifier, 'add', [{'slide_id': 'extra', 'page_title': '新增页', 'summary': '摘要', 'position': 2}])
        changed = self.service.organize(identifier, 'delete', [{'slide_id': 'extra'}])
        self.assertEqual(len(changed['slides']), 3)

    def test_paths_entities_and_unsupported_attributes_fail(self):
        result = make_sample(self.service)
        root = Path(result['project_dir'])
        (root / 'cover.xml').write_text('<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><slide>&a;</slide>')
        with self.assertRaises(Exception):
            self.service.present(result['project_id'])
        (root / 'cover.xml').write_text(text_page('cover', '标题').replace('font="heading"', 'font="heading" shadow="true"'))
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.service.present(result['project_id'])
        outside = Path(self.temporary.name) / 'outside.xml'
        outside.write_text(text_page('cover', '外部文件'))
        (root / 'cover.xml').unlink()
        (root / 'cover.xml').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.service.present(result['project_id'])
        with self.assertRaises(ValueError):
            self.service.attach('manus-resource://manus-slides/deck/other')

    def test_notes_and_versions_survive_reopen_and_export(self):
        result = make_sample(self.service)
        identifier = result['project_id']
        presented = self.service.present(identifier)
        with zipfile.ZipFile(presented['pptx_path']) as archive:
            self.assertIsNone(archive.testzip())
        deck = Presentation(presented['pptx_path'])
        self.assertEqual(len(deck.slides), 3)
        self.assertTrue(all('演讲备注：' in slide.notes_slide.notes_text_frame.text for slide in deck.slides))
        self.assertEqual(len(PdfReader(presented['pdf_path']).pages), 3)
        self.assertIn('办公工具演示', ''.join(p.extract_text() for p in PdfReader(presented['pdf_path']).pages))
        before = Path(presented['pptx_path']).read_bytes()
        self.service.notes(identifier, [{'slide_id': 'cover', 'note': '更新后的备注'}])
        with self.assertRaisesRegex(ValueError, 'present'):
            self.service.export(identifier, 'pptx')
        after = self.service.present(identifier)
        self.assertNotEqual(presented['version_id'], after['version_id'])
        self.assertEqual(Path(presented['pptx_path']).read_bytes(), before)
        reopened = Slides(self.service.root)
        self.assertEqual(reopened.attach(result['resource_uri'])['slides'][0]['note'], '更新后的备注')
        historical = reopened.export(identifier, 'pptx', presented['version_id'])
        self.assertEqual(Path(historical['file_path']).read_bytes(), before)
        with self.assertRaises(ValueError):
            reopened.export(identifier, 'pptx', output_dir=self.temporary.name)
        changed = Presentation(after['pptx_path'])
        self.assertEqual(changed.slides[0].notes_slide.notes_text_frame.text, '更新后的备注')
        self.assertIn('更新后的备注', Path(after['preview_path']).read_text())

    def test_concurrent_structural_updates_preserve_both_changes(self):
        result = make_sample(self.service)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.service.organize, result['project_id'], 'add', [{'slide_id': f'new{i}', 'page_title': '新增', 'summary': '摘要'}]) for i in range(2)]
            for future in futures:
                future.result(timeout=10)
        self.assertEqual(len(self.service.attach(result['resource_uri'])['slides']), 5)


if __name__ == '__main__':
    unittest.main()
