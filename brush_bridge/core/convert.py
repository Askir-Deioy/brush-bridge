"""High level operations shared by the GUI and the CLI."""
from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .model import Brush, BrushRef
from .plugin import AppAdapter, AppInstall, FileInfo, InstallResult, apps, codec, codecs, sniff
from .system import export_dir, safe_filename

log = logging.getLogger(__name__)


@dataclass
class DetectedApp:
    adapter: AppAdapter
    install: Optional[AppInstall]  # None => not found on this computer
    note: str = ""

    @property
    def label(self) -> str:
        return self.install.label if self.install else self.adapter.name


def detect_all() -> list[DetectedApp]:
    """Every known app; installed ones first. Apps without a PC version are listed with a note."""
    found: list[DetectedApp] = []
    missing: list[DetectedApp] = []
    for a in apps():
        try:
            insts = a.detect()
        except Exception:
            log.exception("detect %s", a.id)
            insts = []
        if insts:
            found += [DetectedApp(a, i) for i in insts]
        elif not a.desktop:
            found.append(DetectedApp(a, None, "仅 iPad 版，电脑上无法检测版本；可直接导入或导出 .brush/.brushset 文件"))
        else:
            missing.append(DetectedApp(
                a, None,
                "未自动检测到安装位置；仍可导出对应格式，安装位置可以在其他磁盘或便携目录"))
    return found + missing


@dataclass
class OpenedFile:
    path: Path
    info: Optional[FileInfo]
    refs: list[BrushRef] = field(default_factory=list)
    error: str = ""


def open_file(path: Path) -> OpenedFile:
    path = Path(path)
    info = sniff(path)
    if info is None:
        hint = ""
        if zipfile.is_zipfile(path):
            hint = "（这是一个压缩包，请先解压再拖入里面的笔刷文件）"
        return OpenedFile(path, None, error=f"无法识别的文件格式{hint}")
    try:
        refs = codec(info.format_id).list_refs(path)
    except Exception as e:
        log.exception("list %s", path)
        return OpenedFile(path, info, error=f"读取失败：{e}")
    if info.brush_count < 0:
        info.brush_count = len(refs)
    return OpenedFile(path, info, refs)


def expand_paths(paths: list[Path]) -> list[Path]:
    """Folders → their brush files (recursively); known extensions only."""
    exts = {e for c in codecs() for e in c.extensions}
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            info = sniff(p)
            if info is not None:
                out.append(p)
            else:
                out += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in exts)
        else:
            out.append(p)
    return out


def diagnose(path: Path, detected: Optional[list[DetectedApp]] = None) -> Optional[FileInfo]:
    """Sniff a file and add compatibility problems for every installed app."""
    info = sniff(Path(path))
    if info is None:
        return None
    c = codec(info.format_id)
    for d in detected if detected is not None else detect_all():
        if not d.install:
            continue
        try:
            msg = c.compatibility(info, d.adapter.id, d.install.version)
        except Exception:
            msg = None
        if msg:
            info.problems.append(f"{d.install.label}：{msg}")
    return info


def load_brushes(refs: list[BrushRef]) -> tuple[list[Brush], list[str]]:
    brushes, errors = [], []
    for r in refs:
        try:
            brushes.append(r.load())
        except Exception as e:
            log.exception("load %s", r.name)
            errors.append(f"「{r.name}」读取失败：{e}")
    return brushes, errors


def writable_codecs() -> list:
    return [c for c in codecs() if c.can_write]


def app_export_codec(adapter: AppAdapter):
    """Return the first writable file format that the app can import.

    A file format remains useful when the application is on another drive, is a portable copy,
    or is not installed on this PC, so this lookup deliberately ignores installation detection.
    """
    ids = []
    for format_id in (adapter.native_format, *adapter.import_formats):
        if format_id and format_id not in ids:
            ids.append(format_id)
    for format_id in ids:
        try:
            c = codec(format_id)
        except KeyError:
            continue
        if c.can_write:
            return c
    return None


def export(brushes: list[Brush], codec_id: str, out_path: Path, **options) -> list[Path]:
    from .storage import prepare_export
    c = codec(codec_id)
    out_path = Path(out_path)
    if not out_path.suffix and c.extensions:
        out_path = out_path.with_suffix(c.extensions[0])
    import copy
    return c.write([copy.deepcopy(b) if codec_id == "brushpkg" else prepare_export(b) for b in brushes], out_path, **options)


from .reporting import export_detailed, capability_text


def install(target: DetectedApp, brushes: list[Brush], **options) -> InstallResult:
    if target.install is None:
        # The desktop GUI is file based.  Keep this compatibility fallback for
        # older callers, but describe the current manual-import workflow.
        c = app_export_codec(target.adapter)
        if c is None:
            return InstallResult(False, f"没有可导出的 {target.adapter.name} 文件格式。")
        out_path = options.get("out_path")
        if out_path is None:
            out_dir = Path(options.get("out_dir") or export_dir(f"{target.adapter.id}-手动导入"))
            name = options.get("file_name") or (brushes[0].name if len(brushes) == 1
                                                  else f"BrushBridge_{len(brushes)}笔刷")
            out_path = out_dir / safe_filename(name)
        ignored = {"direct", "out_dir", "out_path", "file_name"}
        write_options = {k: v for k, v in options.items() if k not in ignored}
        try:
            files = export(brushes, c.id, Path(out_path), **write_options)
        except Exception as e:
            log.exception("fallback export for %s", target.adapter.id)
            return InstallResult(False, f"导出 {target.adapter.name} 文件失败：{e}")
        return InstallResult(True, f"已导出 {target.adapter.name} 文件。请在目标软件中手动导入。",
                             files=files)
    try:
        return target.adapter.install(target.install, brushes, **options)
    except NotImplementedError:
        return InstallResult(False, f"当前版本只生成 {target.adapter.name} 的导入文件，请使用「导出为文件」后在软件中导入。")
    except Exception as e:
        log.exception("install into %s", target.adapter.id)
        return InstallResult(False, f"导入 {target.label} 时出错：{e}")
