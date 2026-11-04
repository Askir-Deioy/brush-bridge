"""Transactional file exports and per-brush evidence, independent of installed apps."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .model import Brush
from .storage import prepare_export, seal_source

TARGETS = {"photoshop": "abr", "csp": "csp", "udongman": "csp", "procreate": "procreate", "sai2": "sai2", "huashijie": "huashijie"}


@dataclass
class ExportResult:
    files: list[Path] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    report_path: Path | None = None
    cancelled: bool = False

    @property
    def succeeded(self):
        return sum(e["status"] == "success" for e in self.entries)

    @property
    def ok(self):
        return bool(self.succeeded) and all(e["status"] == "success" for e in self.entries) and not self.cancelled

    def summary(self):
        return (f"已完成 {self.succeeded} 支，失败 {sum(e['status'] == 'failed' for e in self.entries)} 支，"
                f"取消 {sum(e['status'] == 'cancelled' for e in self.entries)} 支。\n"
                "文件检查与实机兼容分别记录；本次未自动验证绘画软件的导入和笔迹。")


def capability_text(target):
    cid = TARGETS.get(target, target)
    if cid == "brushpkg":
        return "保存公共参数、素材、专有数据与原始来源；可重新打开并继续转换。"
    return {
        "abr": "Photoshop：支持基础笔尖、间距、压感和纹理；复杂曲线、湿边和多驱动可能近似。",
        "csp": "CSP／优动漫：输出 SUT，支持内嵌笔尖与纹理；混色和部分动态可能近似，按版本验收。",
        "procreate": "Procreate：输出 brushset；部分混色、双重笔刷和动态可能近似，需要 iPad 验证。",
        "huashijie": "画世界 Pro：输出 .bru；复杂参数保留在主存档，需在手机/平板导入并核对笔迹。",
        "sai2": "SAI2：输出工具与资源目录；单笔尖、尺寸等可转换，倾斜、双重笔刷和混合模式有限制。",
    }.get(cid, "将尽量映射目标格式支持的属性，具体变化见逐支报告。")


def _warnings(b, cid):
    if cid == "brushpkg" or b.source.format == cid:
        return
    if b.wet.enabled:
        b.warn("跨引擎混色使用目标格式参数近似，尚未验证实际混色笔迹。")
    if any(im.mode in {"RGB", "RGBA", "P"} for im in b.tips):
        b.warn("当前目标适配主要使用灰度笔尖，彩色或透明度通道可能被近似；原图保留在主存档。")
    if cid == "huashijie":
        b.warn_extras("画世界 Pro", edge=False, color_shift=False)
        if any(d.is_active() for d in (b.size_dynamic, b.opacity_dynamic, b.flow_dynamic)):
            b.warn("画世界 Pro BRU 当前只写出基础尺寸/不透明度/流量字段，压力、倾斜、速度和抖动动态需在设备中实机核对；原始动态保留在主存档。")
        if b.scatter > 0 or b.count > 1 or b.angle_jitter > 0 or b.angle_follow_stroke:
            b.warn("画世界 Pro BRU 当前未完成散布/多点/跟线旋转动态映射；原始规则保留在主存档。")
        if b.texture is not None:
            b.warn("画世界 Pro BRU 当前写出内嵌笔尖；通用纹理已保留在主存档，需在设备中实机核对。")
        if b.dual is not None:
            b.warn("画世界 Pro BRU 的双重笔刷暂按主笔尖写出；双重笔刷数据仍保留在主存档。")
        if b.wet.enabled:
            b.warn("画世界 Pro BRU 的混色参数尚未完成字段级映射；原始混色数据保留在主存档。")
    if b.texture is not None and any(getattr(b.texture.depth_dynamic, x) > 0 for x in ("pressure", "tilt", "velocity")) \
            and cid not in {"abr", "csp", "krita"}:
        b.warn(f"{cid} 不完整支持纹理深度随压力/倾斜/速度变化，已保留基础纹理并在主存档保存原始动态。")
    for label, dyn in (("尺寸", b.size_dynamic), ("不透明度", b.opacity_dynamic), ("流量", b.flow_dynamic)):
        active = [x for x in ("pressure", "tilt", "velocity", "jitter") if getattr(dyn, x) != 0]
        if any(getattr(dyn, x) < 0 for x in active):
            b.warn(f"{label}存在负向动态，目标映射可能近似；完整规则保留在主存档。")
        if len(active) > 1 or dyn.enabled_inputs or dyn.response_curves:
            b.warn(f"{label}的多输入/扩展动态规则可能无法完整输出，完整规则保留在主存档。")
        if dyn.pressure_curve and cid in {"abr", "sai2"}:
            b.warn(f"{label}的自定义压力曲线在此目标中可能被近似。")


def _losses(b: Brush) -> list[dict[str, str]]:
    """Return machine-readable loss/approximation entries alongside warnings.

    The human-facing Chinese note remains the source of detail.  This compact
    classification gives callers a stable way to filter reports without
    scraping prose, while deliberately avoiding claims about real-app fidelity.
    """
    markers = (
        ("missing_resource", ("缺少", "找不到")),
        ("unsupported", ("不支持", "无法", "不能")),
        ("ignored", ("忽略", "丢失")),
        ("approximation", ("近似", "截断", "只保留", "只写出", "暂按", "不完整支持")),
    )
    out: list[dict[str, str]] = []
    for note in b.notes:
        kind = next((kind for kind, words in markers if any(word in note for word in words)), None)
        if kind:
            out.append({"kind": kind, "message": note})
    for role, resource in b.resources.items():
        if resource.get("status") == "approximate":
            msg = f"{role} 使用可用近似；原始资源仍保存在主存档。"
            if not any(x["message"] == msg for x in out):
                out.append({"kind": "missing_resource", "message": msg})
    return out


def _unique(path):
    if not path.exists():
        return path
    for i in range(2, 100000):
        p = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not p.exists():
            return p
    raise ValueError("无法分配不重名的输出路径")


def _validate(c, files, count, stage, *, allow_missing_resources=False):
    if not files or not all(Path(p).is_file() and Path(p).stat().st_size for p in files):
        raise ValueError("输出为空或缺少文件")
    if c.id == "images":
        from PIL import Image
        for p in files:
            with Image.open(p) as im:
                im.verify()
        return
    if c.id == "mypaint":
        # A MyPaint export also emits ``*_prev.png`` beside each .myb.  The
        # preview is part of the deliverable but is not a MyPaint brush and
        # must not be passed back to the .myb parser during validation.
        files = [p for p in files if Path(p).suffix.lower() == ".myb"]
    if c.id == "sai2":
        tools = [p for p in files if p.suffix == ".saitdat"]
        decoded = [b for p in tools for b in c.read(p)]
        if len(tools) != count or len(decoded) != count:
            raise ValueError("SAI2 工具数量或结构回读不一致")
    else:
        decoded = []
        for p in files:
            decoded.extend(c.read(p))
    if len(decoded) != count:
        raise ValueError(f"输出笔刷数量不一致：期望 {count}，实际 {len(decoded)}")
    if c.id != "brushpkg":
        for brush in decoded:
            if any(r.get("status") == "missing" and r.get("required", role == "tip")
                   and not allow_missing_resources for role, r in brush.resources.items()):
                raise ValueError(f"输出回读发现缺失关键笔尖：{brush.name}")


def export_detailed(brushes, target, out_path, *, cancel_event=None, ignore_missing_optional=False,
                    progress=None, load_errors=None, **options):
    from .plugin import codec
    cid = TARGETS.get(target, target)
    c = codec(cid)
    out_path = Path(out_path)
    if not out_path.suffix and cid != "sai2":
        out_path = out_path.with_suffix(c.extensions[0])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = ExportResult()
    successful = []
    with tempfile.TemporaryDirectory(prefix=".brush-export-", dir=out_path.parent) as tmp:
        stage = Path(tmp)
        for i, original in enumerate(brushes):
            row = {"index": i, "name": original.name, "source_format": original.source.format,
                   "status": "failed", "file_validation": "not_run", "app_import": "not_verified",
                   "stroke_fidelity": "not_verified", "warnings": list(original.notes), "losses": [],
                   "error": "", "files": []}
            result.entries.append(row)
            if cancel_event is not None and cancel_event.is_set():
                row["status"] = "cancelled"
                result.cancelled = True
                continue
            try:
                seal_source(original)
                b = prepare_export(original) if cid != "brushpkg" else prepare_export_package(original)
                if cid != "brushpkg":
                    for role, resource in b.resources.items():
                        if resource.get("status") == "missing":
                            usable = (role == "tip" and (bool(b.tips) or
                                       (b.source.format == "procreate" and "procreate" in b.extensions))) or \
                                      (role == "grain" and b.texture is not None)
                            if usable:
                                resource["status"] = "approximate"
                                b.warn(f"缺少原始{role}素材“{resource.get('name', '')}”，已使用公共模型中的可用近似；"
                                       "完整来源信息仍保存在 .brushpkg。")
                            elif resource.get("required", role == "tip") or not ignore_missing_optional:
                                raise ValueError(f"缺少资源 {role}：{resource.get('name', '')}；请补充原图或明确忽略可选资源")
                            else:
                                b.warn(f"已按用户选择忽略缺少的可选资源 {role}：{resource.get('name', '')}")
                                if role == "grain":
                                    b.texture = None
                _warnings(b, cid)
                # A Procreate brushset stores each native archive and its PNGs in a ZIP.
                # Writing and reopening the entire archive once per brush makes an 18-brush
                # group appear frozen and leaves a long-lived temporary file tree.  The final
                # group write below validates the complete archive, so defer the per-brush
                # container check for multi-brush Procreate exports.
                if cid == "procreate" and len(brushes) > 1:
                    row.update(status="success", file_validation="deferred",
                               warnings=list(b.notes), losses=_losses(b))
                else:
                    test_path = stage / f"check-{i}" / ("brush" + c.extensions[0])
                    test_path.parent.mkdir()
                    files = c.write([b], test_path, **options)
                    _validate(c, files, 1, stage, allow_missing_resources=(cid == "procreate"))
                    row.update(status="success", file_validation="passed", warnings=list(b.notes), losses=_losses(b))
                successful.append(b)
            except Exception as exc:
                row["error"] = str(exc)
            if progress:
                progress(i + 1, len(brushes))
        if successful:
            try:
                final_dir = stage / "final"
                final_dir.mkdir()
                files = c.write(successful, final_dir / out_path.name, **options)
                _validate(c, files, len(successful), stage, allow_missing_resources=(cid == "procreate"))
                # Finish all validation before moving anything into the result directory.
                for child in list(final_dir.iterdir()):
                    dst = _unique(out_path.parent / child.name)
                    relative_files = [p.relative_to(child) for p in files if child.is_dir() and p.is_relative_to(child)]
                    os.replace(child, dst)
                    result.files.extend([dst / p for p in relative_files] if dst.is_dir() else [dst])
                for row, b in zip([e for e in result.entries if e["status"] == "success"], successful):
                    row["warnings"] = list(b.notes)
                    row["losses"] = _losses(b)
                    row["file_validation"] = "passed"
                    row["files"] = [str(p) for p in result.files]
            except Exception as exc:
                for row in result.entries:
                    if row["status"] == "success":
                        row.update(status="failed", file_validation="failed", error=f"最终输出失败：{exc}")
        report = {"schema_version": "0.1", "target": target, "codec": cid,
                  "capabilities": capability_text(target), "cancelled": result.cancelled,
                  "summary": {"succeeded": result.succeeded, "total": len(brushes) + len(load_errors or [])}, "brushes": result.entries,
                  "files": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in result.files]}
        for error in load_errors or []:
            result.entries.append({"name": "读取失败", "status": "failed", "file_validation": "not_run",
                                   "app_import": "not_verified", "stroke_fidelity": "not_verified",
                                   "warnings": [], "losses": [], "error": error, "files": []})
        report_path = _unique(out_path.parent / (out_path.stem + ".report.json"))
        temp_report = stage / "report.json"
        temp_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_report, report_path)
        result.report_path = report_path
    return result


def prepare_export_package(brush):
    import copy
    return copy.deepcopy(brush)
