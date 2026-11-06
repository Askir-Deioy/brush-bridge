# Brush Bridge 阶段状态

> 维护入口：新问题请先登记到 [问题台账](ISSUE_LOG.md)，并按 [反馈记录模板](FEEDBACK_TEMPLATE_ZH.md) 在 `docs/feedback/` 保存原始反馈和证据。本文只维护阶段状态，不替代单次问题记录。

日期：2026-11-02。

这份文件是持续开发的交接入口。自动测试、文件级路线和冻结程序证据以发布目录 `验收记录/` 为准；真实软件导入效果只有在对应软件中实际检查后才改变状态。

## 先回答 0.3.0 的默认笔刷来源

0.3.0 的正常启动入口没有创建或注入默认笔刷：`MainWindow` 只检测本机软件，用户拖入文件后才调用 `add_files()`。发布验收截图里看到的四支“硬圆、软圆、非对称印章、纹理压感”来自测试命令明确传入的 `examples/verification/原创控制笔刷.brushpkg`；冻结检查使用的是 `BrushBridge/BrushBridge.exe --smoke-test <证据目录> <该主存档>`。它们是本项目生成的原创控制样本，不来自 BrushHub，也不代表 BrushHub 或 Procreate 的默认素材。普通双击 `BrushBridge/BrushBridge.exe` 不会自动加载这四支。

## 已完成的两支 SUT 对照

`.cache/sut-compare.json` 对比了 `我爱用的涂抹♥️.sut`（当前写出）和 `我爱用的涂抹♥️好.sut`（参考软件写出）：两者的 1024×1024 笔尖像素哈希相同，`tip_pixels_equal=true`，非零像素数也相同。因此这份证据不支持“笔尖图片被读成实心”的结论。差异集中在参数和目标版本默认字段：当前文件的大小/间距为 387.88 px/1.26%，笔尖顺序为 random；参考文件为 300 px/0.8%，顺序为 sequential；另外还有模糊、速度修正、入出笔、喷雾和旧版 CSP 的隐藏字段差异。后续应把这些字段作为逐字段映射问题继续校准，不能把它们归因于 alpha 丢失。

本轮又补上 alpha 251–254 的边界回归：只要 alpha 不是全 255，就继续按覆盖率读取，避免近透明背景被展平成实心笔尖。进一步审计发现多个目标写出器直接把 RGBA `.convert("L")`，现已统一使用公共 `tip_mask()`；EXP-04 的 alpha 覆盖率在 Photoshop/CSP/优动漫/Procreate/SAI2/画世界 Pro/Krita/GIMP 回读后均保持在约 0.02 灰度误差内。完整测试为 `170 passed, 3 skipped`；跳过项仍是未提供的外部原生样本。测试使用工作区临时目录，避开本机系统临时目录权限限制。

## 朋友实机测试包

已生成 `examples/experiments/`：包含 9 支原创实验笔刷的 `实验笔刷.brushpkg`、Photoshop/CSP/优动漫/Procreate/SAI2/画世界 Pro/Krita/GIMP/MyPaint 的目标文件、逐目标报告、`理想效果.json`、`实验说明.md`、`实验验证.json`、`朋友导入步骤.md` 和可直接填写的 `朋友实机测试记录.csv`。填回结果后可运行 `scripts/validate_field_test_csv.py` 生成汇总并检查漏填。实验覆盖基础间距、尺寸压感、旋转、透明边缘、颗粒、散布、双重笔刷、湿混色和颜色动态。所有目标文件已完成文件级写出与回读；真实软件导入、重启和笔迹仍保持 `not_verified`，等待朋友测试结果。

公开样本回环见 `docs/PUBLIC_ROUNDTRIP.md` 和 `scripts/run_public_roundtrips.py`。当前基线为 28 个公开样本，27 个成功、1 个跳过、0 个失败，共 148 支回读笔刷；81 支有笔尖的样本逐像素哈希相同。它用于先筛查文件级损失，不能替代真实软件笔迹验收。

公开样本跨格式矩阵见 `scripts/run_public_matrix.py`；当前 27 个可用样本已分别导出到 9 个目标格式，全部完成文件级输出，警告和 `losses` 保留在目标报告中。

