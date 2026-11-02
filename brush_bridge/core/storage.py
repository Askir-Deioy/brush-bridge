"""Explicit, non-executable value encoding shared by brushpkg and fingerprints."""
from __future__ import annotations

import hashlib
import io
import json
import math
import datetime
from dataclasses import fields, is_dataclass
from pathlib import Path

from PIL import Image

from . import model

MAX_DEPTH = 64
MAX_IMAGE_PIXELS = 64_000_000


def classes():
    # Never import a class named by an input file.
    from ..formats.sai2 import Asset
    return {c.__name__: c for c in (model.Brush, model.BrushSource, model.Dynamic, model.Texture,
            model.DualTip, model.ColorDynamics, model.WetMix, model.EdgeEffect, Asset)}


def encode(value, put_blob, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError("数据嵌套超过限制")
    child = lambda v: encode(v, put_blob, depth + 1)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("参数包含非有限数值")
        return value
    if isinstance(value, bytes):
        return {"$type": "bytes", "ref": put_blob(value, "bin")}
    if isinstance(value, Image.Image):
        if value.width * value.height > MAX_IMAGE_PIXELS:
            raise ValueError("图片像素超过限制")
        buf = io.BytesIO()
        value.save(buf, format="PNG")
        return {"$type": "image", "ref": put_blob(buf.getvalue(), "png"), "mode": value.mode,
                "size": list(value.size)}
    if isinstance(value, Path):
        return {"$type": "path", "value": str(value)}
    if isinstance(value, datetime.datetime):
        return {"$type": "datetime", "value": value.isoformat()}
    if is_dataclass(value):
        known = {f.name: child(getattr(value, f.name)) for f in fields(value)}
        known.update({k: child(v) for k, v in getattr(value, "_package_unknown", {}).items() if k not in known})
        return {"$type": type(value).__name__, "fields": known}
    if isinstance(value, dict):
        return {"$type": "dict", "items": [[child(k), child(v)] for k, v in value.items()]}
    if isinstance(value, (list, tuple)):
        return {"$type": "tuple" if isinstance(value, tuple) else "list", "items": [child(v) for v in value]}
    raise ValueError(f"无法安全保存的数据类型：{type(value).__name__}")


def decode(value, get_blob, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError("数据嵌套超过限制")
    if not isinstance(value, dict) or "$type" not in value:
        if value is None or isinstance(value, (str, bool, int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("参数包含非有限数值")
            return value
        raise ValueError("无效的类型化数据")
    child = lambda v: decode(v, get_blob, depth + 1)
    kind = value["$type"]
    if kind == "bytes":
        return get_blob(value["ref"])
    if kind == "image":
        with Image.open(io.BytesIO(get_blob(value["ref"]))) as im:
            if im.width * im.height > MAX_IMAGE_PIXELS:
                raise ValueError("图片像素超过限制")
            im.load()
            if list(im.size) != value["size"]:
                raise ValueError("图片尺寸与描述不一致")
            return im.copy()
    if kind == "path":
        return Path(value["value"])
    if kind == "datetime":
        return datetime.datetime.fromisoformat(value["value"])
    if kind in ("list", "tuple"):
        seq = [child(x) for x in value["items"]]
        return tuple(seq) if kind == "tuple" else seq
    if kind == "dict":
        return {child(k): child(v) for k, v in value["items"]}
    cls = classes().get(kind)
    if cls is None:
        raise ValueError(f"不支持的必要数据类型：{kind}")
    vals = {k: child(v) for k, v in value["fields"].items()}
    names = {f.name for f in fields(cls)}
    obj = cls(**{k: v for k, v in vals.items() if k in names})
    obj._package_unknown = {k: v for k, v in vals.items() if k not in names}
    return obj


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def fingerprint(brush):
    def blob(data, suffix):
        return hashlib.sha256(data).hexdigest() + "." + suffix
    vals = {f.name: getattr(brush, f.name) for f in fields(brush)
            if f.name not in {"source", "notes", "uid", "preview"}}
    return hashlib.sha256(json_bytes(encode(vals, blob))).hexdigest()


def seal_source(brush):
    """Capture once, before the caller can edit; never rewrite a stored baseline."""
    if not brush.source.fingerprint:
        from .. import __version__
        baseline = fingerprint(brush)
        path = Path(brush.source.path) if brush.source.path else None
        if path and path.is_file() and brush.source.format != "brushpkg":
            if path.stat().st_size > 256 * 1024 * 1024:
                raise ValueError("源文件超过 256 MiB 存档限制")
            brush.source.snapshots.setdefault(path.name, path.read_bytes())
        brush.source.fingerprint = baseline
        brush.source.parser_version = __version__
    return brush


def prepare_export(brush):
    import copy
    result = copy.deepcopy(brush)
    if result.source.fingerprint and result.source.fingerprint != fingerprint(result):
        result.source.native = {}
        result.warn("公共参数已修改，已停止回放原生参数；来源快照仍保存在主存档中。")
    return result
