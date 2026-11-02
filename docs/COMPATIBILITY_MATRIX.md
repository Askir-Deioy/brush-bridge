# Brush Bridge 跨软件字段兼容矩阵

日期：2026-11-02。这个矩阵描述当前代码和文件级回归能证明的范围，不把“文件能打开”写成“目标软件笔迹相同”。

状态含义：

- `F`：文件级写出、回读或已有控制路线覆盖；仍需真实软件笔迹验收。
- `A`：已有字段，但目标格式或引擎只保存近似；结果报告应保留 warning/losses。
- `N`：当前目标格式没有等价表达，或只保留在 `.brushpkg` 主存档。
- `U`：真实软件导入、重启、压力和笔迹尚未验证。

| 能力 | Photoshop ABR | CSP SUT | 优动漫 SUT | Procreate | SAI2 | 画世界 Pro | Krita | GIMP | MyPaint |
|---|---|---|---|---|---|---|---|---|---|
| 单笔尖、尺寸、间距 | F/U | F/U | F/U | F/U | F/U | F/U | F/U | F/U | F/U |
| RGBA/透明边缘覆盖率 | F/U（转灰度遮罩） | F/U | F/U | F/U | F/U（转灰度遮罩） | F/U（转灰度遮罩） | F/U（转灰度遮罩） | F/U（转灰度遮罩） | A/U（无独立笔尖） |
| 尺寸/不透明度压感 | F/U | F/U | F/U | F/U | F/U（笔压） | A/U | F/U | A/U | F/U |
| 旋转、跟线、角度抖动 | F/U | F/U | F/U | F/U | F/U | A/U | F/U | A/U | F/U |
| 散布、点数、随机 | F/U | F/U | F/U | A/U | F/U | A/U | A/U | F/U（GIH） | A/U |
| 纹理/颗粒 | F/U | F/U | F/U | F/U | F/U | A/U（主笔尖优先） | F/U | A/U | A/U |
| 双重笔刷 | F/U | F/U | F/U | A/U | N/U | N/U | A/U | A/U（管线近似） | A/U |
| 湿混色/湿边 | A/U | A/U | F/U | F/U | A/U | A/U | A/U | N/U | A/U |
| 颜色动态 | F/U | A/U | A/U | F/U | F/U（散布颜色抖动） | A/U | A/U | A/U | A/U |
| 多笔尖顺序/输入轴 | A/U（单笔尖限制） | F/U | F/U | A/U | N/U | A/U | A/U | F/U（GIH） | A/U |

证据入口：

- 基础 30 路线：`examples/verification/compatibility.json`。
- 朋友实验包：`examples/experiments/实验验证.json`、各目标目录的 `.report.json`。
- 逐支损失：导出报告中的 `warnings` 和 `losses` 字段。
- 格式细节：`docs/formats/abr.md`、`csp.md`、`sai2.md`、`krita.md`、`gimp.md`。

矩阵的下一步不是把所有 `A` 改成 `F`，而是为每个 `A` 收集同一支原生笔刷的字段、资源和实际笔迹对照；如果目标引擎没有等价能力，就维持 `A/N`，并在主存档中保留可重新转换的原始数据。
