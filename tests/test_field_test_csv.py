import csv
import json
from pathlib import Path

from scripts.validate_field_test_csv import main


FIELDS = ["experiment_id", "brush_name", "ideal_effect", "test_steps", "app", "imported",
          "restarted_and_kept", "dot", "line", "curve", "pressure", "texture_or_mix", "notes"]


def _write(path: Path, row: dict) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerow(row)


def test_field_test_csv_flags_effect_without_import(tmp_path):
    row = {k: "" for k in FIELDS}
    row.update(experiment_id="EXP-01", brush_name="EXP-01 硬圆基础", app="CSP", line="失败")
    source = tmp_path / "results.csv"
    output = tmp_path / "summary.json"
    _write(source, row)
    assert main([str(source), "--out", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "needs_review"
    assert result["incomplete_rows"]


def test_field_test_csv_accepts_empty_template_row(tmp_path):
    row = {k: "" for k in FIELDS}
    row.update(experiment_id="EXP-01", brush_name="EXP-01 硬圆基础", app="CSP")
    source = tmp_path / "template.csv"
    output = tmp_path / "summary.json"
    _write(source, row)
    assert main([str(source), "--out", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "ready_for_review"
