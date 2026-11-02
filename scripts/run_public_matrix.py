"""Run a bounded public-sample cross-format matrix.

This uses the same downloaded, licence-recorded samples as
run_public_roundtrips.py, caps each source file at three brushes, and records
file-level conversion/loss evidence without claiming real-app fidelity.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brush_bridge.core.plugin import codec, load_plugins
from brush_bridge.core.reporting import export_detailed
from brush_bridge.formats.brushpkg import read_package, write_package

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("photoshop", "csp", "udongman", "procreate", "sai2", "huashijie", "krita", "gimp", "mypaint")
TARGET_CODECS = {"photoshop": "abr", "csp": "csp", "udongman": "csp", "procreate": "procreate",
                 "sai2": "sai2", "huashijie": "huashijie", "krita": "krita", "gimp": "gimp", "mypaint": "mypaint"}


def _read_output(target: str, files):
    cid = TARGET_CODECS[target]
    suffixes = set(codec(cid).extensions)
    if target == "sai2":
        suffixes = {".saitdat"}
    if target == "mypaint":
        suffixes = {".myb"}
    decoded = []
    for p in files:
        if Path(p).suffix.lower() in suffixes:
            decoded.extend(codec(cid).read(p))
    return decoded


def _field_metrics(source, decoded):
    if len(source) != len(decoded) or [b.name for b in source] != [b.name for b in decoded]:
        return {"status": "structure_changed", "source_count": len(source), "result_count": len(decoded),
                "source_names": [b.name for b in source], "result_names": [b.name for b in decoded]}
    rows = []
    for a, b in zip(source, decoded):
        rows.append({"name": a.name,
                     "size_relative_error": abs(a.size_px - b.size_px) / max(abs(a.size_px), 1e-6),
                     "spacing_relative_error": abs(a.spacing - b.spacing) / max(abs(a.spacing), 1e-6),
                     "opacity_absolute_error": abs(a.opacity - b.opacity),
                     "source_texture": a.texture is not None, "result_texture": b.texture is not None,
                     "source_dual": a.dual is not None, "result_dual": b.dual is not None})
    return {"status": "compared", "brushes": rows,
            "max_size_relative_error": max((r["size_relative_error"] for r in rows), default=0),
            "max_spacing_relative_error": max((r["spacing_relative_error"] for r in rows), default=0),
            "max_opacity_absolute_error": max((r["opacity_absolute_error"] for r in rows), default=0)}


def _fetcher():
    spec = importlib.util.spec_from_file_location("fetch_samples", ROOT / "scripts" / "fetch_samples.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _samples(no_fetch: bool):
    f = _fetcher()
    if no_fetch:
        return [("procreate", "procreate", sorted((ROOT / "tests/fixtures/downloaded/procreate").glob("*"))),
                ("krita", "krita", sorted((ROOT / "tests/fixtures/downloaded/krita").glob("*"))),
                ("gimp", "gimp", sorted((ROOT / "tests/fixtures/downloaded/gimp").glob("*")))]
    return [("procreate", "procreate", f.fetch_procreate()),
            ("krita", "krita", f.fetch_krita()),
            ("gimp", "gimp", f.fetch_gimp())]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=".cache/public-matrix")
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--max-brushes", type=int, default=3)
    args = p.parse_args(argv)
    load_plugins()
    out = (ROOT / args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rows = []
    for section, cid, paths in _samples(args.no_fetch):
        for src_path in paths:
            if not src_path.is_file():
                continue
            row = {"section": section, "source": str(src_path), "source_format": cid,
                   "targets": {}, "status": "failed", "error": ""}
            try:
                source = codec(cid).read(src_path)
                if not source:
                    row.update(status="skipped", reason="no_brushes")
                    rows.append(row)
                    continue
                source = source[:max(1, args.max_brushes)]
                pkg = out / "packages" / section / f"{src_path.stem}.brushpkg"
                pkg.parent.mkdir(parents=True, exist_ok=True)
                write_package(source, pkg)
                brushes = read_package(pkg)
                for target in TARGETS:
                    target_dir = out / "exports" / section / src_path.stem / target
                    target_dir.mkdir(parents=True, exist_ok=True)
                    cid_target = TARGET_CODECS[target]
                    ext = codec(cid_target).extensions[0] if target != "sai2" else ""
                    result = export_detailed(brushes, target, target_dir / ("converted" + ext))
                    decoded = _read_output(target, result.files) if result.ok else []
                    row["targets"][target] = {"ok": result.ok, "succeeded": result.succeeded,
                        "total": len(result.entries), "report": str(result.report_path),
                        "losses": sum(len(e.get("losses", [])) for e in result.entries),
                        "warnings": sum(len(e.get("warnings", [])) for e in result.entries),
                        "field_metrics": _field_metrics(brushes, decoded) if result.ok else None}
                row.update(status="success", source_count=len(source), package=str(pkg))
            except Exception as exc:  # a single public sample must not stop the matrix
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
    result = {"schema_version": "0.1", "sample_source": "scripts/fetch_samples.py",
              "max_brushes_per_source": args.max_brushes, "real_app_import": "not_verified",
              "stroke_fidelity": "not_verified", "rows": rows,
              "summary": {"samples": len(rows), "success": sum(r["status"] == "success" for r in rows),
                          "skipped": sum(r["status"] == "skipped" for r in rows),
                          "failed": sum(r["status"] == "failed" for r in rows)}}
    path = out / "public-matrix.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"summary": result["summary"], "report": str(path)}, ensure_ascii=False))
    return 0 if result["summary"]["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
