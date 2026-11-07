"""Brush Bridge desktop GUI (PySide6). Artist-first: pick brushes, pick a target, click once."""
from __future__ import annotations

import logging
import os
import sys
import traceback
import threading
import json
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QFontDatabase, QIcon, QLinearGradient,
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QDialogButtonBox, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QSplitter, QCheckBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget, QSpinBox,
)

from .. import __version__
from ..apps.sai2 import app_install_from_exe
from ..core import convert
from ..core.convert import DetectedApp, OpenedFile
from ..core.model import BrushRef
from ..core.plugin import FileInfo, app as plugin_app, codecs, load_plugins
from ..core.system import export_dir, open_in_explorer, safe_filename
from .brush_grid import (THUMB, BrushGridView, BrushListModel, GroupTree, PixmapCache, ThumbnailLoader,
                         pil_to_qimage)

log = logging.getLogger(__name__)

ROLE_SRC = Qt.UserRole + 2

# Friendlier names for export targets (codec names come from the format plugins).
CODEC_LABELS = {
    "csp": "Clip Studio Paint 笔刷 (.sut)",
    "brushpkg": "通用笔刷分享包 / 中间格式 (.brushpkg)",
    "huashijie": "画世界 Pro 笔刷 (.bru)",
}


# Exported files may be opened on another computer with an older app; let the user pick its version.
EXPORT_VERSIONS = {
    "csp": ("Clip Studio Paint", "target_version",
            [("自动（兼容 2.0 及以上，推荐）", ""), ("1.x 旧版", "1.13"), ("2.x – 4.x", "2.0"), ("5.x 及以上", "5.0")]),
    "krita": ("Krita", "krita_version", [("Krita 5（推荐）", ""), ("Krita 4", "4")]),
    "mypaint": ("MyPaint / GIMP / Krita", "mypaint_version",
                [("新版 v3（MyPaint 1.1+、GIMP 2.10+、Krita 5，推荐）", 3), ("旧版 v2（MyPaint 1.0 及更早）", 2)]),
}


def codec_label(c) -> str:
    return CODEC_LABELS.get(c.id, c.name)

STYLE = """
QWidget { font-size: 13px; }
QMainWindow, QTabWidget::pane { background: #f6f6f8; }
QTabBar::tab { padding: 8px 18px; font-size: 14px; }
QTabBar::tab:selected { font-weight: bold; }
QListWidget#sources { background: white; border: 1px solid #ddd; border-radius: 6px; padding: 4px; }
QListWidget#sources::item { padding: 7px 6px; border-radius: 4px; }
QListWidget#sources::item:selected { background: #2f6fed; color: white; }
QTreeWidget#groups { background: white; border: 1px solid #ddd; border-radius: 6px; padding: 4px; }
QTreeWidget#groups::item { padding: 4px 2px; }
QTreeWidget#groups::item:selected { background: #2f6fed; color: white; border-radius: 4px; }
QListView#grid { background: white; border: 1px solid #ddd; border-radius: 6px; }
QPushButton { padding: 6px 14px; border-radius: 5px; border: 1px solid #c8c8d0; background: white; }
QPushButton:hover { background: #eef2fb; }
QPushButton#primary { background: #2f6fed; color: white; border: none; font-size: 15px; font-weight: bold; padding: 10px 26px; }
QPushButton#primary:hover { background: #255bd0; }
QPushButton#primary:disabled { background: #a9bde8; }
QLabel#title { font-size: 16px; font-weight: bold; }
QLabel#hint { color: #777; }
QFrame#drop { border: 2px dashed #9aa7c2; border-radius: 12px; background: white; }
"""


# ---------------------------------------------------------------------------------------------
# background work
# ---------------------------------------------------------------------------------------------
class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self):
        try:
            try:
                res = self.fn()
            except Exception as e:
                log.exception("background job failed")
                self.signals.failed.emit(f"{e}\n\n{traceback.format_exc(limit=3)}")
            else:
                self.signals.done.emit(res)
        except RuntimeError:  # receiver already gone (app shutting down)
            log.debug("background job finished after shutdown")


_jobs: set = set()


def run_bg(fn: Callable[[], Any], on_done: Callable[[Any], None], on_fail: Optional[Callable[[str], None]] = None):
    job = _Job(fn)
    _jobs.add(job.signals)

    def finish(*_):
        _jobs.discard(job.signals)

    job.signals.done.connect(on_done)
    job.signals.done.connect(finish)
    job.signals.failed.connect(on_fail or (lambda msg: QMessageBox.warning(None, "出错了", msg)))
    job.signals.failed.connect(finish)
    QThreadPool.globalInstance().start(job)


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def pil_to_pixmap(img: Image.Image, size: int = THUMB) -> QPixmap:
    """GUI thread only; workers use ``pil_to_qimage``."""
    return QPixmap.fromImage(pil_to_qimage(img, size))


