# 独立笔刷文件规范 0.1

`.brushpkg` 是本项目的主存档格式，采用 ZIP、UTF-8 JSON 和 PNG/二进制资源；不是绘画软件已经共同支持的行业标准。目标软件仍使用各自适配器导出的文件。

## 结构

- `manifest.json`：`format=brush-bridge-package`、`version=0.1`、必要能力 `typed-values-1`、producer、units、brushes 和 members。
- `brushes/000000.json` 等：笔刷的类型化描述，支持一个包内多支笔刷及重复名称。
- `assets/<sha256>.png`：素材，原始模式和像素尺寸写入引用描述；同内容只存一份。
- `native/<sha256>.bin`：原生描述、原始输入快照及其他字节数据，同内容去重。

`members` 为每个非 manifest 条目记录 size 和 SHA-256；`brushes` 记录笔刷描述的 path、name、uid 和 group。读取时检查全部条目，包括未被笔刷引用的资源。哈希用于完整性检查，不是作者身份认证或数字签名。

公共单位：尺寸 px，角度 degrees，间距相对于笔尖直径的比例，不透明度/流量 0..1，灰度笔尖 255 表示有颜料。颜色图片可原样存档，现有目标适配器可能只使用灰度。

## 类型化值

JSON 原始值保存 null、布尔、字符串、有限数值。复杂值使用 `$type`：

| 类型 | 字段 |
|---|---|
| bytes | ref：native 资源路径 |
| image | ref、mode、size，内容为 PNG |
| list / tuple | items，保留序列类型 |
| dict | items：键值对数组，保留整数键等原始键类型 |
| path / datetime | value，仅作为数据，不自动打开或执行 |
| Brush 等已知数据类 | fields：字段名到类型化值 |

只允许内置白名单：Brush、BrushSource、Dynamic、Texture、DualTip、ColorDynamics、WetMix、EdgeEffect，以及现有 SAI2 Asset。输入文件不能指定任意 Python 类或模块；不使用 pickle。

当前白名单数据类的未知字段及 manifest 的未知可选字段保存后保留。未知必要能力、未知数据类型和其他规范版本明确拒绝；不静默解释为当前版本。

## 模型与来源

Brush 保存名称、分组、笔尖、纹理、双重笔刷、基础属性、颜色变化、混色、湿边、动态，以及 extensions 和 resources。

Dynamic 保留旧适配器使用的 pressure/tilt/velocity/jitter 强度及 pressure_curve，并新增 enabled_inputs、response_curves、output_range、interpolation 和 combination。新增字段在包中完整保存；目标适配器未能执行扩展规则时，转换报告说明近似。Procreate 和画世界 Pro BRU 的原始参数也保存到扩展和原生数据中。

BrushSource 保存来源 app/format/version/index、解析器版本、native 专有数据、snapshots 原始文件字节，以及公共模型 fingerprint。源文件在第一次读取时捕获；重新打开包不需要原始路径仍然存在。

fingerprint 用于检测公共参数和素材修改，不包含来源、告警和预览。公共数据修改后停止回放旧 native 描述，使用现有适配器重新编码，原始数据仍留在主存档中。原始快照可恢复输入，不代表编辑后可以无损改写任意源格式。

resources 可声明 `tip`、`grain` 等资源的 status、name 和 required。主存档允许保留缺失引用；正常导出不允许以默认圆头冒充缺失关键笔尖。可选颗粒只有显式允许忽略时才继续并记录。

## 限制与写入

当前限制：20,000 个条目、单条目 256 MiB、解压总量 1 GiB、单份 JSON 32 MiB、单图 64,000,000 像素、类型化数据深度 64。拒绝不安全路径、重复条目、符号链接、加密包和非有限数值。不将内容解压到用户指定路径。

写入使用目标目录临时文件，完成包回读后再替换。通过应用导出时自动选择不重名路径，不覆盖已有成果。写出某目标时在独立副本上处理，近似不会反向污染主存档。

本格式保存数据，不定义一个所有宿主都能执行的绘画引擎。尤其混色参数只表达公共意图和来源，不能据此保证跨软件笔迹相同。
