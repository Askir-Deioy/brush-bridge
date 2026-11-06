# Brush Bridge 0.5.26

## 修复

- Photoshop ABR 无分组笔刷改用 Photoshop 原生 v6.2 导出的空 `phry` 层级，改善跨 Photoshop 版本读取兼容性。
- 完成提示改为可滚动报告区域，底部操作按钮固定，长批量报告不会遮住退出按钮。

## 验证

- 自动测试：171 passed，3 skipped。
- ABR 对照覆盖问题笔刷、叶子笔刷和海怪笔刷的 Photoshop 重导出样本。
- 真实 Photoshop 跨版本导入仍以用户提供的目标版本和修复后输出文件为准。
