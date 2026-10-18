# Slides MCP 创作

本指南用于已配置的本地 `slides` MCP 服务。没有该 MCP 时使用 pptx.md 的普通文件路径，并明确说明未走 Slides 工作流。

1. 调用 `init_slides`，显式传 `generate_mode="pptx"`、标题和完整 outline，每页有唯一 `id`、`title`、`summary`。保存返回的 `project_id` 和 `project_dir`。重复 init 会创建独立项目；增删重排使用 `organize_slides`。
2. 用文件工具编辑 `project_dir/design.xml` 和返回的每个 `<id>.xml`。可将用户提供的本地图片复制进项目 assets 目录，再在 XML 中引用其绝对路径。项目 JSON 由工具维护。
3. 内容写完后用 `write_notes` 写入备注。`slide_id` 或从 1 开始的 `slide_number` 定位页面；空 note 清空备注。
4. 调用 `present`。缺页、无效 XML、未知属性、越界元素都会失败，按错误修正后重试。成功返回版本 ID、PPTX、PDF 和 HTML 预览路径。逐页查看生成的 PNG，检查中文、留白、遮挡和截断；需要调整则编辑源文件并再次 present。
5. 用户需要文件时调用 `export(project_id="实际ID", format="pptx"/"pdf")`，通常省略 `output_dir`，直接使用返回的路径。若指定 output_dir，只能是该 project_dir 内的绝对目录。文件和 HTML 预览已位于 `/mnt/user-data/outputs`，无需再次复制。备注会写进 PPTX；修改备注或内容后必须重新 present。历史 version_id 可导出旧文件，不覆盖旧版本。
6. `attach_slides(resource_uri)` 使用 `oma-slides://deck/<project_id>` 恢复当前 Session 的已有项目。它不接受 Manus URI，不代表跨 Session 共享权限。Sandbox 重建后可从该 Session 的持久化输出恢复源文件与项目状态。

本地只提供 `deck-stage-basic` 主题，可用 `get_template` 查看；它不是 Manus 官方模板库。不提供 AI `generate_image_slide` 或 HTML/image 创作模式。`style_request` 是交给创作 Agent 的设计要求，服务不自动调用远程模型生成设计。使用 XML 支持的颜色、字体和布局实现样式。

## XML 子集

默认画布为 1280×720。`design.xml` 定义 size、color、font，变量使用名字引用。缺省主题为深蓝、青绿和 Noto CJK 字体。可编辑设计文件改变配色与字体。

```xml
<slide id="cover">
  <background color="bg-page"/>
  <text x="60" y="70" width="1160" height="90" font="heading" color="text-main">中文演示标题</text>
  <shape type="rect" x="60" y="210" width="1160" height="300" fill="bg-card"/>
  <text x="90" y="240" width="1100" height="240" font="body" color="text-main">
    <p>一页聚焦一个观点</p>
    <p space-before="20"><strong>结论</strong>与来源保持一致</p>
  </text>
</slide>
```

每页恰有一个 background，其他元素按写入顺序堆叠。位置、尺寸为像素；字体 size 为像素。颜色为已定义 token 或 `#RRGGBB`。普通元素必须位于画布内且有正宽高，直线允许零宽或零高及反向端点。ID 在页内唯一。

| 元素 | 支持范围 |
| --- | --- |
| text | 普通文本或 p/ul/ol/li，行内 strong/em/u/span；font/size/color/align/anchor/wrap/autofit/line-height/padding，段前后距离 |
| shape | rect、ellipse、arrow、chevron、diamond、triangle、hexagon、直线 line；fill、stroke、stroke-width，直线端点箭头 |
| image | 项目内本地 PNG/JPEG/WebP 等 Pillow 支持的图片，绝对 src；fit 为 cover/contain/stretch |
| icon | circle-check；其他图标先使用本地图片 |
| table | 等列数的 tr/td，columns 列宽比例、fill/header-fill 和文本样式；不支持合并单元格 |
| chart | bar/column/line/area/pie/donut，categories、series name/values/color、图例和数据标签；不支持组合图和散点图 |

共通属性为 id/x/y/width/height/rotation。表格单元格支持普通文本及上述行内/段落标签。图表是可编辑原生图表，数据必须有来源。高级阴影、渐变、裁剪参数、超链接、复杂嵌套列表和未列出的 Manus 扩展不受支持；服务会返回错误，不会静默忽略它们。

用户要求对比图表时保留统计图；数据表可以作为补充，不能用数据表替换失败的图表。柱状对比图的完整 XML 语法如下，类别与数据均为逗号分隔属性，每个 series 的 values 数量与 categories 相同：

```xml
<chart type="column" x="60" y="200" width="1160" height="400" font="body" text-color="text-main" categories="甲组,乙组" legend="bottom" data-labels="false">
  <series name="预算" values="120,80" color="#38BDF8"/>
  <series name="实际" values="110,90" color="#14B8A6"/>
</chart>
```

将示例类别、数值替换为输入中的真实汇总。legend 可取 none/top/bottom/left/right，data-labels 才是 true/false。表格列宽使用 `columns="1,2,2"` 这类正数比例，个数与单元格列数相同，不使用百分号、px或columns-width。图片放在 `project_dir/assets/文件名.png`，src 写完整绝对路径，文件名与大小写必须一致，项目根目录的额外图片不会进入版本快照。交付前用 python-pptx 检查 shape.has_chart、演讲备注正文和嵌入图片，再查看每页预览。

密集的双系列对比图优先保留坐标轴、图例与合计，关闭数据标签，避免相邻数字重叠。图表与合计已满足任务时，无需再添加长明细表。表格文字颜色使用 color（不是 text-color），缺省继承主题 text-main；表格高度至少能容纳每行的字号、行距与内边距，否则 present 会拒绝交付。优先使用部门汇总表，或调整字号与布局；不要删除校验来交付被截断的内容。

`edited` 表示页面文件存在，不代表格式和视觉质量通过；以 present 校验、真实渲染图和下载文件为准。文件写回存储是异步的，工具完成不等于公开 Files catalog 已可见。
