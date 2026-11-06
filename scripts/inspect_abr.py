"""Read-only ABR structure inspection and pair comparison.

This intentionally reports observed structure and decoded values without trying to
repair or rewrite the input file.  It is for compatibility investigations, not a
replacement for Photoshop import testing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from brush_bridge.formats.abr import parse, AbrCodec


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect(path: Path) -> dict:
    raw = path.read_bytes()
    abr = parse(path)
    sections = {}
    for key, section in abr.sections.items():
        blob = abr.data[section.start:section.start + section.length]
        sections[key] = {
            "offset": section.start,
            "length": section.length,
            "sha256": _sha(blob),
        }
    brushes = []
    for brush in AbrCodec().read(path):
        brushes.append({
            "name": brush.name,
            "size_px": brush.size_px,
            "spacing": brush.spacing,
            "tips": [
                {"width": tip.width, "height": tip.height, "sha256": _sha(tip.tobytes())}
                for tip in brush.tips
            ],
            "has_texture": brush.texture is not None,
            "has_dual": brush.dual is not None,
        })
    return {
        "path": str(path),
        "size": len(raw),
        "sha256": _sha(raw),
        "version": abr.version,
        "subversion": abr.subversion,
        "sections": sections,
        "sample_count": len(abr.samples),
        "pattern_count": len(abr.patterns),
        "groups": abr.groups,
        "preset_keys": [[key for key, _typ, _value in desc.items] for desc, *_ in abr.presets],
        "brushes": brushes,
    }


def compare(left: dict, right: dict) -> dict:
    keys = sorted(set(left["sections"]) | set(right["sections"]))
    return {
        "left": left["path"],
        "right": right["path"],
        "same_decoded_brushes": left["brushes"] == right["brushes"],
        "sections": {
            key: {
                "left_length": left["sections"].get(key, {}).get("length"),
                "right_length": right["sections"].get(key, {}).get("length"),
                "same_bytes": left["sections"].get(key, {}).get("sha256") == right["sections"].get(key, {}).get("sha256"),
            }
            for key in keys
        },
        "groups_same": left["groups"] == right["groups"],
        "preset_keys_same": left["preset_keys"] == right["preset_keys"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查 Photoshop ABR 的结构和解码摘要")
    parser.add_argument("paths", nargs="+", type=Path, help="一个或多个 .abr 文件")
    parser.add_argument("--diff", action="store_true", help="比较前两个文件的 section 和解码摘要")
    args = parser.parse_args()
    reports = [inspect(path) for path in args.paths]
    output = {"files": reports}
    if args.diff and len(reports) >= 2:
        output["comparison"] = compare(reports[0], reports[1])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
