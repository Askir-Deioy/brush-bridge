# Brush Bridge 0.5.3

## 修复

- Photoshop ABR 无分组笔刷使用 Photoshop 原生 v6.2 导出的空 `phry` 层级。
- 完成提示改为可滚动报告区域，底部操作按钮固定。
- 本机开发预览目录 `build\BrushBridge` 会自动同步 `_internal` 运行时，可以在正式打包前直接打开。
- CSP 多素材笔刷同时保存灰度遮罩和彩色素材，`.brushpkg` 和 CSP 回写保留彩色素材。
- 多素材降级会在报告 `losses` 中标记为 approximation。

## 验证

- CSP、BrushPkg、ABR 相关测试通过。
- 两份彩色多素材 SUT 完成 `.sut → .brushpkg → .sut` 文件级素材数量和 RGBA 像素回环。
- 冻结 GUI 预览启动成功。
