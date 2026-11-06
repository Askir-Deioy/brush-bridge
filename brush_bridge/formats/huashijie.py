"""画世界 Pro 的 ``.bru`` 笔刷包。

画世界 Pro 的 BRU 文件不是 ZIP，而是 ``hsjbru`` 头部、参数记录和成对的
48x48 预览/200x200 笔尖 PNG。参数记录使用 protobuf 风格的 wire 编码，官方
没有公开 schema，因此本适配器只把已经确认的公共字段映射到 Brush，其余字段
作为 typed extension 和原始记录保存到 ``.brushpkg``。

这让文件可以被读取、保存和转换；写出的 BRU 已通过结构回读检查，但仍需要在
画世界 Pro 的真实版本中导入并画线后才能宣称效果兼容。
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
import struct
import time
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from ..core.imaging import pad_square, round_tip, stroke_preview, tip_mask
from ..core.model import Brush, BrushRef, BrushSource
from ..core.plugin import FileInfo, FormatCodec, register_codec

MAGIC = b"hsjbru"
HEADER_LEN = 0x96
PREFIX_LEN = 50
FORMAT_VERSION = "5"
PNG_SIG = b"\x89PNG\r\n\x1a\n"
MAX_BRUSHES = 4096


class HuaShiJieFormatError(ValueError):
    pass


def _varint(value: int) -> bytes:
    value = int(value)
    if value < 0:
        value &= (1 << 64) - 1
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _field_varint(number: int, value: int) -> bytes:
    return _varint((number << 3) | 0) + _varint(value)


def _field_fixed32(number: int, value: float) -> bytes:
    return _varint((number << 3) | 5) + struct.pack("<f", float(value))


def _field_bytes(number: int, value: bytes | str) -> bytes:
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    return _varint((number << 3) | 2) + _varint(len(raw)) + raw


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while pos < len(data) and shift < 70:
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, pos
        shift += 7
    raise HuaShiJieFormatError("画世界参数记录的整数编码损坏")


def _parse_fields(data: bytes) -> list[tuple[int, int, Any]]:
    """Parse the protobuf-like record without assuming a schema."""
    fields: list[tuple[int, int, Any]] = []
    pos = 0
    while pos < len(data):
        tag, pos = _read_varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if number <= 0:
            raise HuaShiJieFormatError("画世界参数记录包含无效字段号")
        if wire == 0:
            value, pos = _read_varint(data, pos)
        elif wire == 1:
            if pos + 8 > len(data):
                raise HuaShiJieFormatError("画世界参数记录的 fixed64 被截断")
            value, pos = data[pos:pos + 8], pos + 8
        elif wire == 2:
            size, pos = _read_varint(data, pos)
            if size > len(data) - pos:
                raise HuaShiJieFormatError("画世界参数记录的字节字段被截断")
            value, pos = data[pos:pos + size], pos + size
        elif wire == 5:
            if pos + 4 > len(data):
                raise HuaShiJieFormatError("画世界参数记录的 fixed32 被截断")
            value, pos = data[pos:pos + 4], pos + 4
        else:
            raise HuaShiJieFormatError(f"画世界参数记录使用了未知 wire 类型 {wire}")
        fields.append((number, wire, value))
    return fields


def _png_end(data: bytes, start: int) -> int:
    if data[start:start + 8] != PNG_SIG:
        raise HuaShiJieFormatError("PNG 资源签名错误")
    pos = start + 8
    while pos + 12 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + size
        if end > len(data):
            raise HuaShiJieFormatError("PNG 资源超出文件范围")
        pos = end
        if kind == b"IEND":
            return pos
    raise HuaShiJieFormatError("PNG 资源缺少 IEND")


def _pngs(data: bytes) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    pos = 0
    while True:
        start = data.find(PNG_SIG, pos)
        if start < 0:
            break
        end = _png_end(data, start)
        out.append((start, end))
        pos = end
        if len(out) > MAX_BRUSHES * 2:
            raise HuaShiJieFormatError("BRU 内嵌图片数量超过限制")
    if not out or len(out) % 2:
        raise HuaShiJieFormatError("BRU 需要成对的预览图和笔尖图")
    return out


def _field_value(fields: list[tuple[int, int, Any]], number: int, default: Any = None) -> Any:
    for n, wire, value in fields:
        if n == number:
            if wire == 5:
                return struct.unpack("<f", value)[0]
            if wire == 0:
                return value
            if wire == 2:
                try:
                    return value.decode("utf-8")
                except UnicodeDecodeError:
                    return value
    return default


def _find_metadata(data: bytes, start: int, end: int) -> tuple[int, bytes]:
    """Find the first complete parameter record in a metadata gap."""
    # All known BRU revisions begin with field 1 (0x0a).  Scanning instead of
    # hard-coding the 50-byte table makes this tolerant of future table changes.
    for pos in range(start, min(end, start + 256)):
        if data[pos:pos + 1] != b"\x0a":
            continue
        try:
            fields = _parse_fields(data[pos:end])
        except HuaShiJieFormatError:
            continue
        if any(n == 3 and wire == 2 for n, wire, _ in fields):
            return pos, data[pos:end]
    raise HuaShiJieFormatError("找不到画世界参数记录")


def _records(path: Path) -> tuple[bytes, list[tuple[bytes, bytes, bytes, list[tuple[int, int, Any]]]]]:
    data = Path(path).read_bytes()
    if len(data) < HEADER_LEN or not data.startswith(MAGIC):
        raise HuaShiJieFormatError("不是画世界 Pro BRU 文件（缺少 hsjbru 头部）")
    version = int.from_bytes(data[8:10], "big")
    if version >= 8:
        # Newer BRU exports may contain a parameter record without embedded
        # preview/tip PNGs.  The icon path is metadata only.  Keep the record
        # and let the common model represent a computed round tip.
        metadata_start = HEADER_LEN
        declared = int.from_bytes(data[0x6C:0x70], "big")
        end = metadata_start + declared if declared else len(data)
        if end > len(data):
            raise HuaShiJieFormatError("新版 BRU 参数记录超出文件范围")
        metadata = data[metadata_start:end]
        fields = _parse_fields(metadata)
        if not fields or _field_value(fields, 3, None) is None:
            raise HuaShiJieFormatError("新版 BRU 缺少笔刷名称字段")
        return data, [(metadata, b"", b"", fields)]
    # Header: magic[6], reserved[2], version u16 at 0x08, total length u32 at 0x0a.
    declared = int.from_bytes(data[10:14], "big")
    if declared and declared != len(data):
        raise HuaShiJieFormatError(f"BRU 文件长度声明为 {declared}，实际为 {len(data)}")
    images = _pngs(data)
    records = []
    for index in range(0, len(images), 2):
        preview_start, preview_end = images[index]
        tip_start, tip_end = images[index + 1]
        metadata_start = HEADER_LEN if index == 0 else images[index - 1][1]
        if index:
            metadata_start, _ = _find_metadata(data, metadata_start, preview_start)
        else:
            metadata_start, _ = _find_metadata(data, 12, preview_start)
        metadata = data[metadata_start:preview_start]
        fields = _parse_fields(metadata)
        records.append((metadata, data[preview_start:preview_end], data[tip_start:tip_end], fields))
    return data, records


def _unique_id(index: int, name: str) -> str:
    digest = hashlib.sha256(f"brush-bridge:{index}:{name}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:22]


def _image(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        return im.convert("RGBA")


def _record_to_brush(path: Path, index: int, item) -> Brush:
    metadata, preview_png, tip_png, fields = item
    name = _field_value(fields, 3, f"画世界笔刷 {index + 1}")
    if not isinstance(name, str) or not name.strip():
        name = f"画世界笔刷 {index + 1}"
    if preview_png and tip_png:
        preview = _image(preview_png)
        tip_rgba = _image(tip_png)
        tip = tip_rgba.getchannel("A")
        if tip.getbbox() is None:
            # A few old exports use a fully opaque grayscale image instead of alpha.
            tip = tip_rgba.convert("L")
        b = Brush(name=name.strip(), tips=[tip], preview=preview)
    else:
        # v8 basic brushes can omit raster resources and rely on a computed
        # round tip.  Keep the public model honest instead of inventing an
        # embedded PNG that was never present in the source file.
        b = Brush(name=name.strip(), tips=[])
    b.size_px = max(1.0, float(_field_value(fields, 8, 30.0)))
    b.opacity = max(0.0, min(1.0, float(_field_value(fields, 13, 1.0))))
    b.spacing = max(0.0, min(10.0, float(_field_value(fields, 17, 0.1))))
    b.flow = max(0.0, min(1.0, float(_field_value(fields, 22, 1.0))))
    b.group = ""
    b.extensions["huashijie"] = {
        "schema": "hsjbru-wire-observed-1",
        "fields": {str(n): (value.hex() if isinstance(value, bytes) else value)
                    for n, _wire, value in fields},
        "metadata": metadata,
    }
    if tip_png:
        b.resources["tip"] = {"status": "embedded", "name": f"brush[{index}].tip.png",
                               "width": tip.width, "height": tip.height}
    version = "8" if not tip_png else FORMAT_VERSION
    b.source = BrushSource(app="huashijie", format="huashijie", version=version,
                           path=str(path), index=index,
                           native={"metadata": metadata, "preview_png": preview_png, "tip_png": tip_png})
    b.warn("画世界 Pro BRU 已读取；复杂参数保存在主存档扩展区，导出后需要在画世界 Pro 中实机核对。")
    if not tip_png:
        b.warn("新版 BRU 未内嵌笔尖图片，按计算硬圆笔尖读取。")
    return b


def _metadata_for(brush: Brush, index: int) -> bytes:
    now = str(int(time.time() * 1000)) + f"_{index}"
    group = brush.group or "BrushBridge"
    parts = [
        _field_bytes(1, _unique_id(index, brush.name)),
        _field_bytes(2, "BrushBridge-HuaShiJie"),
        _field_bytes(3, brush.name or f"画世界笔刷 {index + 1}"),
        _field_bytes(4, now),
        _field_varint(5, 1),
        _field_bytes(6, "pt39"),
        _field_bytes(7, now),
        _field_fixed32(8, max(1.0, brush.size_px)),
        _field_fixed32(11, 1.0),
        _field_fixed32(12, 500.0),
        _field_fixed32(13, max(0.0, min(1.0, brush.opacity))),
        _field_fixed32(17, max(0.0, min(10.0, brush.spacing))),
        _field_fixed32(20, max(1.0, brush.size_px)),
        _field_fixed32(22, max(0.0, min(1.0, brush.flow))),
        _field_fixed32(23, 1.0),
        _field_fixed32(24, max(0.01, min(1.0, brush.roundness))),
        _field_fixed32(25, 1.0),
        _field_varint(28, 1), _field_varint(30, 1), _field_varint(31, 1), _field_varint(32, 1),
        _field_varint(33, 100), _field_varint(36, 1), _field_varint(37, 13),
        _field_varint(41, 1), _field_bytes(42, b""), _field_varint(47, 1), _field_varint(48, 1),
        _field_fixed32(52, 1.0), _field_varint(53, index), _field_bytes(54, group),
        _field_bytes(55, str(index + 1)), _field_varint(59, 1), _field_varint(65, 1), _field_varint(66, 1),
    ]
    return b"".join(parts)


def _png_bytes(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, "PNG", optimize=False)
    return stream.getvalue()


def _preview_for(brush: Brush) -> bytes:
    image = brush.preview.convert("RGBA") if brush.preview is not None else stroke_preview(brush)
    return _png_bytes(image.resize((48, 48), Image.Resampling.LANCZOS))


def _tip_for(brush: Brush) -> bytes:
    mask = pad_square(tip_mask(brush.main_tip()), 0).resize((200, 200), Image.Resampling.LANCZOS)
    image = Image.new("RGBA", mask.size, (0, 128, 128, 0))
    image.putalpha(mask)
    return _png_bytes(image)


def _encode(brushes: list[Brush]) -> bytes:
    records = [(_metadata_for(b, i), _preview_for(b), _tip_for(b)) for i, b in enumerate(brushes)]
    header = bytearray(HEADER_LEN)
    header[:10] = b"hsjbru\x00\x00\x00\x05"
    struct.pack_into(">I", header, 0x68, 1)
    struct.pack_into(">I", header, 0x6C, len(records[0][0]))
    struct.pack_into(">I", header, 0x70, len(records[0][1]))
    struct.pack_into(">I", header, 0x74, len(records[0][2]))
    parts: list[bytes] = [bytes(header)]
    cursor = HEADER_LEN
    for i, (metadata, preview, tip) in enumerate(records):
        if i:
            prefix = bytearray(PREFIX_LEN)
            prefix[7] = 1
            struct.pack_into(">I", prefix, 8, len(metadata))
            struct.pack_into(">I", prefix, 12, len(preview))
            struct.pack_into(">I", prefix, 16, len(tip))
            cursor += PREFIX_LEN
            end = cursor + len(metadata) + len(preview) + len(tip)
            struct.pack_into(">I", prefix, 24, end)
            parts.append(bytes(prefix))
        parts.extend((metadata, preview, tip))
        cursor += len(metadata) + len(preview) + len(tip)
    struct.pack_into(">I", header, 0x7C, cursor)
    parts[0] = bytes(header)
    data = b"".join(parts)
    data = bytearray(data)
    struct.pack_into(">I", data, 10, len(data))
    return bytes(data)


@register_codec
class HuaShiJieCodec(FormatCodec):
    id = "huashijie"
    name = "画世界 Pro 笔刷 (.bru)"
    extensions = (".bru",)
    can_read = True
    can_write = True

    def sniff(self, path: Path) -> Optional[FileInfo]:
        path = Path(path)
        if path.suffix.lower() != ".bru" or not path.is_file():
            return None
        try:
            data = path.read_bytes()
            if not data.startswith(MAGIC):
                return None
            _raw, records = _records(path)
        except (OSError, HuaShiJieFormatError):
            return None
        info = FileInfo(path=path, format_id=self.id, format_name=self.name,
                        version=f"HSJBRU v{int.from_bytes(data[8:10], 'big')}", brush_count=len(records))
        info.details["格式"] = "画世界 Pro 原生 BRU"
        info.details["笔刷数量"] = str(len(records))
        info.details["读取策略"] = "公共参数 + 原始字段扩展"
        info.problems.append("画世界 Pro 为移动端/平板软件；文件级检查通过后仍需在目标设备导入并核对笔迹。")
        return info

    def list_refs(self, path: Path) -> list[BrushRef]:
        _raw, records = _records(Path(path))
        refs: list[BrushRef] = []
        for i, item in enumerate(records):
            fields = item[3]
            name = _field_value(fields, 3, f"画世界笔刷 {i + 1}")
            if not isinstance(name, str) or not name.strip():
                name = f"画世界笔刷 {i + 1}"
            refs.append(BrushRef(name=name, key=str(i), loader=lambda i=i: self.read_one(Path(path), i),
                                 thumbnail=lambda item=item: _image(item[1]), detail="内嵌笔尖与预览"))
        return refs

    def read_one(self, path: Path, index: int) -> Brush:
        _raw, records = _records(Path(path))
        if not 0 <= index < len(records):
            raise IndexError(index)
        return _record_to_brush(Path(path), index, records[index])

    def read(self, path: Path) -> list[Brush]:
        _raw, records = _records(Path(path))
        return [_record_to_brush(Path(path), i, item) for i, item in enumerate(records)]

    def write(self, brushes: list[Brush], out_path: Path, **options) -> list[Path]:
        if not brushes:
            raise ValueError("没有要写入的画世界 Pro 笔刷")
        out_path = Path(out_path)
        if out_path.suffix.lower() != ".bru":
            out_path = out_path.with_suffix(".bru")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = _encode(brushes)
        tmp = out_path.with_suffix(out_path.suffix + ".part")
        tmp.write_bytes(data)
        try:
            # Structural validation catches truncated output before exposing it to the user.
            _records(tmp)
            tmp.replace(out_path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return [out_path]

