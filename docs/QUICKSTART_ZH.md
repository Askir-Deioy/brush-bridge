# Brush Bridge 0.5 快速开始

## 普通使用

作者：askir。本软件完全免费。

如果拿到 GitHub Release 的 `BrushBridge-0.5.0-User.msi`，双击安装即可；这是精简用户版，安装目录根部直接有 `BrushBridge.exe`，并创建开始菜单快捷方式。它只带运行所需文件、一个示例笔刷、简短说明和回填表。完整开发版 MSI 还会带 CLI、实验笔刷和验收资料。便携 ZIP 也直接运行根部的 `BrushBridge.exe`。

打开发布目录 `BrushBridge/BrushBridge.exe`。拖入 ABR、SUT、Procreate 笔刷、画世界 Pro `.bru`、SAI2 工具/资源目录，或 `.brushpkg` 主存档；勾选笔刷，再选择目标文件格式并导出。

建议先导出一次“独立笔刷主存档”，以后从这份文件分别导出各软件版本。主存档不依赖源文件原来的位置，保留来源和素材，方便以后重新转换。

目标列表包含 Photoshop ABR、CSP SUT、优动漫 SUT、Procreate brushset、画世界 Pro BRU、SAI2 工具资源目录。CSP 和优动漫共用 SUT 编解码器，但软件实机验收分开记录。

“查看所选笔刷数据”展示第一支所选笔刷的参数、动态和素材描述。导出前会显示目标能力概要，结果目录同时生成 `.report.json`：逐支查看成功、失败、近似，以及尚未验证的实机状态；机器读取时可使用每支记录的 `losses` 字段筛选缺资源、目标不支持、忽略和近似。

缺少关键笔尖时请补充原图；缺少可选颗粒可勾选“允许忽略缺失颗粒”，结果报告会记录。取消在笔刷之间生效，当前正在编码的单支先完成；已经完成的结果会保留。同名输出自动换名。

Procreate 输出传到 iPad 导入；SAI2 输出目录附带安装说明。文件回读通过不代表绘画软件中已经验证，当前实际兼容情况见验收记录。

Procreate 只引用内置素材而文件本身没有图像时，程序会先查找 `BRUSHHUB_PROCREATE_ASSETS`、`BRUSH_BRIDGE_PROCREATE_LIBRARY`，以及程序旁边已有的 BrushHub 素材目录；仍找不到时，只要公共模型有可用笔尖/纹理就继续输出，并把近似写入报告。原始引用仍保存在 `.brushpkg`。

画世界 Pro 的 `.bru` 可以直接拖入读取，也可以作为目标格式导出。它主要运行在手机/平板，电脑端不会因为找不到安装位置而阻止转换；把生成的 `.bru` 传到设备后，在画世界 Pro 中选择“导入画笔”。复杂纹理、双重笔刷和混色会在报告中说明，完整来源数据仍保存在 `.brushpkg`。

## 命令行

发布包使用 `BrushBridgeCLI/BrushBridgeCLI.exe`，开发环境使用 `.venv-win\Scripts\brush-bridge-cli.exe`：

```powershell
.\BrushBridgeCLI\BrushBridgeCLI.exe formats
.\BrushBridgeCLI\BrushBridgeCLI.exe list "我的笔刷.brushpkg"
.\BrushBridgeCLI\BrushBridgeCLI.exe convert "源笔刷.brushset" --to brushpkg -o "主存档.brushpkg"
.\BrushBridgeCLI\BrushBridgeCLI.exe convert "主存档.brushpkg" --to photoshop -o "PS版.abr"
.\BrushBridgeCLI\BrushBridgeCLI.exe convert "主存档.brushpkg" --to udongman -o "优动漫版.sut"
.\BrushBridgeCLI\BrushBridgeCLI.exe convert "主存档.brushpkg" --to procreate -o "iPad版.brushset"
.\BrushBridgeCLI\BrushBridgeCLI.exe convert "主存档.brushpkg" --to sai2 -o "SAI2版.saitdat"
.\BrushBridgeCLI\BrushBridgeCLI.exe convert "主存档.brushpkg" --to huashijie -o "画世界Pro版.bru"
```

SAI2 生成的是同名目录和工具/素材文件；不是一个所有信息都内嵌的 `.saitdat`。可用 `--only 0,2-4` 选择笔刷，`--ignore-missing-optional` 显式忽略缺少的可选颗粒。退出码：0 全部成功，2 部分成功，1 全部失败或输入错误。

## 开发、验证和构建

使用独立 Python 3.11，创建项目虚拟环境并安装 `.[dev]`。Qt 只需要 `PySide6-Essentials`；无需 WebEngine 等大型附加组件。

```powershell
py -3.11 -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv-win\Scripts\python.exe -m brush_bridge.gui.app
.\.venv-win\Scripts\python.exe -m pytest -q
.\.venv-win\Scripts\python.exe scripts\verify_routes.py --out examples\verification
.\.venv-win\Scripts\python.exe scripts\build_exe.py
```

构建产物为 `dist/BrushBridge/` 与 `dist/BrushBridgeCLI/` 两个可直接运行的目录；不依赖 PyInstaller 临时解压。原创示例和文件级兼容记录在 `examples/verification`，文件规范在 `docs/BRUSHPKG.md`。

实机回填表在 `examples/experiments/实机回填表.xlsx` 和 `实机回填表.csv`，填写说明在同目录的 `实机回填表说明.md`。