跨软件字段总览见 `docs/COMPATIBILITY_MATRIX.md`。实验验证器 `scripts/verify_experiments.py` 会重新读取 9 个目标格式，检查名称/数量、尺寸/间距/不透明度容差、理想效果元数据、特性字段、9 个目标报告和所有输出文件是否存在；它不会把真实软件状态自动改成通过。最新 `实验验证.json` 中记录了每个目标的 `readback_count` 和逐支 `field_checks`。

| 阶段 | 状态 | 当前证据 | 仍需完成 |
|---|---|---|---|
| G1 当前缺陷修复 | 已完成首轮 | `.cache/sut-compare.json`；`tests/test_csp.py`；`tests/test_procreate.py`；CSP 新写出隐藏默认值清理；alpha 近透明边界回归 | 持续接收新的原生差分样本，并按字段确认参考软件的真实语义 |
| G2 透明度、资源近似、旧格式回归 | 已完成首轮 | 168 项通过、3 项跳过；多目标 RGBA 覆盖率回归、Procreate/SAI2/画世界用户样本记录；CSP RGBA 笔尖、近透明 alpha、旧双重笔刷、Procreate 负向动态和 MyPaint 附属预览验证 | 更多带透明边缘、颗粒和双重笔刷的原生样本 |
| G3 单 ZIP 可复现发布 | 已完成 | `scripts/make_release.py --force`；当前同版本 ZIP 数量为 1；发布目录内 GUI/CLI 是可直接运行的 onedir 文件夹 | 每次代码发布后重复同一命令；无需额外做 ZIP 解压测试 |
| G4 合法复用与多软件路线 | 进行中 | `docs/REUSE_AUDIT.md`；`docs/COMPATIBILITY_MATRIX.md`；统一 `Brush` 模型；30/30 文件级路线；9 支实验笔刷覆盖 9 个目标格式 | 对可合法再分发的 MIT 组件做小模块级接入评估；补齐复杂参数方向 |
| G5 实机验收与最终交付 | 已准备测试包，验收未完成 | `examples/experiments/` 已提供理想效果和 CSV 记录模板 | Photoshop、CSP/优动漫、SAI2、画世界 Pro、iPad Procreate、Krita、GIMP、MyPaint 的导入、重启、笔迹、压感和混色检查 |

## 已验证的用户样本

- `PROKIT.brushset`：31 支 Procreate 笔刷回写并重新读取。
- `吹石笔刷2023-1.brushset`：10 支 Procreate 读取、Procreate 输出、CSP 输出。
- `海怪基础笔刷-旧版2018.brushset`：19 支 Procreate 读取、Procreate 输出、CSP 输出。
- `罐牌厚涂混色.bru`：11 支画世界 Pro 笔刷读取并输出 CSP。
- SAI2 共享包：19 支工具读取并输出 CSP。

这些是文件级和回读级证据，不替代对应软件中的实际笔迹验收。

## 当前实机条件

当前 `detect_all()` 结果显示电脑端 Photoshop、CSP、SAI2、Krita、GIMP 和 MyPaint 均未自动检测到；Procreate 和画世界 Pro 只显示为移动端/平板文件路线。程序会继续允许文件导出，不把“电脑上未检测到安装”当成转换失败。这个结果解释了为什么 G5 仍保持 `not_verified`，但不阻塞 G1–G4 的文件级开发。

## 下一次继续入口

1. 使用现有 `.venv-win` 运行测试和 `scripts/verify_routes.py`。
2. 如果修改了源码，使用 `.venv-win\Scripts\python.exe scripts/build_exe.py`，再运行 `.venv-win\Scripts\python.exe scripts/verify_build.py --out .cache/frozen-smoke-next`。
3. 使用 `scripts/make_release.py --out ..\\BrushBridge-0.5.0-Windows --force` 覆盖固定发布目录和唯一 ZIP。
4. 新增原生样本时，先保存字段/像素对比，再修改映射；实机导入结果单独记录，不把模拟回读标成兼容。
