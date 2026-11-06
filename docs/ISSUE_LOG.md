# 问题台账

最后整理：2026-11-06。状态只在有证据时更新：`open`、`investigating`、`fixed_pending_real_app`、`verified`、`wont_fix`。

| 编号 | 状态 | 问题 | 当前证据 | 下一步 |
|---|---|---|---|---|
| BB-001 | fixed_pending_real_app | Photoshop ABR 兼容性：原生导出与 Procreate 往返后的 ABR 在用户机器上出现无法导入/版本不兼容 | 已定位并修复无分组 `phry` 层级差异。兼容输出的 `phry` 从 91/68 归一为原生风格的 43 字节，`samp`、`patt`、基础参数和笔尖像素保持不变；用户确认原文件均可导入 Photoshop。正确的海怪配对 `海怪测试`/`海怪ps导出测试` 除 UUID 外结构一致 | 在其他人的 Photoshop 版本中导入修复后的两个兼容输出，记录版本和结果；海怪作为成功对照样本保留 |
| BB-002 | fixed_pending_real_app | 完成提示内容过长，遮挡底部退出按钮 | 已将结果提示改为独立可滚动报告区，底部按钮固定；GUI 回归 8 passed | 在 Windows 冻结 GUI 中用长批量报告确认窗口缩放和按钮可操作 |
| BB-003 | investigating | 多素材/固定颜色笔刷（类似 `candy brush.sut`）跨格式表达不清，可能被压成单一笔尖或近似 | `candy brush.sut` 读取为 11 个 `L` 笔尖、随机顺序；`.brushpkg`/CSP/Krita/GIMP 保留 11 个；ABR/Procreate/SAI2/画世界 Pro/MyPaint 回读为 1/1/1/1/0 个；当前 CSP 读取路径通过 `thumbnail_to_tip()` 丢弃素材颜色 | 先在主存档中保留多素材和颜色通道，再按目标能力报告明确降级；补颜色感知的受控样本和报告损失测试 |
| BB-004 | open | 反馈、修复和交接记录没有持续更新，导致状态口径分散 | L0 用户反馈；历史文件与仓库 docs 重复 | 使用 `docs/feedback/` + 本台账；每次修复后更新状态、证据和交接入口 |
| BB-005 | deferred | CC BY-NC 许可路线 | 用户明确说“以后”，当前不作为本轮发布许可变更 | 另开许可决策记录，先不把受限代码或素材放入 MIT 发布包 |

## 记录规则

问题条目只描述一个可验证问题。用户原话、截图和商业样本不改写成结论；结论必须链接到测试、哈希、日志或真实软件记录。关闭问题时写关闭依据和仍未覆盖的边界。
