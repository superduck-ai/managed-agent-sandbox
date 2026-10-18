from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from docx import Document

sys.path.insert(0, str(Path(__file__).parent / 'skills/oma-office/scripts'))
from render import convert


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'brief.md'
        self.source.write_text('负责人：测试负责人\n项目周期：2026年10月1日至10月31日\n', encoding='utf-8')
        self.document = self.root / 'report.docx'

    def save(self, lines):
        document = Document()
        for line in lines:
            document.add_paragraph(line)
        document.save(self.document)

    def test_unsupported_metadata_blocks_conversion_before_output(self):
        self.save(['负责人：项目办公室', '报告周期：2026-10-13至2026-10-18'])
        with patch('render.subprocess.Popen') as process:
            with self.assertRaisesRegex(ValueError, 'DOCX metadata lacks source evidence'):
                convert(self.document, self.root / 'output', 'pdf', [self.source])
            process.assert_not_called()
        self.assertFalse((self.root / 'output').exists())

    def test_unreadable_source_does_not_skip_validation(self):
        self.save(['负责人：测试负责人'])
        with self.assertRaises(FileNotFoundError):
            convert(self.document, self.root / 'output', 'pdf', [self.root / 'missing.txt'])
        self.assertFalse((self.root / 'output').exists())

    def test_session_attachments_are_used_automatically(self):
        self.save(['报告周期：2026-10-13至2026-10-18'])
        original = Path.is_dir
        with patch.object(Path, 'is_dir', lambda path: True if path == Path('/mnt/session/uploads') else original(path)):
            with patch.object(Path, 'rglob', return_value=iter([self.source])):
                with self.assertRaisesRegex(ValueError, '报告周期'):
                    convert(self.document, self.root / 'output', 'pdf')
        self.assertFalse((self.root / 'output').exists())

    def test_supported_identity_and_pending_period_export(self):
        self.save(['负责人：测试负责人', '报告周期：待确认'])
        target = convert(self.document, self.root / 'output', 'pdf', [self.source])
        self.assertTrue(target.read_bytes().startswith(b'%PDF'))
        self.assertEqual(Document(self.document).paragraphs[1].text, '报告周期：待确认')

    def test_document_without_metadata_exports(self):
        self.save(['项目进展'])
        target = convert(self.document, self.root / 'output', 'pdf', [self.source])
        self.assertTrue(target.read_bytes().startswith(b'%PDF'))


if __name__ == '__main__':
    unittest.main()
