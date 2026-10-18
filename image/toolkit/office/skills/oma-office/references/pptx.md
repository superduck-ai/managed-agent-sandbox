# PowerPoint

用 `python-pptx` 创建和编辑可编辑 PPTX。读取既有文件时检查幻灯片尺寸、形状位置、文本、表格、图片、颜色及 `slide.notes_slide.notes_text_frame.text` 备注；图片用 `shape.image.blob` 提取。以现有模板为基础编辑可减少风格丢失。

```python
from pptx import Presentation
from pptx.util import Inches

deck = Presentation()
deck.slide_width = Inches(13.333)
deck.slide_height = Inches(7.5)
slide = deck.slides.add_slide(deck.slide_layouts[1])
slide.shapes.title.text = '季度进展'
slide.placeholders[1].text = '填写有证据的进展与下一步'
for shape in slide.shapes:
    if shape.has_text_frame:
        for paragraph in shape.text_frame.paragraphs:
            for run in paragraph.runs:
                run.font.name = 'Noto Sans CJK SC'
slide.notes_slide.notes_text_frame.text = '讲解重点与数据来源'
deck.save('/mnt/user-data/outputs/季度进展.pptx')
```

每页一个主要观点，文字、图片和图表保留可编辑性。用 render.py 导出 PDF、pdftoppm 逐页预览，核对页数、中文、字号、元素边界和备注内容。标题、母版和文本框可能重叠，XML 可读不代表视觉正确。LibreOffice 与 PowerPoint 对复杂字体、动画、SmartArt 和母版渲染可能不同，只有实际检查过的部分才能宣称兼容。

此环境创建本地 PPTX/PDF，不接受 `manus-slides://` URI。Manus 平台幻灯片需先在原服务导出真实文件。
