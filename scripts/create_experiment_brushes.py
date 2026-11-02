"""Create deterministic cross-software brush experiments for real-app testing.

The generated package contains original procedural tips/textures only.  Each brush
stores a human-readable ideal effect in ``Brush.extensions['experiment']`` and the
folder also receives target-format exports plus a test sheet.  These files are
deliberately small and are meant to be handed to artists for real import/stroke
checks; file round-trips alone do not change the recorded app status.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brush_bridge.core.model import Brush, ColorDynamics, Dynamic, DualTip, EdgeEffect, Texture, WetMix
from brush_bridge.core.plugin import codec, load_plugins
from brush_bridge.core.reporting import export_detailed
from brush_bridge.formats.brushpkg import write_package


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "examples" / "experiments"


def _meta(code: str, ideal: str, steps: str, features: list[str]) -> dict:
    return {"id": code, "ideal_effect": ideal, "test_steps": steps, "features": features,
            "status": "not_verified"}


def _soft_circle(size: int = 128, hardness: float = 0.35) -> Image.Image:
    y, x = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    r = size / 2
    d = np.sqrt((x - r) ** 2 + (y - r) ** 2) / r
    edge = np.clip((1 - d) / max(1 - hardness, 0.02), 0, 1)
    edge = edge * edge * (3 - 2 * edge)
    return Image.fromarray((edge * 255).astype(np.uint8), "L")


def _triangle(size: int = 160) -> Image.Image:
    im = Image.new("L", (size, size), 0)
    ImageDraw.Draw(im).polygon([(size * 0.08, size * 0.18), (size * 0.9, size * 0.42),
                                (size * 0.25, size * 0.9)], fill=255)
    return im


def _cross(size: int = 96) -> Image.Image:
    im = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(im)
    w = size // 5
    d.rectangle((size // 2 - w // 2, size // 8, size // 2 + w // 2, size * 7 // 8), fill=255)
    d.rectangle((size // 8, size // 2 - w // 2, size * 7 // 8, size // 2 + w // 2), fill=255)
    return im.filter(ImageFilter.GaussianBlur(1.2))


def _dot(size: int = 64) -> Image.Image:
    im = Image.new("L", (size, size), 0)
    ImageDraw.Draw(im).ellipse((size * .22, size * .22, size * .78, size * .78), fill=255)
    return im


def _grain(size: int = 128) -> Image.Image:
    y, x = np.mgrid[0:size, 0:size]
    wave = 0.5 + 0.5 * np.sin(x / 4.5 + np.sin(y / 15) * 2.2)
    fine = ((x * 17 + y * 31) % 29) / 29
    a = np.clip(wave * .72 + fine * .28, 0, 1)
    return Image.fromarray((a * 255).astype(np.uint8), "L")


def _transparent_stamp(size: int = 160) -> Image.Image:
    y, x = np.mgrid[0:size, 0:size].astype(np.float32) + .5
    r = size / 2
    d = np.sqrt((x - r) ** 2 + (y - r) ** 2) / r
    alpha = np.clip((.95 - d) / .18, 0, 1)
    alpha *= (0.78 + 0.22 * np.sin(x / 8) * np.sin(y / 11))
    rgba = np.zeros((size, size, 4), np.uint8)
    rgba[:, :, :3] = 255
    rgba[:, :, 3] = np.clip(alpha * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def experiments() -> list[Brush]:
    out: list[Brush] = []

    b = Brush("EXP-01 硬圆基础", size_px=40, spacing=.12)
    b.extensions["experiment"] = _meta("EXP-01", "点按是完整圆形，连续直线没有明显断点或额外散布。",
        "画 5 个单点，再画慢速直线和快速直线；观察圆形、间距和速度是否稳定。", ["tip", "spacing"])
    out.append(b)

    b = Brush("EXP-02 软边尺寸压感", tips=[_soft_circle()], size_px=60, spacing=.18,
              size_dynamic=Dynamic(pressure=.65))
    b.extensions["experiment"] = _meta("EXP-02", "轻压笔尖较小，重压笔尖变大；边缘始终是柔和渐变。",
        "用轻、中、重三档压力画平行线；比较线宽变化和边缘灰度，不要只看缩略图。", ["tip", "size_pressure"])
    out.append(b)

    b = Brush("EXP-03 三角跟随旋转", tips=[_triangle()], size_px=64, spacing=.25, angle=15,
              angle_follow_stroke=True)
    b.extensions["experiment"] = _meta("EXP-03", "三角笔尖沿笔画方向旋转，转弯处方向连续，不突然跳回固定角度。",
        "画水平、垂直、顺时针圆弧和逆时针圆弧；记录尖角方向是否跟随笔画。", ["tip", "rotation"])
    out.append(b)

    b = Brush("EXP-04 透明边缘", tips=[_transparent_stamp()], size_px=72, spacing=.2, opacity=.9)
    b.extensions["experiment"] = _meta("EXP-04", "笔尖外圈逐渐透明，没有白色或黑色实心方框；叠画时中心逐渐变浓。",
        "在白底、灰底、深色底各画点和短线；检查透明边缘、叠加次序和是否出现方形背景。", ["rgba_tip", "alpha"])
    out.append(b)

    b = Brush("EXP-05 颗粒固定间距", tips=[_soft_circle(128, .7)], size_px=58, spacing=.2,
              texture=Texture(_grain(), name="实验波纹颗粒", scale=.65, depth=.7,
                              depth_dynamic=Dynamic(pressure=.35)))
    b.extensions["experiment"] = _meta("EXP-05", "笔尖轮廓保持稳定，颗粒在笔画中连续平铺；轻压时颗粒较淡，重压时更明显。",
        "画慢速直线、快速直线、交叉线；分别观察颗粒是否平铺、是否随压力改变深度。", ["tip", "texture", "texture_pressure"])
    out.append(b)

    b = Brush("EXP-06 散布随机", tips=[_dot()], size_px=36, spacing=.16, scatter=.85,
              scatter_both_axes=True, count=3, count_jitter=.45, angle_jitter=.2, tip_order="random")
    b.extensions["experiment"] = _meta("EXP-06", "主路径仍可辨认，但笔点在两侧随机散开，点数和角度有轻微变化。",
        "画慢速直线和曲线；记录散布范围、点数、随机性以及重启后是否仍是同一规则。", ["scatter", "count", "random"])
    out.append(b)

    b = Brush("EXP-07 双重叠加", tips=[_soft_circle(128, .8)], size_px=54, spacing=.18,
              dual=DualTip(_cross(), size_px=24, spacing=.3, scatter=.08, mode="multiply"))
    b.extensions["experiment"] = _meta("EXP-07", "主笔尖形成柔和圆形，双重十字只在叠加处改变密度，不应替换主笔尖。",
        "画单点、慢线和快速线；比较关闭/开启双重笔刷时的轮廓、密度和双重间距。", ["dual_tip", "multiply"])
    out.append(b)

    b = Brush("EXP-08 湿混色", tips=[_soft_circle(128, .45)], size_px=48, spacing=.14,
              wet=WetMix(enabled=True, mix=.6, load=.65, wetness=.7, color_stretch=.4),
              edge=EdgeEffect(opacity=.2, darken=.15, width_px=3))
    b.extensions["experiment"] = _meta("EXP-08", "在已有蓝色和黄色底色上拖动时拾取画布颜色，交叠处产生连续混色；边缘略有聚色。",
        "先铺蓝色和黄色，再用同一支笔跨过两色交界；记录是否拾色、混色方向、载荷衰减和边缘聚色。", ["wet_mix", "edge"])
    out.append(b)

    b = Brush("EXP-09 颜色压力动态", tips=[_soft_circle(128, .6)], size_px=46, spacing=.16,
              color=ColorDynamics(hue_pressure=.18, saturation_pressure=.22, brightness_pressure=.12))
    b.extensions["experiment"] = _meta("EXP-09", "轻压与重压的色相、饱和度或亮度出现可重复变化；没有颜色动态的目标应明确报告忽略。",
        "选固定前景色，用轻、中、重压力画三条线；记录颜色是否变化、变化方向和重启后是否一致。", ["color_pressure"])
    out.append(b)
    return out


def _write_test_sheet(brushes: list[Brush], path: Path) -> None:
    fields = ["experiment_id", "brush_name", "ideal_effect", "test_steps", "app", "imported",
              "restarted_and_kept", "dot", "line", "curve", "pressure", "texture_or_mix", "notes"]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for b in brushes:
            m = b.extensions["experiment"]
            for app in ("Photoshop", "CSP", "优动漫", "Procreate", "SAI2", "画世界 Pro", "Krita", "GIMP", "MyPaint"):
                w.writerow({"experiment_id": m["id"], "brush_name": b.name, "ideal_effect": m["ideal_effect"],
                            "test_steps": m["test_steps"], "app": app})


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_plugins()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    brushes = experiments()
    package = OUT / "实验笔刷.brushpkg"
    write_package(brushes, package)
    manifest = {"title": "Brush Bridge 跨软件实验笔刷", "version": "1", "brushes": [b.extensions["experiment"] | {"name": b.name} for b in brushes],
                "source": "original-procedural", "app_status": "not_verified"}
    (OUT / "理想效果.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_test_sheet(brushes, OUT / "朋友实机测试记录.csv")
    lines = ["# Brush Bridge 跨软件实验笔刷", "", "这套笔刷只使用本项目生成的形状和颗粒，不包含商业或第三方素材。",
             "请把 `实验笔刷.brushpkg` 作为主存档，也可以直接测试各目标软件目录中的文件。", "",
             "每支笔刷的理想效果和操作步骤见 `理想效果.json`；请在 `朋友实机测试记录.csv` 中按软件填写。",
             "结果请区分：导入成功、重启后保留、点/线/曲线、压力、纹理/混色。文件能打开不等于笔迹达到理想效果。填回后可运行 `scripts/validate_field_test_csv.py` 检查漏填和矛盾。", ""]
    for b in brushes:
        m = b.extensions["experiment"]
        lines += [f"## {m['id']} {b.name}", f"理想效果：{m['ideal_effect']}", f"测试步骤：{m['test_steps']}",
                  f"覆盖特性：{'、'.join(m['features'])}", ""]
    (OUT / "实验说明.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "朋友导入步骤.md").write_text(
        "# 朋友实机导入步骤\n\n"
        "先保存 `实验笔刷.brushpkg`，它是完整主存档；下面的目标文件用于在对应软件中做真实导入和笔迹测试。\n\n"
        "## Photoshop\n\n"
        "使用 `photoshop/实验笔刷.abr`，在 Photoshop 的画笔面板导入。重启 Photoshop 后再画测试线。\n\n"
        "## CSP / 优动漫\n\n"
        "使用 `csp/` 或 `udongman/` 目录内的 9 个 `.sut`，拖入对应软件的“子工具”面板，不要拖到工具面板。导入后重启软件，再测试点、线、曲线和压力。\n\n"
        "## Procreate\n\n"
        "把 `procreate/实验笔刷.brushset` 传到 iPad，在 Procreate 中打开。导入后关闭并重新打开 Procreate，检查笔刷是否保留。\n\n"
        "## SAI2\n\n"
        "先关闭 SAI2，再按 `sai2/实验笔刷/安装说明.txt` 合并 `brushfom`、`brushtex`、`scatter`、`custool`。重新启动 SAI2 后测试；不要在软件运行时覆盖工具设置。\n\n"
        "## 画世界 Pro\n\n"
        "把 `huashijie/实验笔刷.bru` 传到手机或平板，在画世界 Pro 中选择导入画笔。记录设备型号和应用版本，重启应用后再画。\n\n"
        "## Krita\n\n"
        "在资源管理器中导入 `krita/实验笔刷.bundle`，启用资源包后重启 Krita，在笔刷预设中逐支测试。\n\n"
        "## GIMP\n\n"
        "把 `gimp/` 下的 `.gbr` 复制到 GIMP 的 brushes 搜索路径，刷新笔刷列表；GIMP 的纹理、散布和颜色动态需要另外在绘画动态面板中设置，记录为“目标格式未保存”或“手动补设”。\n\n"
        "## MyPaint\n\n"
        "把 `mypaint/` 下的 `.myb` 和同名 `_prev.png` 放入 MyPaint 笔刷目录，重启 MyPaint 后逐支测试。MyPaint 主要验证尺寸、间距、压力和混色，纹理、双重笔刷和颜色动态按报告记录损失。\n\n"
        "## 每支都要记录\n\n"
        "导入成功、重启后是否保留、单点、慢速直线、快速直线、曲线、轻中重压力、纹理/混色，以及软件版本。将结果填回 `朋友实机测试记录.csv`；文件能打开但笔迹不符合时，仍记录为导入成功、效果失败或近似。\n",
        encoding="utf-8")

    targets = {
        "photoshop": ("photoshop", "实验笔刷.abr"), "csp": ("csp", "实验笔刷.sut"),
        "udongman": ("udongman", "实验笔刷.sut"), "procreate": ("procreate", "实验笔刷.brushset"),
        "sai2": ("sai2", "实验笔刷"), "huashijie": ("huashijie", "实验笔刷.bru"),
        "krita": ("krita", "实验笔刷.bundle"), "gimp": ("gimp", "实验笔刷.gbr"),
        "mypaint": ("mypaint", "实验笔刷.myb"),
    }
    exports = {}
    for target, (codec_id, filename) in targets.items():
        d = OUT / target
        d.mkdir()
        result = export_detailed(brushes, target, d / filename)
        exports[target] = {"ok": result.ok, "files": [str(p.relative_to(OUT)) for p in result.files],
                           "report": str(result.report_path.relative_to(OUT)) if result.report_path else None,
                           "succeeded": result.succeeded, "total": len(result.entries)}
    manifest["exports"] = exports
    manifest["package_sha256"] = hashlib.sha256(package.read_bytes()).hexdigest()
    (OUT / "理想效果.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"package": str(package), "brushes": len(brushes), "exports": exports}, ensure_ascii=False, indent=2))
    return 0 if all(x["ok"] for x in exports.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
