"""Run public-sample -> brushpkg -> same-format round-trip evidence.

Samples are downloaded at test time according to scripts/fetch_samples.py and
remain outside the release.  The report separates structural/common-field
round-trip results from real application import and stroke fidelity.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brush_bridge.core.plugin import codec, load_plugins
from brush_bridge.formats.brushpkg import read_package, write_package


ROOT = Path(__file__).resolve().parents[1]


def _fetcher():
    spec = importlib.util.spec_from_file_location("fetch_samples", ROOT / "scripts" / "fetch_samples.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _names(brushes):
    return [b.name for b in brushes]


def _tip_stats(brush):
    if not brush.tips:
        return {"present": False}
    a = np.asarray(brush.tips[0].convert("L"), dtype=np.float32)
    return {"present": True, "size": list(brush.tips[0].size), "mean": float(a.mean()),
            "min": int(a.min()), "max": int(a.max()), "nonzero": int(np.count_nonzero(a))}


def _compare(source, result):
    if len(source) != len(result) or _names(source) != _names(result):
        return {"status": "structure_changed", "source_count": len(source), "result_count": len(result),
                "source_names": _names(source), "result_names": _names(result)}
    rows = []
    for a, b in zip(source, result):
        sa = _tip_stats(a)
        sb = _tip_stats(b)
        if sa.get("present") and sb.get("present"):
            as_bytes = np.asarray(a.tips[0].convert("L"), dtype=np.uint8).tobytes()
            bs_bytes = np.asarray(b.tips[0].convert("L"), dtype=np.uint8).tobytes()
            sa["sha256"] = hashlib.sha256(as_bytes).hexdigest()
            sb["sha256"] = hashlib.sha256(bs_bytes).hexdigest()
            tip_equal = a.tips[0].size == b.tips[0].size and as_bytes == bs_bytes
            tip_mean_diff = abs(sa["mean"] - sb["mean"])
        else:
            tip_equal = sa.get("present") == sb.get("present")
            tip_mean_diff = None
        rows.append({"name": a.name,
                     "size_relative_error": abs(a.size_px - b.size_px) / max(abs(a.size_px), 1e-6),
                     "spacing_relative_error": abs(a.spacing - b.spacing) / max(abs(a.spacing), 1e-6),
                     "opacity_absolute_error": abs(a.opacity - b.opacity),
                     "source_tip": sa, "result_tip": sb,
                     "tip_pixels_equal": tip_equal, "tip_mean_absolute_difference": tip_mean_diff,
                     "texture_source": a.texture is not None, "texture_result": b.texture is not None,
                     "dual_source": a.dual is not None, "dual_result": b.dual is not None,
                     "source_notes": list(a.notes), "result_notes": list(b.notes)})
    return {"status": "roundtrip_readback", "brushes": rows}


def _load_samples(no_fetch: bool = False):
    f = _fetcher()
    if no_fetch:
        return [
            ("procreate", "procreate", sorted((ROOT / "tests" / "fixtures" / "downloaded" / "procreate").glob("*"))),
            ("krita", "krita", sorted((ROOT / "tests" / "fixtures" / "downloaded" / "krita").glob("*"))),
            ("gimp", "gimp", sorted((ROOT / "tests" / "fixtures" / "downloaded" / "gimp").glob("*"))),
        ]
    return [
        ("procreate", "procreate", f.fetch_procreate()),
        ("krita", "krita", f.fetch_krita()),
        ("gimp", "gimp", f.fetch_gimp()),
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=".cache/public-roundtrips")
    p.add_argument("--no-fetch", action="store_true", help="use only samples already downloaded")
    args = p.parse_args(argv)
    load_plugins()
    out = (ROOT / args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    report = {"schema_version": "0.1", "sample_source": "scripts/fetch_samples.py",
              "real_app_import": "not_verified", "stroke_fidelity": "not_verified", "samples": []}
    for section, cid, paths in _load_samples(args.no_fetch):
        for src_path in paths:
            if not src_path.is_file():
                continue
            c = codec(cid)
            row = {"section": section, "source": str(src_path), "source_sha256": hashlib.sha256(src_path.read_bytes()).hexdigest(),
                   "codec": cid, "status": "failed", "error": ""}
            try:
                source = c.read(src_path)
                if not source:
                    row.update(status="skipped", reason="no_brushes")
                    report["samples"].append(row)
                    continue
                pkg = out / section / f"{src_path.stem}.brushpkg"
                pkg.parent.mkdir(parents=True, exist_ok=True)
                write_package(source, pkg)
                reopened = read_package(pkg)
                target_dir = out / section / src_path.stem
                target_dir.mkdir(parents=True, exist_ok=True)
                files = c.write(reopened, target_dir / src_path.name)
                decoded = [b for f in files if Path(f).suffix.lower() in c.extensions for b in c.read(f)]
                row.update(status="success", source_count=len(source), package_count=len(reopened),
                           output_files=[str(f) for f in files], comparison=_compare(source, decoded),
                           warnings=[n for b in reopened for n in b.notes],
                           package_sha256=hashlib.sha256(pkg.read_bytes()).hexdigest())
            except Exception as exc:  # one public sample must not stop the corpus
                row["error"] = f"{type(exc).__name__}: {exc}"
            report["samples"].append(row)
    report["summary"] = {"samples": len(report["samples"]),
                          "success": sum(r["status"] == "success" for r in report["samples"]),
                          "failed": sum(r["status"] == "failed" for r in report["samples"]),
                          "skipped": sum(r["status"] == "skipped" for r in report["samples"])}
    path = out / "public-roundtrips.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "report": str(path)}, ensure_ascii=False))
    return 0 if report["summary"]["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
