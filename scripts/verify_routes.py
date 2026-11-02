"""Create original controls, a portable master and reproducible file-level route evidence.

Run with the project Python: python scripts/verify_routes.py --out examples/verification
No painting application or user brush library is accessed.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw
from brush_bridge import __version__
from brush_bridge.core import convert
from brush_bridge.core.model import Brush, Dynamic, Texture, DualTip, WetMix
from brush_bridge.core.plugin import codec
from brush_bridge.formats.brushpkg import write_package, read_package

APPS = {"photoshop": "abr", "csp": "csp", "udongman": "csp", "procreate": "procreate", "sai2": "sai2", "huashijie": "huashijie"}


def controls():
    tip = Image.new("L", (64, 48), 0)
    ImageDraw.Draw(tip).polygon([(3, 3), (58, 8), (18, 42)], fill=255)
    grain = Image.new("L", (32, 32), 255)
    ImageDraw.Draw(grain).rectangle((0, 0, 12, 31), fill=90)
    return [Brush("硬圆", size_px=24, spacing=0.2), Brush("软圆", hardness=0.3, size_px=40, spacing=0.15),
            Brush("非对称印章", tips=[tip], size_px=64, spacing=0.3, angle=20),
            Brush("纹理压感", tips=[tip], size_px=48, opacity=0.8, spacing=0.25,
                  texture=Texture(grain, name="原创颗粒"), size_dynamic=Dynamic(pressure=0.7), group="原创控制样本")]


def compare(before, after, *, require_texture=True, require_dynamic=True):
    a, b = {x.name: x for x in before}, {x.name: x for x in after}
    if a.keys() != b.keys():
        raise AssertionError("笔刷名称/数量不一致")
    checks = []
    for name, src in a.items():
        dst = b[name]
        values = {}
        for field, tolerance in (("size_px", 0.08), ("spacing", 0.08), ("opacity", 0.05)):
            x, y = getattr(src, field), getattr(dst, field)
            error = abs(x - y) / max(abs(x), 1e-6)
            if error > tolerance:
                raise AssertionError(f"{name} {field}: {x} → {y}，超过控制样本容差 {tolerance}")
            values[field] = {"source": x, "output": y, "relative_error": error}
        if src.tips and not dst.tips:
            raise AssertionError(f"{name} 丢失笔尖")
        if require_texture and src.texture and not dst.texture:
            raise AssertionError(f"{name} 丢失纹理")
        if require_dynamic and src.size_dynamic.pressure and abs(src.size_dynamic.pressure - dst.size_dynamic.pressure) > 0.08:
            raise AssertionError(f"{name} 压感参数偏差过大")
        checks.append({"name": name, "parameters": values, "tip_preserved": bool(dst.tips),
                       "texture_preserved": dst.texture is not None, "size_pressure": dst.size_dynamic.pressure})
    return checks


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="examples/verification")
    args = p.parse_args(argv)
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    write_package(controls(), root / "原创控制笔刷.brushpkg")
    complex_b = controls()[-1]
    complex_b.name = "混色双重笔刷（近似验证）"
    complex_b.wet = WetMix(True, 0.5, 0.7, 0.4, 0.2)
    complex_b.dual = DualTip(complex_b.tips[0].copy(), size_px=22)
    complex_b.size_dynamic.pressure_curve = [(0, 0.2), (0.4, 0.3), (1, 1)]
    write_package([complex_b], root / "复杂效果示例.brushpkg")
    sources = {}
    for source, cid in APPS.items():
        c = codec(cid)
        base = root / "sources" / source
        base.mkdir(parents=True, exist_ok=True)
        files = c.write(copy.deepcopy(controls()), base / ("controls" + c.extensions[0]))
        files = [f for f in files if f.suffix == ".saitdat"] if cid == "sai2" else files
        loaded = [b for f in files for b in c.read(f)]
        sources[source] = read_package(write_package(loaded, base / "master.brushpkg"))
    routes = []
    for source in APPS:
        for target, cid in APPS.items():
            if source == target:
                continue
            out = root / "routes" / f"{source}-to-{target}" / ("brushes" + codec(cid).extensions[0])
            result = convert.export_detailed(sources[source], target, out)
            route = {"source": source, "target": target, "file_validation": "failed", "app_import": "not_verified",
                     "stroke_fidelity": "not_verified", "report": str(result.report_path.relative_to(root))}
            try:
                if not result.ok:
                    raise AssertionError(str(result.entries))
                files = [f for f in result.files if f.suffix == ".saitdat"] if cid == "sai2" else result.files
                decoded = [b for f in files for b in codec(cid).read(f)]
                route["checks"] = compare(sources[source], decoded,
                                           require_texture=target != "huashijie",
                                           require_dynamic=target != "huashijie")
                route["file_validation"] = "passed"
            except Exception as exc:
                route["error"] = str(exc)
            routes.append(route)
    evidence = {"app_version": __version__, "sample_origin": "original-generated-controls", "routes": routes,
                "note": "文件回读和控制参数检查；没有执行目标绘画软件的导入、重启或笔迹检查。"}
    (root / "compatibility.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(r["file_validation"] == "passed" for r in routes)
    print(f"File-level routes: {passed}/{len(routes)}. Evidence: {root / 'compatibility.json'}")
    for r in routes:
        if r["file_validation"] != "passed":
            print(r["source"], r["target"], r.get("error"))
    return 0 if passed == len(routes) else 1


if __name__ == "__main__":
    sys.exit(main())
