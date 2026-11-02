"""画世界 Pro adapter.

画世界 Pro 主要运行在手机和平板。桌面端不尝试猜测安装目录，转换时直接生成
``.bru`` 文件，用户再通过文件管理器或云盘传到设备中导入。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..core.model import Brush, BrushRef
from ..core.plugin import AppAdapter, AppInstall, InstallResult, codec, register_app
from ..core.system import documents_dir, safe_filename, unique_path


def default_export_dir() -> Path:
    return documents_dir() / "BrushBridge 导出" / "画世界Pro"


@register_app
class HuaShiJieAdapter(AppAdapter):
    id = "huashijie"
    name = "画世界 Pro"
    native_format = "huashijie"
    import_formats = ("huashijie", "abr")
    desktop = False

    def detect(self) -> list[AppInstall]:
        # The existence of an Android/iPad app cannot be inferred from a Windows path.
        # Keep a stable file-export entry so this never blocks conversion.
        return [AppInstall(app_id=self.id, name=self.name,
                           version="移动端/平板，电脑上无法检测",
                           data_dir=default_export_dir(),
                           extra={"mobile_only": True})]

    def list_brushes(self, inst: AppInstall) -> list[BrushRef]:
        refs: list[BrushRef] = []
        root = Path(inst.data_dir or default_export_dir())
        if not root.is_dir():
            return refs
        c = codec("huashijie")
        for path in sorted(root.rglob("*.bru")):
            try:
                for ref in c.list_refs(path):
                    ref.group = path.stem
                    ref.key = f"{path}::{ref.key}"
                    refs.append(ref)
            except Exception:
                continue
        return refs

    def install(self, inst: AppInstall, brushes: list[Brush], **options) -> InstallResult:
        if not brushes:
            return InstallResult(False, "没有要导出的笔刷。")
        out_dir = Path(options.get("out_dir") or inst.data_dir or default_export_dir())
        title = options.get("set_name") or options.get("name") or (
            brushes[0].name if len(brushes) == 1 else f"BrushBridge_{len(brushes)}笔刷")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            out = unique_path(out_dir / f"{safe_filename(title, '画世界Pro笔刷')}.bru")
            files = codec("huashijie").write(brushes, out)
        except Exception as exc:  # noqa: BLE001
            return InstallResult(False, f"导出画世界 Pro 笔刷失败：{exc}")
        return InstallResult(True,
                             f"已生成 {len(brushes)} 支画世界 Pro 笔刷：\n{files[0]}\n\n"
                             "请把 .bru 文件传到手机或平板，在画世界 Pro 的“导入画笔”中选择。",
                             files=list(files))

    def install_files(self, inst: AppInstall, files: list[Path]) -> InstallResult:
        out_dir = Path(inst.data_dir or default_export_dir())
        copied: list[Path] = []
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            for src in files:
                src = Path(src)
                if src.suffix.lower() not in (".bru", ".abr"):
                    continue
                dst = unique_path(out_dir / src.name)
                shutil.copy2(src, dst)
                copied.append(dst)
        except OSError as exc:
            return InstallResult(False, f"复制画世界 Pro 文件失败：{exc}", files=copied)
        if not copied:
            return InstallResult(False, "没有可传到画世界 Pro 的 .bru 或 .abr 文件。")
        return InstallResult(True,
                             f"已复制 {len(copied)} 个文件到：\n{out_dir}\n\n"
                             "请把文件传到手机或平板，在画世界 Pro 中选择“导入画笔”。",
                             files=copied)

    def is_running(self, inst: AppInstall) -> bool:
        return False

