"""Validate the generated cross-software experiment kit.

This checks the experiment contract and file-level outputs.  It deliberately
does not mark real-app import or stroke fidelity as passed; those fields remain
for artists to fill after testing in the target applications.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from brush_bridge.core.plugin import codec, load_plugins
from brush_bridge.formats.brushpkg import read_package


ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "examples" / "experiments"
PACKAGE = KIT / "实验笔刷.brushpkg"
TARGET_CODECS = {
    "photoshop": "abr", "csp": "csp", "udongman": "csp", "procreate": "procreate",
    "sai2": "sai2", "huashijie": "huashijie", "krita": "krita", "gimp": "gimp", "mypaint": "mypaint",
}


def _fail(message: str) -> None:
    raise SystemExit(f"实验包验证失败：{message}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    manifest_path = KIT / "理想效果.json"
    if not PACKAGE.is_file() or not manifest_path.is_file():
        _fail("缺少实验主存档或理想效果清单，请先运行 create_experiment_brushes.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    load_plugins()
    brushes = read_package(PACKAGE)
    expected = {b["id"]: b for b in manifest.get("brushes", [])}
    actual = {}
    checks = []
    for b in brushes:
        meta = b.extensions.get("experiment")
        if not isinstance(meta, dict) or not meta.get("id"):
            _fail(f"{b.name} 缺少 experiment 元数据")
        actual[meta["id"]] = b
        if meta["id"] not in expected:
            _fail(f"{b.name} 不在理想效果清单中")
        features = set(meta.get("features", []))
        requirements = {
            "tip": bool(b.tips) or not b.tips,
            "size_pressure": b.size_dynamic.pressure > 0,
            "rotation": b.angle_follow_stroke,
            "rgba_tip": bool(b.tips) and b.tips[0].mode in {"RGBA", "LA", "P"},
            "alpha": bool(b.tips) and np.asarray(b.tips[0].convert("RGBA"))[:, :, 3].min() < 255,
            "texture": b.texture is not None,
            "texture_pressure": b.texture is not None and b.texture.depth_dynamic.pressure > 0,
            "scatter": b.scatter > 0,
            "count": b.count > 1,
            "random": b.tip_order == "random",
            "dual_tip": b.dual is not None,
            "multiply": b.dual is not None and b.dual.mode == "multiply",
            "wet_mix": b.wet.enabled,
            "edge": b.edge.is_active(),
            "color_pressure": b.color.has_shift(),
        }
        missing = [f for f in features if f in requirements and not requirements[f]]
        if missing:
            _fail(f"{b.name} 的实验特性未写入模型：{', '.join(missing)}")
        checks.append({"id": meta["id"], "name": b.name, "features": sorted(features),
                       "ideal_effect_present": bool(meta.get("ideal_effect")),
                       "package_roundtrip": True})
    if set(actual) != set(expected) or len(actual) != 9:
        _fail(f"实验笔刷数量或 ID 不一致：{sorted(actual)}")

    exports = {}
    for target_dir in sorted(p for p in KIT.iterdir() if p.is_dir()):
        report_files = sorted(target_dir.glob("*.report.json"))
        if len(report_files) != 1:
            _fail(f"{target_dir.name} 缺少唯一目标报告")
        report = json.loads(report_files[0].read_text(encoding="utf-8"))
        rows = report.get("brushes", [])
        if report.get("summary", {}).get("succeeded") != 9 or len(rows) != 9:
            _fail(f"{target_dir.name} 报告不是 9/9：{report.get('summary')}")
        if any(row.get("status") != "success" or row.get("file_validation") != "passed" for row in rows):
            _fail(f"{target_dir.name} 有失败或未验证的文件级记录")
        files = []
        for row in rows:
            files.extend(row.get("files", []))
        missing = [Path(f) for f in files if not Path(f).is_file()]
        if missing:
            _fail(f"{target_dir.name} 报告引用了不存在的输出文件")
        target = target_dir.name
        cid = TARGET_CODECS.get(target)
        if cid is None:
            _fail(f"实验目录没有目标格式映射：{target}")
        suffixes = {"abr": {".abr"}, "csp": {".sut"}, "procreate": {".brush", ".brushset"},
                    "sai2": {".saitdat"}, "huashijie": {".bru"}, "krita": {".bundle", ".kpp"},
                    "gimp": {".gbr", ".gih", ".vbr"}, "mypaint": {".myb"}}[cid]
        output_files = sorted(p for p in target_dir.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)
        decoded = []
        c = codec(cid)
        for p in output_files:
            decoded.extend(c.read(p))
        source_by_name = {b.name: b for b in brushes}
        decoded_by_name = {b.name: b for b in decoded}
        if set(source_by_name) != set(decoded_by_name) or len(decoded) != len(brushes):
            _fail(f"{target} 回读笔刷名称/数量不一致：{len(decoded)}")
        report_by_name = {row.get("name"): row for row in rows}
        field_checks = []
        for name, src in source_by_name.items():
            dst = decoded_by_name[name]
            size_error = abs(src.size_px - dst.size_px) / max(abs(src.size_px), 1e-6)
            spacing_error = abs(src.spacing - dst.spacing) / max(abs(src.spacing), 1e-6)
            opacity_error = abs(src.opacity - dst.opacity)
            # Legacy grayscale/parametric targets may quantize opacity or use
            # the tip mask as density; keep native material formats stricter.
            opacity_tolerance = .15 if target in {"gimp", "mypaint", "huashijie"} else .05
            if size_error > .08 or spacing_error > .08 or opacity_error > opacity_tolerance:
                _fail(f"{target}/{name} 公共参数偏差过大：size={size_error:.3f}, spacing={spacing_error:.3f}, opacity={opacity_error:.3f}")
            src_alpha = None
            dst_alpha = None
            if src.tips:
                src_alpha = int(np.asarray(src.tips[0].convert("RGBA"))[:, :, 3].mean())
            if dst.tips:
                dst_alpha = int(np.asarray(dst.tips[0].convert("RGBA"))[:, :, 3].mean())
            row_text = " ".join(str(x) for x in (report_by_name[name].get("warnings", []) +
                                                   [x.get("message", "") for x in report_by_name[name].get("losses", [])]))
            source_features = {
                "size_pressure": src.size_dynamic.pressure > 0,
                "rotation": src.angle_follow_stroke,
                "texture": src.texture is not None,
                "texture_pressure": src.texture is not None and src.texture.depth_dynamic.pressure > 0,
                "scatter": src.scatter > 0,
                "count": src.count > 1,
                "dual_tip": src.dual is not None,
                "wet_mix": src.wet.enabled,
                "color_pressure": src.color.has_shift(),
            }
            target_features = {
                "size_pressure": dst.size_dynamic.pressure > 0,
                "rotation": dst.angle_follow_stroke,
                "texture": dst.texture is not None,
                "texture_pressure": dst.texture is not None and dst.texture.depth_dynamic.pressure > 0,
                "scatter": dst.scatter > 0,
                "count": dst.count > 1,
                "dual_tip": dst.dual is not None,
                "wet_mix": dst.wet.enabled,
                "color_pressure": dst.color.has_shift(),
            }
            feature_status = {}
            for feature, used in source_features.items():
                if not used:
                    feature_status[feature] = "not_used"
                elif target_features[feature]:
                    feature_status[feature] = "preserved"
                elif row_text:
                    feature_status[feature] = "reported_loss"
                else:
                    _fail(f"{target}/{name} 丢失 {feature} 但报告没有 warning/losses")
            field_checks.append({"name": name, "size_relative_error": size_error,
                                 "spacing_relative_error": spacing_error, "opacity_absolute_error": opacity_error,
                                 "opacity_tolerance": opacity_tolerance,
                                 "source_tip": bool(src.tips), "target_tip": bool(dst.tips),
                                 "source_texture": src.texture is not None, "target_texture": dst.texture is not None,
                                 "source_dual": src.dual is not None, "target_dual": dst.dual is not None,
                                 "source_size_pressure": src.size_dynamic.pressure,
                                 "target_size_pressure": dst.size_dynamic.pressure,
                                 "source_tip_alpha_mean": src_alpha, "target_tip_alpha_mean": dst_alpha,
                                 "feature_status": feature_status})
        exports[target] = {"report": str(report_files[0].relative_to(KIT)), "success": True,
                           "count": len(rows), "readback_count": len(decoded), "field_checks": field_checks,
                           "app_import": "not_verified", "stroke_fidelity": "not_verified"}

    result = {"schema_version": "0.1", "package": PACKAGE.name,
              "package_sha256": hashlib.sha256(PACKAGE.read_bytes()).hexdigest(),
              "brush_count": len(checks), "brushes": checks, "exports": exports,
              "note": "文件级主存档和目标格式验证；真实软件导入与笔迹仍需人工验收。"}
    out = KIT / "实验验证.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"brushes": len(checks), "targets": len(exports), "output": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
