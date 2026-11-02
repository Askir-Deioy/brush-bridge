"""Check and summarize a returned friend-test CSV.

The template intentionally allows empty cells.  This tool reports what is
filled, flags malformed row identities, and distinguishes import success from
stroke/effect results; it never promotes an empty or partial row to pass.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

EXPECTED_APPS = {"Photoshop", "CSP", "优动漫", "Procreate", "SAI2", "画世界 Pro", "Krita", "GIMP", "MyPaint"}
REQUIRED = {"experiment_id", "brush_name", "app", "imported", "restarted_and_kept", "dot", "line", "curve", "pressure", "texture_or_mix", "notes"}
RESULT_COLUMNS = ("imported", "restarted_and_kept", "dot", "line", "curve", "pressure", "texture_or_mix")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("input", nargs="?", default="examples/experiments/朋友实机测试记录.csv")
    p.add_argument("--out", default="examples/experiments/实机测试汇总.json")
    args = p.parse_args(argv)
    path = Path(args.input)
    if not path.is_file():
        raise SystemExit(f"找不到测试 CSV：{path}")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit("CSV 没有数据行")
    fields = set(rows[0])
    missing = REQUIRED - fields
    if missing:
        raise SystemExit(f"CSV 缺少字段：{', '.join(sorted(missing))}")
    invalid = []
    for i, row in enumerate(rows, 2):
        if row.get("app") not in EXPECTED_APPS:
            invalid.append({"line": i, "problem": "unknown_app", "value": row.get("app", "")})
        if not row.get("experiment_id", "").startswith("EXP-") or not row.get("brush_name"):
            invalid.append({"line": i, "problem": "invalid_experiment_identity"})
    filled = [r for r in rows if any((r.get(k) or "").strip() for k in RESULT_COLUMNS)]
    imported = Counter((r.get("imported") or "").strip().lower() for r in rows)
    effect_filled = Counter()
    incomplete = []
    for r in rows:
        vals = [(r.get(k) or "").strip() for k in RESULT_COLUMNS]
        if any(vals[1:]) and not vals[0]:
            incomplete.append({"experiment_id": r.get("experiment_id"), "app": r.get("app"),
                               "problem": "effect_filled_without_import_result"})
        for key, value in zip(RESULT_COLUMNS, vals):
            if value:
                effect_filled[key] += 1
    result = {
        "schema_version": "0.1", "input": str(path), "total_rows": len(rows),
        "filled_rows": len(filled), "empty_rows": len(rows) - len(filled),
        "imported_values": dict(imported), "filled_result_columns": dict(effect_filled),
        "invalid_rows": invalid, "incomplete_rows": incomplete,
        "status": "invalid" if invalid else ("needs_review" if incomplete else "ready_for_review"),
        "note": "导入成功、重启保留和笔迹效果分别统计；空白不代表通过。",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"status": result["status"], "total_rows": len(rows),
                      "filled_rows": len(filled), "output": str(out)}, ensure_ascii=False))
    return 0 if not invalid else 2


if __name__ == "__main__":
    raise SystemExit(main())