def result_dialog(parent, ok: bool, message: str, files: list[Path]):
    # QMessageBox puts long text into one growing label and may let the native
    # dialog push the buttons below the screen.  Use a small modeless dialog
    # with a dedicated scrollable report and a fixed button row instead.
    box = QDialog(parent)
    box.setWindowTitle("完成" if ok else "没有完全成功")
    box.setWindowModality(Qt.NonModal)
    box.setAttribute(Qt.WA_DeleteOnClose)
    box.setMinimumSize(560, 360)
    box.resize(760, 560)

    layout = QVBoxLayout(box)
    report = QTextBrowser(box)
    report.setReadOnly(True)
    report.setPlainText(message)
    report.setLineWrapMode(QTextBrowser.WidgetWidth)
    layout.addWidget(report, 1)

    if files:
        files_box = QTextBrowser(box)
        files_box.setReadOnly(True)
        files_box.setMaximumHeight(110)
        files_box.setPlainText("生成文件：\n" + "\n".join(str(f) for f in files))
        layout.addWidget(files_box)

    buttons = QDialogButtonBox(box)
    if files:
        open_button = buttons.addButton("打开文件所在位置", QDialogButtonBox.ActionRole)
        open_button.clicked.connect(lambda: open_in_explorer(Path(files[0])))
    buttons.addButton("好的", QDialogButtonBox.AcceptRole)
    buttons.accepted.connect(box.accept)
    layout.addWidget(buttons, 0)

    # Keep a Python reference until the non-modal box finishes.  Without this,
    # a large batch result can leave a native dialog alive without a usable
    # Python wrapper on Windows.
    parent._active_result_dialog = box

    def on_finished(*_):
        if getattr(parent, "_active_result_dialog", None) is box:
            parent._active_result_dialog = None

    box.finished.connect(on_finished)
    box.open()


