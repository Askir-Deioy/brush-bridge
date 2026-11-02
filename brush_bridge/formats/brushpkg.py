"""Brush package 0.1: ZIP + typed JSON + content-addressed PNG/binary assets."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from ..core.model import Brush
from ..core.plugin import FileInfo, FormatCodec, register_codec
from ..core.storage import decode, encode, json_bytes, seal_source

FORMAT = "brush-bridge-package"
VERSION = "0.1"
MAX_MEMBER = 256 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_ENTRIES = 20000
MAX_JSON = 32 * 1024 * 1024


class PackageError(ValueError):
    pass


def _safe(name):
    p = PurePosixPath(name)
    if not name or "\\" in name or ":" in name or p.is_absolute() or any(x in ("", ".", "..") for x in name.split("/")):
        raise PackageError("包内路径不安全")
    return name


def _json(data):
    if len(data) > MAX_JSON:
        raise PackageError("JSON 描述超过限制")
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise PackageError("JSON 存在重复字段")
            out[k] = v
        return out
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(PackageError("非法数值")))


def _inspect(zf):
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_TOTAL:
        raise PackageError("压缩包条目数量或解压总量超过限制")
    names = set()
    for info in infos:
        _safe(info.filename)
        if info.filename in names or info.file_size > MAX_MEMBER or info.flag_bits & 1:
            raise PackageError("重复条目、加密条目或单条目超过限制")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise PackageError("不允许符号链接")
        names.add(info.filename)
    if "manifest.json" not in names:
        raise PackageError("缺少 manifest.json")
    if zf.getinfo("manifest.json").file_size > MAX_JSON:
        raise PackageError("清单超过限制")
    m = _json(zf.read("manifest.json"))
    if m.get("format") != FORMAT or m.get("version") != VERSION:
        raise PackageError("不支持的笔刷包规范版本")
    if set(m.get("required_features", [])) - {"typed-values-1"}:
        raise PackageError("包含尚不支持的必要能力")
    if not isinstance(m.get("brushes"), list) or not m["brushes"]:
        raise PackageError("笔刷清单为空或无效")
    members = m.get("members", {})
    if set(members) != names - {"manifest.json"}:
        raise PackageError("资源清单与包内文件不一致")
    for name, meta in members.items():
        _safe(name)
        if meta.get("size") != zf.getinfo(name).file_size:
            raise PackageError("资源大小与清单不一致")
    return m


def read_package(path):
    try:
        with zipfile.ZipFile(path) as zf:
            m = _inspect(zf)
            cache = {}
            def get(name):
                _safe(name)
                if name not in m["members"]:
                    raise PackageError("资源不在清单中")
                if name not in cache:
                    data = zf.read(name)
                    if hashlib.sha256(data).hexdigest() != m["members"][name]["sha256"]:
                        raise PackageError(f"资源哈希不一致：{name}")
                    cache[name] = data
                return cache[name]
            # Check even unreferenced resources: a successfully opened package is fully verified.
            for name in m["members"]:
                get(name)
            out = []
            for entry in m["brushes"]:
                b = decode(_json(get(entry["path"])), get)
                if not isinstance(b, Brush):
                    raise PackageError("笔刷描述的根类型错误")
                if not isinstance(b.name, str) or not isinstance(b.tips, list):
                    raise PackageError("笔刷字段类型错误")
                b._package_manifest_unknown = {k: v for k, v in m.items() if k not in {
                    "format", "version", "required_features", "brushes", "members", "units", "producer"}}
                out.append(b)
            return out
    except (OSError, KeyError, TypeError, RecursionError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise PackageError(f"笔刷包无法读取：{exc}") from exc


def write_package(brushes, path):
    from .. import __version__
    if not brushes:
        raise PackageError("没有可保存的笔刷")
    payloads = {}
    def put(data, suffix):
        if len(data) > MAX_MEMBER:
            raise PackageError("单项资源超过限制")
        name = f"{'assets' if suffix == 'png' else 'native'}/{hashlib.sha256(data).hexdigest()}.{suffix}"
        payloads.setdefault(name, data)
        return name
    entries = []
    for i, brush in enumerate(brushes):
        seal_source(brush)
        name = f"brushes/{i:06d}.json"
        data = json_bytes(encode(brush, put))
        if len(data) > MAX_JSON:
            raise PackageError("笔刷描述超过限制")
        payloads[name] = data
        entries.append({"path": name, "name": brush.name, "uid": brush.uid, "group": brush.group})
    manifest = dict(getattr(brushes[0], "_package_manifest_unknown", {}))
    manifest.update(format=FORMAT, version=VERSION, required_features=["typed-values-1"],
                    producer={"name": "Brush Bridge", "version": __version__},
                    units={"size": "px", "angle": "degrees", "spacing": "tip-diameter-fraction",
                           "coverage": "0..1", "tip_polarity": "255=paint"}, brushes=entries,
                    members={n: {"size": len(d), "sha256": hashlib.sha256(d).hexdigest()} for n, d in payloads.items()})
    payloads["manifest.json"] = json_bytes(manifest)
    if len(payloads) > MAX_ENTRIES or sum(map(len, payloads.values())) > MAX_TOTAL:
        raise PackageError("存档总量超过限制")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".brushpkg-", suffix=".part", dir=path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in payloads.items():
                zf.writestr(name, data)
        read_package(tmp)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return path


@register_codec
class BrushPackageCodec(FormatCodec):
    id = "brushpkg"
    name = "独立笔刷主存档 (.brushpkg)"
    extensions = (".brushpkg",)
    can_write = True

    def sniff(self, path):
        if Path(path).suffix.lower() != ".brushpkg" or not Path(path).is_file():
            return None
        # Retain an actionable read error even for malformed packages.
        return FileInfo(Path(path), self.id, self.name, VERSION)

    def read(self, path):
        return read_package(path)

    def write(self, brushes, out_path, **options):
        return [write_package(brushes, out_path)]
