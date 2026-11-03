# Brush Bridge 笔刷桥

作者：askir。本软件完全免费。

> 本地开发版 **0.5.0**：新增独立 `.brushpkg` 主存档、来源保留、逐支转换报告、画世界 Pro BRU、Procreate 内置素材查找和 SAI2 共享包读取。先看 [中文快速开始](docs/QUICKSTART_ZH.md)、[文件规范](docs/BRUSHPKG.md) 和 [实现与验收状态](docs/IMPLEMENTATION_STATUS.md)。下文保留上游功能介绍，其中实机兼容和“无损”描述不代表本地版本已经完成对应验收。

**在 Photoshop、Clip Studio Paint (CSP)、优动漫、Procreate、画世界 Pro、SAI2、Krita、GIMP、MyPaint 之间转换笔刷文件的桌面工具。**
面向画师设计：打开笔刷文件 → 勾选笔刷 → 选择目标格式和导出位置 → 完成转换。

跨软件实机验证用的原创实验笔刷在 [`examples/experiments`](examples/experiments)；每支都附有理想效果、测试步骤和可填写的 CSV 记录，不把文件回读冒充真实软件笔迹验收。

[English](#english) · [下载](https://github.com/CanderFlower/brush-bridge/releases/latest) · MIT License

---

## 能做什么

| 功能 | 说明 |
|---|---|
| 📄 打开外部笔刷文件 | 把 `.abr` `.sut` `.brush` `.brushset` `.bru` `.bundle` `.kpp` `.gbr` `.gih` `.myb` 或 PNG 图片拖进窗口即可 |
| 🚚 批量转换 | 一次勾选多支笔刷，选择目标格式并导出；导出后在目标软件自己的导入入口载入文件 |
| 🔧 修复导入失败 | 例如新版 Photoshop 导出的 ABR v10 在 CSP 里报错，可一键转成通用的 v6.2 格式（无损） |

## 支持情况

| 软件 | 打开文件 | 导出文件 | 导出后如何使用 |
|---|---|---|---|
| Photoshop | ✅ .abr v1/v2/v6/v10 | ✅ .abr v6.2 | 在 Photoshop 的画笔面板中导入 |
| Clip Studio Paint | ✅ .sut | ✅ .sut | 在 CSP 的辅助工具面板中导入 |
| SAI2 | ✅ 工具/资源目录 | ✅ 工具/资源目录 | 按随文件生成的说明复制到 SAI2 设置目录 |
| Procreate（iPad） | ✅ .brush / .brushset | ✅ .brushset | 把文件传到 iPad 后在 Procreate 中打开 |
| 画世界 Pro（手机/平板） | ✅ .bru | ✅ .bru | 把文件传到设备后在画世界 Pro 中导入 |
| Krita | ✅ .bundle / .kpp | ✅ .bundle | 在 Krita 的资源管理器中导入 |
| GIMP | ✅ .gbr / .gih / .vbr | ✅ .gbr | 在 GIMP 的笔刷设置中导入或刷新 |
| MyPaint | ✅ .myb v1/v2/v3 | ✅ .myb v3 / v2 | 把文件放入 MyPaint 笔刷目录后重启或刷新 |
| 图片 | ✅ PNG/JPG 等作为笔尖 | ✅ PNG | 在目标软件中作为笔尖图片使用 |

> 同一软件内的「原样搬运」（例如 Photoshop 画笔库 → .abr 文件 → CSP）会保留原始数据，不损失任何参数。
> 不同软件之间的转换会尽量映射笔尖、间距、压感、散布、纹理、双重画笔、颜色动态、混色等参数；
> 无法对应的效果会在结果里明确提示。转到 CSP 时，Procreate 的湿边/灼边会映射为「水彩边界」，
> 压力/倾斜颜色（色相、饱和度、亮度）映射为笔尖颜色变化的压力/倾斜输入，倾斜渗流/渐变映射为倾斜控制的浓度。
> 转到 CSP 时，自定义笔尖、纸纹和双重画笔会以原始分辨率生成为 CSP 素材，直接内嵌在 .sut 里。

## 不同版本的软件

导出时可以选择目标软件的版本，生成的文件会按该版本能读取的格式写入；默认使用兼容面最广的格式。

| 软件 | 兼容范围 |
|---|---|
| Photoshop | 导出 .abr v6.2，Photoshop 7.0 及以后都能读取；读取 v1/v2/v6/v10 |
| Clip Studio Paint | 默认按 2.x–5.x 通用格式写；可选 1.x 格式（去掉 1.x 不认识的参数） |
| Procreate | 导出 Procreate 5 及以后的格式（Procreate 4 无法导入 5 的笔刷） |
| 画世界 Pro | 读写 BRU v5 结构；复杂字段需在设备中实机核对 |
| Krita | 默认 Krita 5 资源包；可选 Krita 4 及更早（.kpp 2.2） |
| MyPaint | 默认 v3（MyPaint 1.1+、GIMP 2.10+、Krita 5 可读）；可选 v2 给更老的版本 |
| SAI2 / GIMP | 写入各自通用的格式 |

> 已用 600 多个来自不同网站、不同风格的公开笔刷（ABR、SUT、Krita、MyPaint、SAI2、Procreate）做过互转测试，全部能读取；
> 剩余的差异主要是目标格式本身不支持的效果，会在结果里提示。

## 使用方法

1. 从 [Releases](https://github.com/Askir-Deioy/brush-bridge/releases/latest) 下载“简洁用户版” MSI；安装后，安装目录根部的 `BrushBridge.exe` 就是图形界面，开始菜单也有快捷方式。
   也可以下载同一 Release 的便携 ZIP，直接运行根部的 `BrushBridge.exe`（无需安装）。开发版目录仍使用 `BrushBridge/BrushBridge.exe` 和 `BrushBridgeCLI/BrushBridgeCLI.exe`。
2. **① 选择文件**：点击“打开笔刷文件”，或把笔刷文件拖进窗口。
3. **② 勾选笔刷**：可以搜索、按分组筛选、全选。
4. **③ 选择目标格式**：选择目标软件格式或 `.brushpkg` 通用分享格式，点 **开始转换** 并选择导出位置。

**常见问题**

- *软件安装在哪里会影响转换吗？*：不会。转换页只处理笔刷文件，不检查目标软件是否安装；选择目标格式和导出位置即可。需要带到另一台电脑时，可以选择 `.brushpkg` 通用分享包，之后再转换成目标软件格式。
- *CSP 提示「此文件无法添加到工具组面板」*：要把 .abr / .sut 拖到 **辅助工具** 面板，而不是工具面板。
- *CSP 提示无法读取 .abr*：多半是新版 Photoshop 的 v10 格式。到「🔧 修复导入问题」页拖入文件，点「转换为通用的 v6.2 格式」。
- *直接写入软件设置会不会弄坏？* 写入前会自动备份到同目录的 `brush-bridge-backups` 文件夹，并要求先关闭目标软件。
- *Procreate 笔刷提示「使用了 Procreate 内置纹理/形状」*：这类笔刷引用的是 Procreate 自带素材库里的图片，笔刷文件里没有。
  把这些原图（文件名如 `Acrylic-Mix.jpg`、`Brush-Preset-Ink.jpg`）放进 `%LOCALAPPDATA%\BrushBridge\procreate_library`
  文件夹（可含子文件夹），重新转换即可使用原图；没有原图时会用程序生成的同类纹理近似代替。
  这些素材的版权属于 Procreate，本项目不附带。
- *为什么 BrushHub 能转、这里以前失败？*：部分 Procreate 笔刷只引用应用内置素材。0.5.0 会自动查找本机已有的 `BrushHub-1.0.0\brushhub\assets\procreate-builtin`，也支持 `BRUSHHUB_PROCREATE_ASSETS` 环境变量；找不到原图但公共模型有可用笔尖/纹理时会继续转换，并在报告标明近似。
- *SAI2 共享包没有显示完整笔刷*：解压后如果 `.saitdat` 直接在包根目录、旁边有 `brushfom` / `brushtex`，可以直接把这个文件夹拖入；程序会把工具参数和引用素材一起读取。
- *0.3.0 打开后看到的“默认笔刷”从哪里来？*：它们不是下载的商业笔刷，也不是 BrushHub 的素材。发布包里的 `示例笔刷/原创控制笔刷.brushpkg` 由 `scripts/verify_routes.py` 生成，固定包含“硬圆、软圆、非对称印章、纹理压感”四支原创验收样本；“复杂效果示例”是其中一支的单独副本。

## 从源码运行 / 开发

```bash
git clone https://github.com/CanderFlower/brush-bridge
cd brush-bridge
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
brush-bridge            # 图形界面
brush-bridge-cli convert <笔刷文件> --to csp -o <导出文件.sut>
python -m pytest        # 测试
python scripts/build_exe.py   # 打包 dist/BrushBridge/ 与 dist/BrushBridgeCLI/
python scripts/make_release.py --out ..\\BrushBridge-0.5.0-Windows --force  # 只生成一个对应的 ZIP
```

架构：`brush_bridge/core/model.py` 定义与软件无关的笔刷模型；`formats/` 每种文件格式一个编解码器，`apps/` 保存软件相关的读取和兼容性处理。新增软件只需添加一个插件文件。各格式的逆向笔记见 [docs/formats](docs/formats)。

欢迎提交 Issue 附上无法导入的笔刷文件（请确认你有权分享）。

## 许可

MIT。本仓库不包含任何商业笔刷；测试用的公开笔刷在测试时下载，来源与许可见 [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md)。
Photoshop、Clip Studio Paint、Procreate、PaintTool SAI、Krita、GIMP、MyPaint 是其各自所有者的商标，本项目与它们无关。

---

## English

**Brush Bridge** is a friendly desktop app that converts brush files between Photoshop, Clip Studio Paint, Procreate, PaintTool SAI 2, Krita, GIMP and MyPaint.

- Reads brush files directly without requiring the target painting app to be installed.
- Converts to a selected target format or the portable `.brushpkg` sharing format.
- Opens external brush files (`.abr`, `.sut`, `.brush`, `.brushset`, `.bundle`, `.kpp`, `.gbr`, `.gih`, `.myb`, images) by drag & drop.
- Writes files for a chosen target app version (e.g. CSP 1.x / 2.x–5.x, Krita 4 / 5, MyPaint v2 / v3).
- Lets the user choose an output path and writes a file ready to import into the target app.
- Diagnoses files that an app refuses to import and repairs them (e.g. Photoshop 2020+ ABR v10 → v6.2 for Clip Studio Paint).
- Procreate brushes that reference Procreate's built-in library use your own copies of those images from `%LOCALAPPDATA%\BrushBridge\procreate_library` (not shipped; procedural look-alikes otherwise).

Download the compact user MSI or ZIP from Releases, or run from source with `pip install -e .` then `brush-bridge`. MIT licensed.
