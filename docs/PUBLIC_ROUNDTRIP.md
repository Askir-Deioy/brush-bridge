# 公开笔刷回环验证

这条路线用于文件级筛查：公开样本 → 读取 → 独立 `.brushpkg` → 写回原格式 → 重新读取。它能发现数量/名称变化、公共参数变化、笔尖覆盖率变化、纹理/双重笔刷缺失和转换报告遗漏；它不能证明 Photoshop、CSP、Procreate 等真实软件的渲染笔迹相同。

样本来源和许可记录在 `scripts/fetch_samples.py` 与 `docs/THIRD_PARTY.md`，下载文件只放在 git-ignored 的 `tests/fixtures/downloaded/`，不进入发布包。当前已接入 Procreate、Krita、GIMP 的公开样本。

运行：

```powershell
.\.venv-win\Scripts\python.exe scripts\run_public_roundtrips.py
```

离线、只使用已经下载的样本：

```powershell
.\.venv-win\Scripts\python.exe scripts\run_public_roundtrips.py --no-fetch
```

结果写到 `.cache/public-roundtrips/public-roundtrips.json`。当前基线为 28 个样本：27 个成功、1 个跳过（Krita `test1.bundle` 没有可保存的笔刷）、0 个失败，共 148 支回读笔刷；其中 81 支有笔尖，81 支的源/回写笔尖灰度像素哈希完全相同，所有有笔尖样本的覆盖率均无平均值差异。报告中的 `tip_pixels_equal`、`tip_sha256` 和 `tip_mean_absolute_difference` 只描述文件回读，不代表真实软件渲染兼容；`real_app_import` 和 `stroke_fidelity` 永远保持 `not_verified`。

跨格式批量筛查使用：

```powershell
.\.venv-win\Scripts\python.exe scripts\run_public_matrix.py --no-fetch --max-brushes 3
```

它从每个公开文件最多取 3 支笔刷，导出到 Photoshop、CSP、优动漫、Procreate、SAI2、画世界 Pro、Krita、GIMP、MyPaint，并记录每个目标的成功数量、警告和损失。当前基线为 28 个样本：27 个成功、1 个跳过、0 个失败；9 个目标格式对 27 个样本均完成文件级输出，损失集中记录在目标报告中。结果写到 `.cache/public-matrix/public-matrix.json`。

公开矩阵还会检查回读后的名称、尺寸、间距和不透明度。MyPaint 的 JSON `description` 会用于恢复笔刷名称；无法表达的极小间距会保留为目标格式下限，并在报告中标出近似。
