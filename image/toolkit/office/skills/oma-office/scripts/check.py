import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from render import convert


def dependencies():
    versions = {}
    for name in ['python-docx', 'openpyxl', 'python-pptx', 'pypdf', 'pdfplumber', 'reportlab', 'Pillow']:
        versions[name] = importlib.metadata.version(name)
    for name in ['soffice', 'pdftoppm', 'pdftotext', 'qpdf', 'dot', 'pandoc', 'tesseract']:
        if not shutil.which(name):
            raise RuntimeError('Missing command: ' + name)
    fonts = subprocess.check_output(['fc-match', 'Noto Sans CJK SC', 'family'], text=True)
    if 'Noto Sans CJK' not in fonts:
        raise RuntimeError('Missing Noto CJK fonts')
    return versions


def exercise(output):
    from docx import Document
    from docx.oxml.ns import qn
    from openpyxl import Workbook, load_workbook
    from openpyxl.chart import BarChart, Reference
    from pptx import Presentation
    from pptx.util import Inches
    from pypdf import PdfReader, PdfWriter
    from PIL import Image

    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='oma-office-check-') as scratch:
        work = Path(scratch)
        missing = work / 'missing.docx'
        try:
            convert(missing, output, 'pdf')
        except FileNotFoundError:
            pass
        else:
            raise AssertionError('Missing input must fail')
        doc = Document()
        style = doc.styles['Normal']
        style.font.name = 'Noto Sans CJK SC'
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), 'Noto Sans CJK SC')
        doc.add_heading('办公能力验收 Office Acceptance', 0)
        doc.add_paragraph('中文内容、表格与文件交付。')
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = '合计'
        table.rows[0].cells[1].text = '2000'
        doc.save(output / 'report.docx')
        assert len(Document(output / 'report.docx').tables) == 1
        book = Workbook()
        sheet = book.active
        sheet.append(['项目', '金额'])
        sheet.append(['研发', 1200])
        sheet.append(['运营', 800])
        sheet.append(['合计', '=SUM(B2:B3)'])
        sheet.column_dimensions['A'].width = 18
        sheet.column_dimensions['B'].width = 18
        chart = BarChart()
        chart.add_data(Reference(sheet, min_col=2, min_row=1, max_row=3), titles_from_data=True)
        chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=3))
        sheet.add_chart(chart, 'D2')
        sheet.print_options.horizontalCentered = True
        sheet.page_setup.orientation = 'landscape'
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 1
        book.save(work / 'budget.xlsx')
        recalculated = convert(work / 'budget.xlsx', output, 'xlsx')
        assert load_workbook(recalculated, data_only=False).active['B4'].value == '=SUM(B2:B3)'
        assert load_workbook(recalculated, data_only=True).active['B4'].value == 2000
        deck = Presentation()
        deck.slide_width = Inches(13.333)
        deck.slide_height = Inches(7.5)
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = '办公能力验收 Office Acceptance'
        slide.placeholders[1].text = '文档 / 表格 / 幻灯片 / PDF / 图表'
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    for run in paragraph.runs:
                        run.font.name = 'Noto Sans CJK SC'
        slide.notes_slide.notes_text_frame.text = '验收备注：合计 2000'
        deck.save(output / 'slides.pptx')
        assert '2000' in Presentation(output / 'slides.pptx').slides[0].notes_slide.notes_text_frame.text
        preview = work / 'preview'
        for name in ['report.docx', 'budget.xlsx', 'slides.pptx']:
            pdf = convert(output / name, preview, 'pdf')
            assert len(PdfReader(pdf).pages) == 1
            text = subprocess.check_output(['pdftotext', str(pdf), '-'], text=True)
            assert '办公能力验收' in text.replace(' ', '') if name != 'budget.xlsx' else '2000' in text
            subprocess.run(['qpdf', '--check', str(pdf)], check=True, capture_output=True)
            shutil.copyfile(pdf, output / pdf.name)
            subprocess.run(['pdftoppm', '-scale-to', '1400', '-singlefile', '-png', str(pdf), str(output / pdf.stem)], check=True)
        markdown = work / 'markdown.md'
        markdown.write_text('# Markdown acceptance\n\nOffice report with a table.\n\n| Item | Value |\n| --- | --- |\n| Total | 2000 |\n')
        subprocess.run(['pandoc', str(markdown), '-o', str(work / 'markdown.docx')], check=True)
        convert(work / 'markdown.docx', output, 'pdf')
        merged = PdfWriter()
        merged.append(output / 'report.pdf')
        merged.append(output / 'slides.pdf')
        merged.write(output / 'merged.pdf')
        assert len(PdfReader(output / 'merged.pdf').pages) == 2
        graph = work / 'flow.dot'
        graph.write_text('digraph { node [fontname="Noto Sans CJK SC"]; "需求" -> "分析" -> "交付"; }')
        subprocess.run(['dot', '-Tpng', str(graph), '-o', str(output / 'flow.png')], check=True)
        with Image.open(output / 'flow.png') as image:
            assert image.width > 0 and image.height > 0
        try:
            convert(output / 'report.docx', preview, 'pdf')
        except FileExistsError:
            pass
        else:
            raise AssertionError('Existing output must not be replaced')
    return sorted(path.name for path in output.iterdir() if path.is_file())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--exercise', type=Path)
    args = parser.parse_args()
    result = {'dependencies': dependencies()}
    if args.exercise:
        result['files'] = exercise(args.exercise)
    print(json.dumps(result, ensure_ascii=False))
