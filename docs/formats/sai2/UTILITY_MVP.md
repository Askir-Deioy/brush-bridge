# SAI2 便捷工具 MVP

版本：0.5.5 开发版。

GUI 的“便捷工具”页支持用户选择 `sai2.exe`，读取对应设置目录的本地工具，安装选中的笔刷，并导出单支笔刷。

- 不扫描全盘，路径由用户选择并保存在用户设置中。
- 安装复用 `Sai2Adapter.install()`，会按 SAI2 运行状态提示重启或关闭软件。
- 导出复用 `sai2` codec 的 `prefer_native=True`，源笔刷未修改时优先保留原生 sections、BMP 和 INI。
- 当前使用用户提供的 `F:\画画\SAI\[Saier]SAI Ver.2\sai2.exe` 已能识别为 Saier 2020-06-17 构建，并找到文档设置目录。

真实安装和启动验收仍由用户在 SAI2 中确认，测试代码只使用临时设置目录。
