import importlib.util
from pathlib import Path
import tempfile
import unittest

from docx import Document


spec = importlib.util.spec_from_file_location('check_metadata', Path(__file__).parent / 'skills/oma-office/scripts/check_metadata.py')
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)


class MetadataTests(unittest.TestCase):
    def inspect(self, lines, source):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = Document()
            for line in lines:
                document.add_paragraph(line)
            document.save(root / 'report.docx')
            (root / 'source.md').write_text(source)
            return metadata.inspect(root / 'report.docx', [root / 'source.md'])

    def test_invented_period_and_author_are_rejected(self):
        result = self.inspect(['报告周期：2026年10月13日至2026年10月18日', '编制：项目办公室'], '负责人：测试负责人。\n周期：2026年10月1日至10月31日。')
        self.assertEqual({row['field'] for row in result['unsupported']}, {'报告周期', '编制'})

    def test_source_fields_and_pending_period_are_accepted(self):
        result = self.inspect(['项目负责人：测试负责人', '报告周期：待确认', '报告生成日期：2026年10月18日'], '负责人：测试负责人。')
        self.assertEqual(result['unsupported'], [])
        self.assertEqual(result['checked_fields'], 1)

    def test_explicit_period_accepts_equivalent_date_format(self):
        result = self.inspect(['报告周期：2026-10-01至2026-10-07'], '报告周期：2026年10月1日至10月7日。')
        self.assertEqual(result['unsupported'], [])


if __name__ == '__main__':
    unittest.main()