# ---------------------------------------------------------------------------------------------
# Page 1: move brushes
# ---------------------------------------------------------------------------------------------
class TransferPage(QWidget):
    export_progress = Signal(int, int)

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.detected: list[DetectedApp] = []
        self.files: list[OpenedFile] = []
        self.source_token = 0
        self.thumb_loader = ThumbnailLoader(pil_to_qimage, max_threads=2, parent=self)
        self.thumb_cache = PixmapCache(2000)
        self.model = BrushListModel(self.thumb_loader, self.thumb_cache, self)
        self.model.checkedChanged.connect(self._update_count)

        root = QHBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split)

        # --- left: sources
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        t = QLabel("① 选择笔刷文件")
        t.setObjectName("title")
        lv.addWidget(t)
        self.sources = QListWidget()
        self.sources.setObjectName("sources")
        self.sources.currentItemChanged.connect(self._source_changed)
        lv.addWidget(self.sources, 1)
        add = QPushButton("＋ 打开笔刷文件…")
        add.clicked.connect(self._open_dialog)
        lv.addWidget(add)
        h = QLabel("提示：也可以把笔刷文件或文件夹\n直接拖进这个窗口（比如从 iPad\n传过来的 Procreate 笔刷）。\n无需安装目标软件即可转换。")
        h.setObjectName("hint")
        lv.addWidget(h)
        split.addWidget(left)

        # --- right: brushes + target
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.title = QLabel("② 勾选要转换的笔刷")
        self.title.setObjectName("title")
        head.addWidget(self.title, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 搜索笔刷名称")
        self.search.textChanged.connect(self._filter)
        self.search.setMaximumWidth(220)
        head.addWidget(self.search)
        rv.addLayout(head)

        self.grid = BrushGridView(self.model, parent=self)
        self.grid.setObjectName("grid")
        self.grid.checkAllRequested.connect(lambda: self._check_all(True))
        self.proxy = self.grid.proxy
        self.group = GroupTree()
        self.group.groupChanged.connect(self._filter)
        self.group.setVisible(False)
        body = QSplitter(Qt.Horizontal)
        body.setChildrenCollapsible(False)
        body.addWidget(self.group)
        body.addWidget(self.grid)
        body.setStretchFactor(1, 1)
        body.setSizes([210, 700])
        rv.addWidget(body, 1)
        self.status = QLabel("")
        self.status.setObjectName("hint")
        rv.addWidget(self.status)
        self.export_progress.connect(lambda done, total: self.status.setText(f"已处理 {done}/{total} 支笔刷…"))

        sel = QHBoxLayout()
        for text, fn, tip in (("全选", lambda: self._check_all(True), "勾选当前显示的全部笔刷"),
                              ("全不选", lambda: self._check_all(False), "取消勾选当前显示的全部笔刷"),
                              ("反选", self._invert, "把当前显示的笔刷勾选状态反过来")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            sel.addWidget(b)
        sel.addStretch(1)
        inspect = QPushButton("查看所选笔刷数据")
        inspect.clicked.connect(self._inspect_brush)
        sel.addWidget(inspect)
        self.count = QLabel("已选 0 个")
        sel.addWidget(self.count)
        rv.addLayout(sel)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        rv.addWidget(line)
        tgt = QHBoxLayout()
        t3 = QLabel("③ 选择目标格式")
        t3.setObjectName("title")
        tgt.addWidget(t3)
        self.target = QComboBox()
        self.target.setMinimumWidth(320)
        self.target.setMinimumHeight(34)
        tgt.addWidget(self.target, 1)
        self.go = QPushButton("开始转换")
        self.go.setObjectName("primary")
        self.go.setMinimumWidth(180)
        self.go.clicked.connect(self._go)
        tgt.addWidget(self.go)
        self.cancel_event = threading.Event()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_event.set)
        tgt.addWidget(self.cancel_button)
        rv.addLayout(tgt)
        self.capabilities = QLabel("")
        self.capabilities.setWordWrap(True)
        self.capabilities.setObjectName("hint")
        rv.addWidget(self.capabilities)
        self.ignore_optional = QCheckBox("允许忽略缺失颗粒（在报告中记录）")
        rv.addWidget(self.ignore_optional)
        self.target.currentIndexChanged.connect(self._target_changed)
        split.addWidget(right)
        split.setSizes([260, 900])

    # sources ---------------------------------------------------------------------------------
    def set_detected(self, detected: list[DetectedApp]):
        self.detected = detected
        self._rebuild_sources()
        self._rebuild_targets()

    def _header(self, text: str):
        it = QListWidgetItem(text)
        it.setFlags(Qt.NoItemFlags)
        f = it.font()
        f.setBold(True)
        it.setFont(f)
        it.setForeground(QColor("#666"))
        self.sources.addItem(it)

    def _rebuild_sources(self):
        cur = self.sources.currentItem().data(ROLE_SRC) if self.sources.currentItem() else None
        self.sources.blockSignals(True)
        self.sources.clear()
        self._header("已打开的笔刷文件")
        if self.files:
            for f in self.files:
                n = f.info.brush_count if f.info else 0
                it = QListWidgetItem(f"📄 {f.path.name}" + (f"（{n}）" if n >= 0 else ""))
                it.setData(ROLE_SRC, ("file", f))
                it.setToolTip(str(f.path))
                self.sources.addItem(it)
        self.sources.blockSignals(False)
        for i in range(self.sources.count()):
            if cur is not None and self.sources.item(i).data(ROLE_SRC) is not None and \
                    self.sources.item(i).data(ROLE_SRC)[1] is cur[1]:
                self.sources.setCurrentRow(i)
                return

    @staticmethod
    def _target_key(data) -> Optional[tuple]:
        if not data:
            return None
        kind, obj = data
        if kind == "codec":
            return kind, obj.id
        if kind == "app_export":
            app_target, export_codec = obj
            return kind, app_target.adapter.id, export_codec.id
        return kind, obj.adapter.id, obj.label

    def _rebuild_targets(self):
        prev = self._target_key(self.target.currentData())
        self.target.blockSignals(True)
        self.target.clear()
        for c in codecs():
            if c.can_write:
                self.target.addItem(f"导出为 {codec_label(c)}", ("codec", c))
        if prev is not None:  # keep the artist's choice if the format list is rebuilt
            for i in range(self.target.count()):
                if self._target_key(self.target.itemData(i)) == prev:
                    self.target.setCurrentIndex(i)
                    break
        self.target.blockSignals(False)
        self._target_changed()

    def _target_changed(self, *_):
        data = self.target.currentData()
        if data and data[0] == "codec":
            self.capabilities.setText(convert.capability_text(data[1].id))
        else:
            self.capabilities.setText("选择目标格式并导出文件；也可以选择 .brushpkg 作为通用分享/中间格式。")

    def _inspect_brush(self):
        refs = self.checked_refs()
        if not refs:
            QMessageBox.information(self, "选择笔刷", "请先勾选一支笔刷。")
            return
        def work():
            from ..core.storage import encode
            b = refs[0].load()
            # Display the persistent public model without printing raw source bytes.
            values = {k: getattr(b, k) for k in ("name", "group", "size_px", "spacing", "opacity", "flow",
                      "tips", "texture", "dual", "size_dynamic", "opacity_dynamic", "flow_dynamic", "wet", "resources", "notes")}
            return json.dumps(encode(values, lambda data, suffix: f"已保存资源（{len(data)} 字节，{suffix}）"),
                              ensure_ascii=False, indent=2)
        def done(text):
            box = QMessageBox(self)
            box.setWindowTitle("笔刷数据")
            box.setText("公共参数、素材与动态规则；预览不代表目标软件的实际笔迹。")
            box.setDetailedText(text)
            box.exec()
        run_bg(work, done)

    def add_files(self, paths: list[Path]):
        paths = convert.expand_paths(paths)
        if not paths:
            return
        self.win.busy(f"正在读取 {len(paths)} 个文件…")

        def work():
            return [convert.open_file(p) for p in paths]

        def done(opened: list[OpenedFile]):
            self.win.idle()
            bad = [o for o in opened if o.error]
            good = [o for o in opened if not o.error]
            known = {str(f.path) for f in self.files}
            self.files += [g for g in good if str(g.path) not in known]
            self._rebuild_sources()
            if good:
                for i in range(self.sources.count()):
                    src = self.sources.item(i).data(ROLE_SRC)
                    if src and src[0] == "file" and src[1].path == good[-1].path:
                        self.sources.setCurrentRow(i)
            if bad:
                QMessageBox.warning(self, "有文件无法打开", "\n".join(f"{b.path.name}：{b.error}" for b in bad))

        run_bg(work, done, lambda m: (self.win.idle(), QMessageBox.warning(self, "出错了", m)))

    def _open_dialog(self):
        exts = " ".join(f"*{e}" for c in codecs() for e in c.extensions)
        files, _ = QFileDialog.getOpenFileNames(self, "选择笔刷文件", str(Path.home() / "Downloads"),
                                                f"笔刷文件 ({exts});;所有文件 (*)")
        if files:
            self.add_files([Path(f) for f in files])

    def _source_changed(self, cur: QListWidgetItem, _prev=None):
        if cur is None or cur.data(ROLE_SRC) is None:
            return
        kind, obj = cur.data(ROLE_SRC)
        self.source_token += 1
        token = self.source_token
        self.model.set_refs([], None)
        self.title.setText(f"② 勾选要转换的笔刷 — {cur.text()}")
        self.status.setText("正在读取笔刷列表…")

        def work():
            if kind == "app":
                return obj.adapter.list_brushes(obj.install)
            return obj.refs

        def done(refs):
            if token != self.source_token:
                return
            self._show_refs(refs, kind, obj)

        run_bg(work, done, lambda m: self.status.setText("读取失败：" + m.splitlines()[0]))

    @property
    def refs(self) -> list[BrushRef]:
        return self.model.refs

    @staticmethod
    def _source_ns(kind: str, obj) -> tuple:
        """Stable identity of a brush source, so thumbnails stay cached when switching back."""
        if kind == "app" and obj is not None:
            inst = obj.install
            return ("app", obj.adapter.id, inst.label if inst else "", str(inst.data_dir) if inst else "")
        if kind == "file" and obj is not None:
            try:
                mtime = obj.path.stat().st_mtime_ns
            except OSError:
                mtime = 0
            return ("file", str(obj.path), mtime)
        return ("other", id(obj))

    def _show_refs(self, refs: list[BrushRef], kind: str, obj):
        ns = self._source_ns(kind, obj) if obj is not None else None
        self.model.set_refs(refs, ns)
        log.info("showing %d brushes from %s", len(refs), ns)
        self.group.set_groups(r.group for r in refs)
        self.group.setVisible(self.group.has_groups)
        self.empty_msg = "没有找到笔刷。"
        if not refs and kind == "app" and obj is not None and obj.adapter.id == "procreate":
            self.empty_msg = "Procreate 只在 iPad 上运行：请在 iPad 上分享笔刷，把 .brushset / .brush 文件传到电脑后拖进来。"
        self._update_count()
        self._filter()
        self.grid.scrollToTop()

    # selection -------------------------------------------------------------------------------
    def _filter(self, *_):
        self.proxy.set_filter(self.search.text(), self.group.current_group())
        total, shown = len(self.refs), self.proxy.rowCount()
        if not total:
            self.status.setText(getattr(self, "empty_msg", "没有找到笔刷。"))
        else:
            head = f"共 {total} 个笔刷" + (f"，当前显示 {shown} 个" if shown != total else "")
            self.status.setText(f"{head}。单击笔刷即可勾选 / 取消，按住 Shift 单击可连续勾选一串。")
        self.grid.schedule_visible()

    def _check_all(self, on: bool):
        self.model.set_checked(self.proxy.source_rows(), on)

    def _invert(self):
        self.model.invert(self.proxy.source_rows())

    def checked_refs(self) -> list[BrushRef]:
        return self.model.checked_refs()

    def _update_count(self, *_):
        n = len(self.model.checked)
        self.count.setText(f"已选 {n} 个")
        self.go.setText(f"开始转换（{n}）" if n else "开始转换")

    # go --------------------------------------------------------------------------------------
    def _go(self):
        refs = self.checked_refs()
        if not refs:
            QMessageBox.information(self, "还没选笔刷", "请先在中间勾选要转换的笔刷。")
            return
        data = self.target.currentData()
        if not data:
            return
        kind, obj = data
        if kind == "codec":
            ext = obj.extensions[0] if obj.extensions else ""
            default = export_dir("导出") / (refs[0].name if len(refs) == 1 else f"BrushBridge_{len(refs)}笔刷")
            path, _ = QFileDialog.getSaveFileName(self, "选择导出位置", str(default) + ext,
                                                  f"{codec_label(obj)} (*{ext})")
            if not path:
                return
            opts = {}
            opts["ignore_missing_optional"] = self.ignore_optional.isChecked()
            if obj.id in EXPORT_VERSIONS:
                app_name, key, choices = EXPORT_VERSIONS[obj.id]
                label, ok = QInputDialog.getItem(self, "目标软件版本", f"要在哪个版本的 {app_name} 里使用？",
                                                 [c[0] for c in choices], 0, False)
                if not ok:
                    return
                value = dict(choices)[label]
                if value:
                    opts[key] = value
            self._run_transfer(refs, lambda brushes, errors: self._export(obj, brushes, Path(path), load_errors=errors, **opts),
                               report_empty=True)

    def _export(self, c, brushes, path: Path, **opts):
        result = convert.export_detailed(brushes, c.id, path, cancel_event=self.cancel_event,
                                         progress=self.export_progress.emit, **opts)
        notes = sorted({n for row in result.entries for n in row["warnings"]})
        msg = result.summary()
        for row in result.entries:
            if row["error"]:
                msg += f"\n{row['name']}：{row['error']}"
        if notes:
            msg += "\n\n转换提示：\n· " + "\n· ".join(notes)
        files = result.files + ([result.report_path] if result.report_path else [])
        return result.ok, msg, files, False

    def _install(self, target: DetectedApp, brushes, **opts):
        res = convert.install(target, brushes, **opts)
        return res.ok, res.message, res.files, res.needs_app_closed

    def _run_transfer(self, refs, action, report_empty=False):
        self.win.busy(f"正在转换 {len(refs)} 个笔刷…")
        self.go.setEnabled(False)
        self.cancel_event.clear()
        self.cancel_button.setEnabled(True)

        def work():
            brushes, errors = convert.load_brushes(refs)
            if not brushes and not report_empty:
                return False, "所选笔刷都读取失败：\n" + "\n".join(errors), [], False
            ok, msg, files, needs_closed = action(brushes, errors)
            if errors:
                msg += "\n\n以下笔刷读取失败：\n" + "\n".join(errors)
            return ok, msg, files, needs_closed

        def done(res):
            self.win.idle()
            self.go.setEnabled(True)
            self.cancel_button.setEnabled(False)
            ok, msg, files, needs_closed = res
            if needs_closed:
                QMessageBox.information(self, "请先关闭软件", msg)
                return
            result_dialog(self, ok, msg, [Path(f) for f in files])

        def fail(m):
            self.win.idle()
            self.go.setEnabled(True)
            self.cancel_button.setEnabled(False)
            QMessageBox.warning(self, "出错了", m)

        run_bg(work, done, fail)


# ---------------------------------------------------------------------------------------------
# Page 2: diagnose & fix
# ---------------------------------------------------------------------------------------------
class DropZone(QFrame):
    dropped = Signal(list)

    def __init__(self, text: str):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setMinimumHeight(140)
        lay = QVBoxLayout(self)
        lab = QLabel(text)
        lab.setAlignment(Qt.AlignCenter)
        f = lab.font()
        f.setPointSize(13)
        lab.setFont(f)
        lay.addWidget(lab)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.dropped.emit([Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()])
        e.acceptProposedAction()


class DiagnosePage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.info: Optional[FileInfo] = None
        v = QVBoxLayout(self)
        t = QLabel("笔刷导入不进去？在这里检查并自动修复")
        t.setObjectName("title")
        v.addWidget(t)
        self.zone = DropZone("把导入失败的笔刷文件拖到这里\n（或点击下面的按钮选择文件）")
        self.zone.dropped.connect(lambda ps: ps and self.check(ps[0]))
        v.addWidget(self.zone)
        pick = QPushButton("选择文件…")
        pick.clicked.connect(self._pick)
        v.addWidget(pick, 0, Qt.AlignLeft)
        self.report = QTextBrowser()
        v.addWidget(self.report, 1)
        self.fixes = QHBoxLayout()
        v.addLayout(self.fixes)

    def _pick(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择笔刷文件", str(Path.home() / "Downloads"))
        if f:
            self.check(Path(f))

    def check(self, path: Path):
        self.report.setHtml(f"<p>正在检查 {path.name} …</p>")
        detected = self.win.detected

        def work():
            return convert.diagnose(path, detected)

        run_bg(work, lambda info: self._show(path, info))

    def _show(self, path: Path, info: Optional[FileInfo]):
        while self.fixes.count():
            w = self.fixes.takeAt(0).widget()
            if w:
                w.deleteLater()
        if info is None:
            self.report.setHtml(f"<h3>{path.name}</h3><p>❌ 无法识别这个文件。支持的格式：</p><ul>" +
                                "".join(f"<li>{c.name}</li>" for c in codecs()) + "</ul>")
            return
        rows = "".join(f"<tr><td style='color:#666;padding-right:12px'>{k}</td><td>{v}</td></tr>" for k, v in
                       [("格式", info.format_name), ("版本", info.version or "—"),
                        ("笔刷数量", str(info.brush_count) if info.brush_count >= 0 else "—"), *info.details.items()])
        html = f"<h3>{path.name}</h3><table>{rows}</table>"
        if info.problems:
            html += "<h4>⚠️ 发现的问题</h4><ul>" + "".join(f"<li>{p}</li>" for p in info.problems) + "</ul>"
        else:
            html += "<p>✅ 没有发现兼容性问题。如果仍然导入失败，请确认是拖到了正确的面板（例如 CSP 要拖到「辅助工具」面板）。</p>"
        if info.fixes:
            html += "<p>可以点击下面的按钮自动修复：</p>"
        self.report.setHtml(html)
        for fx in info.fixes:
            b = QPushButton(f"🔧 {fx.title}")
            b.setToolTip(fx.description)
            b.clicked.connect(lambda _=False, fx=fx: self._apply(path, fx))
            self.fixes.addWidget(b)
        self.fixes.addStretch(1)

    def _apply(self, path: Path, fx):
        out = export_dir("修复后的笔刷")
        self.win.busy("正在修复…")

        def done(files):
            self.win.idle()
            result_dialog(self, True, f"已生成修复后的文件，请导入这个新文件：\n{files[0].name}" if files else "完成", files)

        run_bg(lambda: fx.apply(path, out), done, lambda m: (self.win.idle(), QMessageBox.warning(self, "修复失败", m)))


# ---------------------------------------------------------------------------------------------
# Page 3: convenience tools
# ---------------------------------------------------------------------------------------------
class UtilityPage(QWidget):
    """Small, explicit helpers that need a user-selected local application."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.install = None
        self.refs: list[BrushRef] = []
        self.settings = QSettings("BrushBridge", "BrushBridge")

        root = QVBoxLayout(self)
        title = QLabel("便捷工具")
        title.setObjectName("title")
        root.addWidget(title)
        hint = QLabel("先选择本机 SAI2 的 sai2.exe，再查看、安装或导出单支笔刷。")
        hint.setObjectName("hint")
        root.addWidget(hint)

        path_row = QHBoxLayout()
        self.exe_path = QLineEdit(str(self.settings.value("sai2/exe", "")))
        self.exe_path.setPlaceholderText("选择 SAI2.exe")
        path_row.addWidget(self.exe_path, 1)
        browse = QPushButton("选择 SAI2.exe…")
        browse.clicked.connect(self._browse_exe)
        path_row.addWidget(browse)
        load = QPushButton("读取本地笔刷")
        load.clicked.connect(self._load_sai2)
        path_row.addWidget(load)
        root.addLayout(path_row)

        self.status = QLabel("尚未选择 SAI2。")
        self.status.setObjectName("hint")
        root.addWidget(self.status)

        self.brushes = QListWidget()
        self.brushes.setObjectName("sources")
        root.addWidget(self.brushes, 1)

        group_row = QHBoxLayout()
        group_row.addWidget(QLabel("安装工具组"))
        self.group_name = QLineEdit("导入笔刷")
        group_row.addWidget(self.group_name, 1)
        root.addLayout(group_row)

        action_row = QHBoxLayout()
        self.install_button = QPushButton("安装选中笔刷")
        self.install_button.clicked.connect(self._install_selected)
        self.install_button.setEnabled(False)
        action_row.addWidget(self.install_button)
        self.export_button = QPushButton("导出选中笔刷…")
        self.export_button.clicked.connect(self._export_selected)
        self.export_button.setEnabled(False)
        action_row.addWidget(self.export_button)
        picker = QPushButton("打开独立取色器")
        picker.clicked.connect(self._open_picker)
        action_row.addWidget(picker)
        action_row.addStretch(1)
        root.addLayout(action_row)

    def _open_picker(self):
        dialog = ColorPickerDialog(self)
        self._picker_dialog = dialog
        dialog.show()

    def _browse_exe(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 SAI2.exe", self.exe_path.text() or str(Path.home()), "SAI2 (sai2.exe)")
        if path:
            self.exe_path.setText(path)
            self._load_sai2()

    def _load_sai2(self):
        path = Path(self.exe_path.text().strip())
        inst = app_install_from_exe(path)
        if inst is None:
            self.install = None
            self.refs = []
            self.brushes.clear()
            self.install_button.setEnabled(False)
            self.export_button.setEnabled(False)
            self.status.setText("找不到可用的 sai2.exe。")
            return
        self.settings.setValue("sai2/exe", str(path))
        self.install = inst
        try:
            self.refs = plugin_app("sai2").list_brushes(inst)
        except Exception as exc:
            self.refs = []
            self.status.setText(f"读取 SAI2 设置失败：{exc}")
            return
        self.brushes.clear()
        for ref in self.refs:
            item = QListWidgetItem(ref.name)
            item.setData(Qt.UserRole, ref)
            item.setToolTip(ref.detail or ref.group or ref.name)
            self.brushes.addItem(item)
        self.status.setText(f"{inst.label}。本地笔刷 {len(self.refs)} 支，设置目录：{inst.data_dir}")
        enabled = bool(self.refs)
        self.install_button.setEnabled(enabled)
        self.export_button.setEnabled(enabled)

    def _selected_ref(self) -> Optional[BrushRef]:
        item = self.brushes.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _install_selected(self):
        ref = self._selected_ref()
        if ref is None or self.install is None:
            return
        group = self.group_name.text().strip() or "导入笔刷"
        self.win.busy("正在安装 SAI2 笔刷…")
        self.install_button.setEnabled(False)

        def work():
            brush = ref.load()
            return plugin_app("sai2").install(self.install, [brush], group_name=group, prefer_native=True)

        def done(result):
            self.win.idle()
            self.install_button.setEnabled(True)
            if result.ok:
                result_dialog(self, True, result.message, result.files)
            else:
                QMessageBox.warning(self, "SAI2 安装失败", result.message)

        run_bg(work, done, lambda msg: (self.win.idle(), self.install_button.setEnabled(True),
                                        QMessageBox.warning(self, "SAI2 安装失败", msg)))

    def _export_selected(self):
        ref = self._selected_ref()
        if ref is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择 SAI2 导出目录", str(Path.home() / "Downloads"))
        if not folder:
            return
        self.win.busy("正在导出 SAI2 笔刷…")
        self.export_button.setEnabled(False)

        def work():
            brush = ref.load()
            destination = Path(folder) / safe_filename(ref.name)
            return convert.export([brush], "sai2", destination, prefer_native=True)

        def done(files):
            self.win.idle()
            self.export_button.setEnabled(True)
            result_dialog(self, True, "已导出选中的 SAI2 笔刷。", files)

        run_bg(work, done, lambda msg: (self.win.idle(), self.export_button.setEnabled(True),
                                        QMessageBox.warning(self, "SAI2 导出失败", msg)))


class HueRingWidget(QWidget):
    changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hue = 0.0
        self.setMinimumSize(180, 180)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 12
        rect = self.rect().center()
        outer = side / 2
        area = self.rect().center().x() - outer, self.rect().center().y() - outer, side, side
        gradient = QConicalGradient(self.rect().center(), -90)
        for pos, color in ((0.0, "#ff0000"), (1/6, "#ffff00"), (2/6, "#00ff00"),
                           (3/6, "#00ffff"), (4/6, "#0000ff"), (5/6, "#ff00ff"), (1.0, "#ff0000")):
            gradient.setColorAt(pos, QColor(color))
        p.setBrush(QBrush(gradient))
        p.setPen(Qt.NoPen)
        p.drawEllipse(*area)
        inner = side - 30
        p.setBrush(self.palette().window())
        p.drawEllipse(self.rect().center().x() - inner / 2, self.rect().center().y() - inner / 2, inner, inner)
        angle = self.hue / 360 * 2 * 3.141592653589793 - 3.141592653589793 / 2
        x = self.rect().center().x() + (outer - 8) * __import__("math").cos(angle)
        y = self.rect().center().y() + (outer - 8) * __import__("math").sin(angle)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(Qt.black, 3))
        p.drawEllipse(x - 6, y - 6, 12, 12)

    def mousePressEvent(self, event):
        self._set_from_pos(event.position().x(), event.position().y())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._set_from_pos(event.position().x(), event.position().y())

    def _set_from_pos(self, x, y):
        import math
        dx, dy = x - self.width() / 2, y - self.height() / 2
        self.hue = (math.degrees(math.atan2(dy, dx)) + 90) % 360
        self.changed.emit(self.hue)
        self.update()


class SaturationValueWidget(QWidget):
    changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hue, self.saturation, self.value = 0.0, 1.0, 1.0
        self.setMinimumSize(240, 180)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        base = QColor.fromHsvF(self.hue / 360, 1.0, 1.0)
        p.fillRect(self.rect(), base)
        white = QLinearGradient(0, 0, self.width(), 0)
        white.setColorAt(0, QColor(255, 255, 255, 255))
        white.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(self.rect(), white)
        black = QLinearGradient(0, 0, 0, self.height())
        black.setColorAt(0, QColor(0, 0, 0, 0))
        black.setColorAt(1, QColor(0, 0, 0, 255))
        p.fillRect(self.rect(), black)
        x, y = self.saturation * self.width(), (1 - self.value) * self.height()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(Qt.white, 2))
        p.drawEllipse(x - 6, y - 6, 12, 12)
        p.setPen(QPen(Qt.black, 1))
        p.drawEllipse(x - 7, y - 7, 14, 14)

    def set_hue(self, hue):
        self.hue = hue
        self.update()

    def mousePressEvent(self, event):
        self._set_from_pos(event.position().x(), event.position().y())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._set_from_pos(event.position().x(), event.position().y())

    def _set_from_pos(self, x, y):
        self.saturation = max(0.0, min(1.0, x / max(1, self.width())))
        self.value = max(0.0, min(1.0, 1 - y / max(1, self.height())))
        self.changed.emit(self.saturation, self.value)
        self.update()


class ColorPickerDialog(QDialog):
    """Independent standard-HSV picker MVP. CSP synchronization is a later adapter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("独立取色器 · 标准 HSV")
        self.resize(620, 430)
        self.hue, self.saturation, self.value = 0.0, 1.0, 1.0
        self._updating = False
        root = QVBoxLayout(self)
        canvas = QHBoxLayout()
        self.ring = HueRingWidget()
        self.square = SaturationValueWidget()
        self.ring.changed.connect(self._hue_changed)
        self.square.changed.connect(self._sv_changed)
        canvas.addWidget(self.ring)
        canvas.addWidget(self.square, 1)
        root.addLayout(canvas, 1)

        grid = QGridLayout()
        self.fields = {}
        for row, (label, key, maximum) in enumerate((("R", "r", 255), ("G", "g", 255), ("B", "b", 255),
                                                       ("H", "h", 359), ("S", "s", 100), ("V", "v", 100))):
            spin = QSpinBox()
            spin.setRange(0, maximum)
            spin.valueChanged.connect(lambda _=0, key=key: self._field_changed(key))
            grid.addWidget(QLabel(label), row // 3, (row % 3) * 2)
            grid.addWidget(spin, row // 3, (row % 3) * 2 + 1)
            self.fields[key] = spin
        grid.addWidget(QLabel("HEX"), 2, 0)
        self.hex_edit = QLineEdit("#FF0000")
        self.hex_edit.editingFinished.connect(self._hex_changed)
        grid.addWidget(self.hex_edit, 2, 1, 1, 3)
        root.addLayout(grid)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("同步模式"))
        mode = QComboBox()
        mode.addItems(["手动同步", "自动同步", "仅复制 HEX"])
        mode.setToolTip("CSP 联动接口确认后启用同步；当前 MVP 可复制 HEX。")
        controls.addWidget(mode)
        top = QCheckBox("窗口置顶")
        top.toggled.connect(self._set_topmost)
        controls.addWidget(top)
        copy_button = QPushButton("复制 HEX")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.hex_edit.text()))
        controls.addWidget(copy_button)
        hide_button = QPushButton("隐藏")
        hide_button.clicked.connect(self.hide)
        controls.addWidget(hide_button)
        controls.addStretch(1)
        root.addLayout(controls)
        self._sync_from_hsv()

    def _set_topmost(self, checked):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        self.show()

    def _hue_changed(self, hue):
        self.hue = hue
        self._sync_from_hsv()

    def _sv_changed(self, saturation, value):
        self.saturation, self.value = saturation, value
        self._sync_from_hsv()

    def _sync_from_hsv(self):
        if self._updating:
            return
        self._updating = True
        color = QColor.fromHsvF(self.hue / 360, self.saturation, self.value)
        self.fields["r"].setValue(color.red())
        self.fields["g"].setValue(color.green())
        self.fields["b"].setValue(color.blue())
        self.fields["h"].setValue(round(self.hue) % 360)
        self.fields["s"].setValue(round(self.saturation * 100))
        self.fields["v"].setValue(round(self.value * 100))
        self.hex_edit.setText(color.name().upper())
        self.square.set_hue(self.hue)
        self.square.saturation, self.square.value = self.saturation, self.value
        self.square.update()
        self.ring.hue = self.hue
        self.ring.update()
        self._updating = False

    def _field_changed(self, key):
        if self._updating:
            return
        if key in "rgb":
            color = QColor(self.fields["r"].value(), self.fields["g"].value(), self.fields["b"].value())
            h, s, v, _ = color.getHsvF()
            self.hue, self.saturation, self.value = (0 if h < 0 else h * 360), s, v
        else:
            self.hue = self.fields["h"].value()
            self.saturation = self.fields["s"].value() / 100
            self.value = self.fields["v"].value() / 100
        self._sync_from_hsv()

    def _hex_changed(self):
        if self._updating:
            return
        color = QColor(self.hex_edit.text().strip())
        if not color.isValid():
            self._sync_from_hsv()
            return
        h, s, v, _ = color.getHsvF()
        self.hue, self.saturation, self.value = (0 if h < 0 else h * 360), s, v
        self._sync_from_hsv()


# ---------------------------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Brush Bridge 笔刷格式转换 {__version__}")
        self.resize(1200, 760)
        self.setAcceptDrops(True)
        self.detected: list[DetectedApp] = []
        self.tabs = QTabWidget()
        self.transfer = TransferPage(self)
        self.diag = DiagnosePage(self)
        self.tabs.addTab(self.transfer, "🖌 笔刷转换")
        self.tabs.addTab(self.diag, "🔧 修复导入问题")
        self.tools = UtilityPage(self)
        self.tabs.addTab(self.tools, "🧰 便捷工具")
        self.setCentralWidget(self.tabs)
        self.transfer.set_detected([])
        self.statusBar().showMessage("请选择笔刷文件，然后选择目标格式导出　　作者：askir · 本软件完全免费")

    def busy(self, text: str):
        self.statusBar().showMessage(text)
        QApplication.setOverrideCursor(Qt.BusyCursor)

    def idle(self, text: str = "就绪"):
        self.statusBar().showMessage(text)
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()

    def closeEvent(self, e):
        self.transfer.cancel_event.set()
        self.transfer.thumb_loader.shutdown()  # don't make the app wait for queued thumbnails
        super().closeEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if not paths:
            return
        if self.tabs.currentWidget() is self.diag:
            self.diag.check(paths[0])
        else:
            self.tabs.setCurrentWidget(self.transfer)
            self.transfer.add_files(paths)
        e.acceptProposedAction()


def _setup_logging():
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BrushBridge"
    base.mkdir(parents=True, exist_ok=True)
    try:
        logging.basicConfig(filename=base / "brush-bridge.log", level=logging.INFO, encoding="utf-8",
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    except PermissionError:
        # A previous GUI instance can still hold the shared log on Windows.
        # Logging must never prevent opening a brush file or running smoke mode.
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: Optional[list[str]] = None) -> int:
    _setup_logging()
    # Brush decoders used for thumbnails are mostly pure Python and run on worker threads; a short
    # GIL switch interval keeps painting/scrolling responsive while they work (default is 5 ms).
    sys.setswitchinterval(0.0005)
    argv = sys.argv if argv is None else argv
    smoke_dir = smoke_input = None
    if "--smoke-test" in argv:
        pos = argv.index("--smoke-test")
        smoke_dir = Path(argv[pos + 1]).resolve()
        smoke_input = Path(argv[pos + 2]).resolve() if len(argv) > pos + 2 else None
        argv = argv[:pos]
    app = QApplication(argv)
    app.setApplicationName("Brush Bridge")
    if smoke_dir and not QFontDatabase.families():
        # The offscreen Qt backend does not discover Windows system fonts.
        font_file = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
        if font_file.is_file():
            QFontDatabase.addApplicationFont(str(font_file))
    f = QFont("Microsoft YaHei UI")
    f.setPointSize(10)
    app.setFont(f)
    app.setStyleSheet(STYLE)
    icon = Path(__file__).with_name("icon.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    load_plugins()
    w = MainWindow()
    w.show()
    if smoke_dir:
        smoke_dir.mkdir(parents=True, exist_ok=True)
        attempts = [0]
        def smoke_finish():
            attempts[0] += 1
            ready = not smoke_input or w.transfer.proxy.rowCount() > 0
            if not ready and attempts[0] < 100:
                QTimer.singleShot(100, smoke_finish)
                return
            evidence = {"window_visible": w.isVisible(), "brush_count": w.transfer.proxy.rowCount(),
                        "version": __version__, "input_loaded": ready}
            if ready and smoke_input:
                t = w.transfer
                t._check_all(True)
                brushes, errors = convert.load_brushes(t.checked_refs())
                from ..core.plugin import codec
                ok, message, files, _ = t._export(codec("brushpkg"), brushes, smoke_dir / "gui-roundtrip.brushpkg")
                evidence.update(export_ok=ok, errors=errors, message=message, files=[str(p) for p in files])
            w.grab().save(str(smoke_dir / "window.png"))
            (smoke_dir / "gui-smoke.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            w.close()
            app.exit(0 if ready and evidence.get("export_ok", True) else 1)
        if smoke_input:
            QTimer.singleShot(100, lambda: w.transfer.add_files([smoke_input]))
        QTimer.singleShot(500, smoke_finish)
    files = [Path(a) for a in argv[1:] if Path(a).exists()]
    if files:
        QTimer.singleShot(300, lambda: w.transfer.add_files(files))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
