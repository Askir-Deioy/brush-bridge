# CSP 仿 SAI2 取色器与自动联动

版本：0.5.15 开发版。

当前是独立置顶的 CSP 暗色 Mod 风格悬浮取色器，二维区域使用已对齐 SAI2 Ver.2 像素级精度的 V-HSV 模型：

- **精确 V-HSV 模型（`brush_bridge/core/color_models.py`）**：`C(s, v) = s × (1 - 0.5 × s × (1 - v))`，`RGB = clamp(v - C × (1 - P), 0, 1)`，并提供闭式二次方程反算（未截断区域无色相偏移，全图 108×108 像素平均误差 `0.12 / 255`、中位误差 `0 / 255`）。

- **自适应几何与防误触**：外圈粗色环与中央内接二维方块随窗口自由缩放；方块尺寸严格按 `(inner_diameter - 6) / √2` 计算，四角绝不压环；色环仅在圆环区域响应按下，四角空白与内孔缝隙不误触。
- **真正独立的双色板与 8 格存色板**：
  - `DualSwatch`（左上主色 / 右下副色）拥有两个完全独立的 V-HSV 颜色槽位：拖动色轮仅修改当前选中槽位，绝不覆盖另一槽位；点击另一槽位可切换当前编辑颜色，点击右上箭头可互换主副色。
  - `SwatchPaletteBar` 提供 8 个独立的持久化存色格（保存在 `QSettings` 中），支持左键读取已存颜色、点击「＋存色」或右键点击任意格分开保存多个颜色。
- **内嵌 HEX 与可折叠 R/G/B/H/S/V 抽屉**：HEX 可在主面板直接查看、编辑和一键复制；点击「数值」可在面板内展开 R/G/B/H/S/V 精确输入框（整组失焦提交，不抢写）。
- **CSP 4.2 自动传色联动（`brush_bridge/apps/csp_sync.py`）**：
  - 自动从本机 `%APPDATA%\CELSYSUserData\CELSYS\CLIPStudioPaintVer*\Shortcut\default.khc` 读取 CSP「编辑 → 获得屏幕颜色（`applicationscreeneyecolorpicker`）」快捷键（本机已自动识别为 `Ctrl+F`）。
  - 在「自动同步」模式下，松开色轮鼠标或点击色板格时，自动唤醒运行中的 `CLIP STUDIO PAINT` 窗口、触发屏幕取色键并采样当前活动色块中心像素，随后将鼠标归还原位并把焦点留在 CSP 画布，实现接近内部插件的无缝绘画体验。

模型与截图证据见 [2026-11-07 V-HSV 截图差分记录](../../feedback/2026-11-07-VHSV-screenshot-probe.md)。
