# 办公图表

已安装 toolkit 镜像并配置 workbench MCP 时，可使用 `diagram_render(engine, input_path, output_path)`，engine 为 graphviz、mermaid、d2、plantuml 或 typst。输入放 `/mnt/user-data`，输出放 outputs；前四类支持 PNG/SVG，Typst 还支持 PDF。也可调用 `oma-tools render ENGINE INPUT OUTPUT`。依赖是否存在以 tools_status 和真实调用为准。

流程、依赖和组织关系用 Graphviz；定量图表用 Excel 图表。中文 Graphviz 节点设置 `fontname="Noto Sans CJK SC"`，输出 SVG 用于可缩放展示，PNG 用于 Word/PPT 嵌入。SVG 只有渲染成图片后才检查视觉；成功返回不代表没有标签截断。

```dot
digraph workflow {
  rankdir=LR;
  node [shape=box, fontname="Noto Sans CJK SC"];
  edge [fontname="Noto Sans CJK SC"];
  "需求" -> "分析" -> "交付";
}
```

执行 `dot -Tpng /workspace/task/flow.dot -o /mnt/user-data/outputs/流程.png`。保留 DOT 源文件供编辑。D2、PlantUML、Mermaid 是另外的语法，不能直接喂给 dot；需要用户指定语法时先检测对应 CLI，缺失则报告并由管理员安装，或征得用户同意后转换。此基础包不提供远程 AI 图片、视频、音乐或 3D 生成。
