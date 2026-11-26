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
from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, QSettings, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QFontDatabase, QIcon, QImage,
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QDialogButtonBox, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QSizePolicy, QSplitter, QStackedLayout, QCheckBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget, QSpinBox,
)

from .. import __version__
from ..apps.csp_sync import (
    client_to_physical_screen,
    format_shortcut,
    parse_shortcut_text,
    read_csp_screen_picker_shortcut,
    sync_color_to_csp,
    sync_color_to_photoshop,
)
from ..apps.sai2 import app_install_from_exe
from ..core import convert
from ..core.color_models import rgb8_to_vhsv, vhsv_to_rgb8
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

        thumbnail_label = QLabel("卡片显示原笔刷缩略图。请在目标软件中试画以查看转换效果。")
        thumbnail_label.setObjectName("hint")
        rv.addWidget(thumbnail_label)

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
UTILITY_STYLE = """
QWidget#utilityPage { background: #f3f5fa; color: #1d293b; }
QLabel#utilityTitle { font-size: 24px; font-weight: bold; }
QFrame[utilityCard="true"] { background: #ffffff; border: 1px solid #dce2ed; border-radius: 12px; }
QFrame#utilityPicker { background: #edf2ff; border-color: #d7e2fc; }
QLabel[cardTitle="true"] { font-size: 16px; font-weight: bold; }
QLabel[cardHint="true"] { color: #526077; }
QFrame#utilityHue { border-radius: 23px;
    background: qconicalgradient(cx: 0.5, cy: 0.5, angle: 120, stop: 0 #e5687e,
                  stop: 0.17 #a17ae7, stop: 0.34 #6189e5, stop: 0.51 #4bb5bd,
                  stop: 0.68 #77b777, stop: 0.85 #edbc60, stop: 1 #e5687e); }
QFrame#utilityHueCenter { background: #edf2ff; border-radius: 15px; }
QLineEdit { background: #ffffff; color: #1d293b; border: 1px solid #cbd4e2;
            border-radius: 6px; padding: 7px 9px; }
QLineEdit:focus, QPushButton:focus { border-color: #2f6fed; }
QWidget#utilityList { background: #f7f9fc; border: 1px solid #dce2ed; border-radius: 8px; }
QPushButton { color: #25344c; border-color: #cbd4e2; padding: 7px 12px; }
QPushButton:disabled { background: #f1f4f8; color: #6b7587; border-color: #e0e6ef; }
QPushButton#utilityOpenPicker { background: #245bd3; color: #ffffff; border-color: #245bd3; font-weight: bold; }
QPushButton#utilityOpenPicker:hover { background: #1948b6; }
QPushButton#utilityOpenPicker:focus { border-color: #163879; }
"""


class UtilityPage(QWidget):
    """Small, explicit helpers that need a user-selected local application."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.install = None
        self.refs: list[BrushRef] = []
        self.settings = QSettings("BrushBridge", "BrushBridge")
        self.setObjectName("utilityPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(UTILITY_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 22)
        root.setSpacing(14)
        title = QLabel("便捷工具")
        title.setObjectName("utilityTitle")
        root.addWidget(title)

        cards = QGridLayout()
        cards.setSpacing(14)
        cards.setColumnStretch(0, 2)
        cards.setColumnStretch(1, 1)
        cards.setRowStretch(1, 1)
        root.addLayout(cards, 1)

        setup, setup_body = self._card("SAI2 连接")
        cards.addWidget(setup, 0, 0)
        self.exe_path = QLineEdit(str(self.settings.value("sai2/exe", "")))
        self.exe_path.setPlaceholderText("sai2.exe 的路径")
        self.exe_path.setAccessibleName("SAI2 程序路径")
        self.exe_path.returnPressed.connect(self._load_sai2)
        setup_body.addWidget(self.exe_path)

        path_row = QHBoxLayout()
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse_exe)
        path_row.addWidget(browse)
        load = QPushButton("读取本地笔刷")
        load.clicked.connect(self._load_sai2)
        path_row.addWidget(load)
        path_row.addStretch(1)
        setup_body.addLayout(path_row)

        self.status = QLabel("选择本机的 sai2.exe 以读取笔刷。")
        self.status.setProperty("cardHint", True)
        self.status.setWordWrap(True)
        setup_body.addWidget(self.status)

        picker, picker_body = self._card("独立取色器")
        picker.setObjectName("utilityPicker")
        cards.addWidget(picker, 0, 1)
        picker_hint = QLabel("使用 V-HSV 色环选色，或从屏幕拾取颜色。")
        picker_hint.setProperty("cardHint", True)
        picker_hint.setWordWrap(True)
        picker_intro = QHBoxLayout()
        picker_intro.addWidget(picker_hint, 1, Qt.AlignTop)
        hue = QFrame()
        hue.setObjectName("utilityHue")
        hue.setFixedSize(46, 46)
        hue_center = QFrame(hue)
        hue_center.setObjectName("utilityHueCenter")
        hue_center.setGeometry(8, 8, 30, 30)
        picker_intro.addWidget(hue, 0, Qt.AlignRight)
        picker_body.addLayout(picker_intro)
        picker_body.addStretch(1)
        open_picker = QPushButton("打开取色器")
        open_picker.setObjectName("utilityOpenPicker")
        open_picker.clicked.connect(self._open_picker)
        picker_body.addWidget(open_picker, 0, Qt.AlignLeft)

        library, library_body = self._card("SAI2 笔刷管理")
        cards.addWidget(library, 1, 0, 1, 2)
        list_panel = QWidget()
        list_panel.setObjectName("utilityList")
        self.brush_views = QStackedLayout(list_panel)
        self.brush_views.setContentsMargins(1, 1, 1, 1)
        empty = QLabel("从 SAI2 读取的笔刷会显示在这里。")
        empty.setProperty("cardHint", True)
        empty.setAlignment(Qt.AlignCenter)
        self.brush_views.addWidget(empty)

        self.brushes = QListWidget()
        self.brushes.setObjectName("sources")
        self.brushes.setAccessibleName("SAI2 本地笔刷")
        self.brushes.setMinimumHeight(140)
        self.brush_views.addWidget(self.brushes)
        library_body.addWidget(list_panel, 1)

        action_row = QHBoxLayout()
        group_label = QLabel("安装工具组")
        self.group_name = QLineEdit("导入笔刷")
        group_label.setBuddy(self.group_name)
        action_row.addWidget(group_label)
        action_row.addWidget(self.group_name, 1)
        self.install_button = QPushButton("安装选中笔刷")
        self.install_button.clicked.connect(self._install_selected)
        self.install_button.setEnabled(False)
        action_row.addWidget(self.install_button)
        self.export_button = QPushButton("导出选中笔刷…")
        self.export_button.clicked.connect(self._export_selected)
        self.export_button.setEnabled(False)
        action_row.addWidget(self.export_button)
        library_body.addLayout(action_row)

    @staticmethod
    def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setProperty("utilityCard", True)
        body = QVBoxLayout(frame)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(9)
        heading = QLabel(title)
        heading.setProperty("cardTitle", True)
        body.addWidget(heading)
        return frame, body

    def _open_picker(self):
        dialog = getattr(self, "_picker_dialog", None)
        if dialog is None:
            dialog = ColorPickerDialog(None)
            self._picker_dialog = dialog
            dialog.finished.connect(lambda *_: setattr(self, "_picker_dialog", None))
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, dialog.top_check.isChecked())
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _browse_exe(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 SAI2.exe", self.exe_path.text() or str(Path.home()), "SAI2 (sai2.exe)")
        if path:
            self.exe_path.setText(path)
            self._load_sai2()

    def _load_sai2(self):
        path = Path(self.exe_path.text().strip())
        self.install = app_install_from_exe(path)
        self.refs = []
        self.brushes.clear()
        self.brush_views.setCurrentIndex(0)
        self.install_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.status.setToolTip("")
        if self.install is None:
            self.status.setText("找不到可用的 sai2.exe。")
            return
        self.settings.setValue("sai2/exe", str(path))
        try:
            self.refs = plugin_app("sai2").list_brushes(self.install)
        except Exception as exc:
            self.status.setText(f"读取 SAI2 设置失败：{exc}")
            return
        for ref in self.refs:
            item = QListWidgetItem(ref.name)
            item.setData(Qt.UserRole, ref)
            item.setToolTip(ref.detail or ref.group or ref.name)
            self.brushes.addItem(item)
        self.status.setText(f"{self.install.label} · 本地笔刷 {len(self.refs)} 支")
        self.status.setToolTip(str(self.install.data_dir))
        enabled = bool(self.refs)
        if enabled:
            self.brush_views.setCurrentWidget(self.brushes)
            self.brushes.setCurrentRow(0)
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


PICKER_STYLE = """
QDialog {
    background: #2b2b2b;
    color: #e0e0e0;
    font-size: 12px;
}
QWidget {
    color: #e0e0e0;
    font-size: 12px;
}
QLabel#hint {
    color: #b8b8b8;
    font-family: "Consolas", "Microsoft YaHei UI";
    font-size: 12px;
}
QLabel#status {
    color: #8f9ba8;
    font-size: 11px;
}
QPushButton {
    background: #3a3a3a;
    color: #e4e4e4;
    border: 1px solid #525252;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 12px;
}
QPushButton:hover {
    background: #484848;
    border-color: #686868;
}
QPushButton:pressed {
    background: #2e2e2e;
}
QComboBox, QLineEdit, QSpinBox {
    background: #222222;
    color: #eaeaea;
    border: 1px solid #4c4c4c;
    border-radius: 3px;
    padding: 2px 4px;
    font-size: 12px;
}
QComboBox::drop-down {
    border: none;
    width: 16px;
}
QComboBox QAbstractItemView {
    background: #2b2b2b;
    color: #eaeaea;
    selection-background-color: #2f6fed;
}
QCheckBox {
    spacing: 4px;
    font-size: 12px;
}
"""


class HueRingWidget(QWidget):
    changed = Signal(float)
    committed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hue = 0.0
        self._dragging = False
        self.setMinimumSize(160, 160)

    @staticmethod
    def ring_geometry(side: int) -> tuple[float, float, float]:
        """Return ``(outer_diameter, ring_thickness, inner_diameter)`` for ``side``."""
        outer_diameter = max(40.0, float(side) - 12.0)
        ring_thickness = max(16.0, min(26.0, float(side) * 0.10))
        inner_diameter = max(20.0, outer_diameter - 2.0 * ring_thickness)
        return outer_diameter, ring_thickness, inner_diameter

    def paintEvent(self, _event):
        import math

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        outer_d, ring_w, inner_d = self.ring_geometry(side)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        outer_r = outer_d / 2.0
        inner_r = inner_d / 2.0

        # CSP/SAI orientation: red at upper-left (150 deg CCW from +x), green at
        # upper-right, blue across the lower half.
        gradient = QConicalGradient(cx, cy, 150)
        for pos, color in ((0.0, "#ff0000"), (1/6, "#ff00ff"), (2/6, "#0000ff"),
                           (3/6, "#00ffff"), (4/6, "#00ff00"), (5/6, "#ffff00"), (1.0, "#ff0000")):
            gradient.setColorAt(pos, QColor(color))
        p.setBrush(QBrush(gradient))
        p.setPen(QPen(QColor("#181818"), 1))
        p.drawEllipse(cx - outer_r, cy - outer_r, outer_d, outer_d)

        p.setBrush(QColor("#232323"))
        p.setPen(QPen(QColor("#181818"), 1))
        p.drawEllipse(cx - inner_r, cy - inner_r, inner_d, inner_d)

        angle = math.radians(self.hue - 150.0)
        mid_r = (outer_r + inner_r) / 2.0
        cur_r = max(5.0, min(8.0, ring_w * 0.36))
        x = cx + mid_r * math.cos(angle)
        y = cy + mid_r * math.sin(angle)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 230), 2.6))
        p.drawEllipse(x - cur_r - 1.0, y - cur_r - 1.0, (cur_r + 1.0) * 2.0, (cur_r + 1.0) * 2.0)
        p.setPen(QPen(QColor(255, 255, 255, 245), 1.8))
        p.drawEllipse(x - cur_r, y - cur_r, cur_r * 2.0, cur_r * 2.0)

    def _is_on_ring(self, x: float, y: float) -> bool:
        import math

        side = min(self.width(), self.height())
        outer_d, _, inner_d = self.ring_geometry(side)
        dx, dy = x - self.width() / 2.0, y - self.height() / 2.0
        dist = math.hypot(dx, dy)
        return (inner_d / 2.0 - 5.0) <= dist <= (outer_d / 2.0 + 6.0)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._is_on_ring(event.position().x(), event.position().y()):
            self._dragging = True
            self._set_from_pos(event.position().x(), event.position().y())

    def mouseMoveEvent(self, event):
        if self._dragging and (event.buttons() & Qt.LeftButton):
            self._set_from_pos(event.position().x(), event.position().y())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self._set_from_pos(event.position().x(), event.position().y())
            self.committed.emit()

    def _set_from_pos(self, x, y):
        import math
        dx, dy = x - self.width() / 2.0, y - self.height() / 2.0
        self.hue = (math.degrees(math.atan2(dy, dx)) + 150.0) % 360.0
        self.changed.emit(self.hue)
        self.update()


class SaturationValueWidget(QWidget):
    changed = Signal(float, float)
    committed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hue, self.saturation, self.value = 0.0, 1.0, 1.0
        self._rendered_key = None
        self._rendered_image = None
        self._dragging = False
        self.setMinimumSize(64, 64)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        width, height = max(1, self.width()), max(1, self.height())
        key = (width, height, round(self.hue, 3))
        if self._rendered_key != key:
            import colorsys
            import numpy as np

            saturation = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
            value = np.linspace(1.0, 0.0, height, dtype=np.float32)[:, None]
            pure = np.asarray(colorsys.hsv_to_rgb((self.hue % 360.0) / 360.0, 1.0, 1.0), dtype=np.float32)
            chroma = saturation * (1.0 - 0.5 * saturation * (1.0 - value))
            rgb = value[:, :, None] - chroma[:, :, None] * (1.0 - pure[None, None, :])
            pixels = np.rint(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
            image = QImage(pixels.data, width, height, width * 3, QImage.Format_RGB888).copy()
            self._rendered_key, self._rendered_image = key, image
        p.setClipRect(self.rect())
        p.drawImage(0, 0, self._rendered_image)
        p.setBrush(Qt.NoBrush)
        x, y = self.saturation * self.width(), (1.0 - self.value) * self.height()
        p.setPen(QPen(QColor(0, 0, 0, 220), 2.2))
        p.drawEllipse(x - 6.5, y - 6.5, 13.0, 13.0)
        p.setPen(QPen(QColor(255, 255, 255, 245), 1.6))
        p.drawEllipse(x - 5.5, y - 5.5, 11.0, 11.0)
        p.setPen(QPen(QColor("#141414"), 1.5))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def set_hue(self, hue):
        self.hue = hue
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._set_from_pos(event.position().x(), event.position().y())

    def mouseMoveEvent(self, event):
        if self._dragging and (event.buttons() & Qt.LeftButton):
            self._set_from_pos(event.position().x(), event.position().y())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self._set_from_pos(event.position().x(), event.position().y())
            self.committed.emit()

    def _set_from_pos(self, x, y):
        self.saturation = max(0.0, min(1.0, x / max(1, self.width())))
        self.value = max(0.0, min(1.0, 1.0 - y / max(1, self.height())))
        self.changed.emit(self.saturation, self.value)
        self.update()


class ColorWheelWidget(QWidget):
    """CSP/SAI-style nested hue ring and V-HSV square that scales with the window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ring = HueRingWidget(self)
        self.square = SaturationValueWidget(self)
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(260, 260)

    def resizeEvent(self, event):
        side = min(self.width(), self.height())
        x0 = (self.width() - side) // 2
        y0 = (self.height() - side) // 2
        self.ring.setGeometry(x0, y0, side, side)
        # The square's diagonal must strictly fit inside the circular hole of
        # the hue ring with a small dark gap around all four corners.
        _, _, inner_diameter = HueRingWidget.ring_geometry(side)
        square_side = max(64, int((inner_diameter - 6.0) / 1.41421356237))
        self.square.setGeometry(
            x0 + (side - square_side) // 2,
            y0 + (side - square_side) // 2,
            square_side,
            square_side,
        )
        super().resizeEvent(event)


class DualSwatch(QWidget):
    """CSP/SAI-style Main/Sub color slots that store two colors independently."""

    clicked = Signal()
    slotChanged = Signal(int)
    swapped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.slot_colors = [QColor("#FF0000"), QColor("#FFFFFF")]
        self.active_slot = 0
        self.setFixedSize(62, 42)
        self.setToolTip("左键点击主色/副色切换编辑槽位；点击右上 ⇄ 互换主副色；双击打开数值输入")

    @property
    def current(self) -> QColor:
        return self.slot_colors[self.active_slot]

    @current.setter
    def current(self, color: QColor):
        self.slot_colors[self.active_slot] = QColor(color)

    @property
    def previous(self) -> QColor:
        return self.slot_colors[1 - self.active_slot]

    @previous.setter
    def previous(self, color: QColor):
        self.slot_colors[1 - self.active_slot] = QColor(color)

    def set_active_color(self, color: QColor):
        self.slot_colors[self.active_slot] = QColor(color)
        self.update()

    def set_colors(self, current: QColor, previous: QColor):
        self.slot_colors[self.active_slot] = QColor(current)
        self.slot_colors[1 - self.active_slot] = QColor(previous)
        self.update()

    @staticmethod
    def slot_rect(index: int) -> QRect:
        return QRect(3, 3, 28, 28) if index == 0 else QRect(23, 12, 28, 28)

    @staticmethod
    def swap_rect() -> QRect:
        return QRect(34, 1, 20, 11)

    def active_global_center(self) -> QPoint:
        """Return the screen coordinate of the active slot's unobstructed center."""
        rect = self.slot_rect(self.active_slot)
        return self.mapToGlobal(rect.center())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            if self.swap_rect().contains(pos):
                self.slot_colors[0], self.slot_colors[1] = self.slot_colors[1], self.slot_colors[0]
                self.swapped.emit()
                self.update()
                return
            inactive = 1 - self.active_slot
            active_r = self.slot_rect(self.active_slot)
            inactive_r = self.slot_rect(inactive)
            # Active slot is drawn on top; clicking the visible part of the
            # inactive slot switches to it without overwriting either slot.
            if inactive_r.contains(pos) and not active_r.contains(pos):
                self.active_slot = inactive
                self.slotChanged.emit(self.active_slot)
                self.update()
                return
            if active_r.contains(pos):
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        inactive = 1 - self.active_slot

        # Draw inactive slot behind
        r_in = self.slot_rect(inactive)
        p.setPen(QPen(QColor("#606060"), 1))
        p.setBrush(self.slot_colors[inactive])
        p.drawRoundedRect(r_in, 4, 4)

        # Draw active slot on top so its entire 28x28 area is solid & unobstructed
        r_act = self.slot_rect(self.active_slot)
        p.setPen(QPen(QColor("#121212"), 2.5))
        p.setBrush(self.slot_colors[self.active_slot])
        p.drawRoundedRect(r_act, 4, 4)
        p.setPen(QPen(QColor("#ffffff"), 1.3))
        p.drawRoundedRect(r_act.adjusted(1, 1, -1, -1), 3, 3)

        # Tiny swap arrows in top-right corner (drawn with QPainter so it never
        # depends on Unicode symbol font fallback).
        sr = self.swap_rect()
        cx, cy = sr.center().x(), sr.center().y()
        p.setPen(QPen(QColor("#b8b8b8"), 1.3))
        p.drawLine(cx - 5, cy - 2, cx + 4, cy - 2)
        p.drawLine(cx + 2, cy - 4, cx + 4, cy - 2)
        p.drawLine(cx + 2, cy, cx + 4, cy - 2)
        p.drawLine(cx - 4, cy + 2, cx + 5, cy + 2)
        p.drawLine(cx - 2, cy, cx - 4, cy + 2)
        p.drawLine(cx - 2, cy + 4, cx - 4, cy + 2)


class SwatchPaletteBar(QWidget):
    """8 independent saved color slots so the artist can store and recall colors separately."""

    colorSelected = Signal(float, float, float)

    def __init__(self, parent=None, count: int = 8):
        super().__init__(parent)
        self.count = count
        self.slots: list[Optional[tuple[float, float, float]]] = [None] * count
        self.selected_index = 0
        self.setFixedHeight(22)
        self.setMinimumWidth(160)
        self.setToolTip("独立存色格：左键点击已存色格读取颜色；点击空格、右键点击任意格或点右侧「＋」将当前颜色存入该格")

    def load_from_list(self, items: list):
        for i in range(min(self.count, len(items))):
            val = items[i]
            if isinstance(val, (list, tuple)) and len(val) == 3:
                try:
                    self.slots[i] = (float(val[0]), float(val[1]), float(val[2]))
                except (TypeError, ValueError):
                    self.slots[i] = None
        self.update()

    def to_list(self) -> list:
        return [list(s) if s is not None else None for s in self.slots]

    def save_to_slot(self, hsv: tuple[float, float, float], index: Optional[int] = None) -> int:
        if index is None:
            # Prefer the first empty slot; if all are full, store into selected_index
            empty = next((i for i, s in enumerate(self.slots) if s is None), None)
            index = empty if empty is not None else self.selected_index
        index = max(0, min(self.count - 1, int(index)))
        self.slots[index] = (float(hsv[0]), float(hsv[1]), float(hsv[2]))
        self.selected_index = index
        self.update()
        return index

    def _cell_rect(self, index: int) -> QRect:
        gap = 4
        w = max(16, (self.width() - gap * (self.count - 1)) // self.count)
        return QRect(index * (w + gap), 1, w, self.height() - 2)

    def _index_at(self, pos: QPoint) -> Optional[int]:
        for i in range(self.count):
            if self._cell_rect(i).contains(pos):
                return i
        return None

    def mousePressEvent(self, event):
        idx = self._index_at(event.position().toPoint())
        if idx is None:
            return
        self.selected_index = idx
        if event.button() == Qt.RightButton:
            self.parent()._save_current_to_palette(idx)
            return
        if event.button() == Qt.LeftButton:
            if self.slots[idx] is not None:
                h, s, v = self.slots[idx]
                self.colorSelected.emit(h, s, v)
                self.update()
            else:
                self.parent()._save_current_to_palette(idx)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i in range(self.count):
            r = self._cell_rect(i)
            hsv = self.slots[i]
            if hsv is not None:
                rgb = vhsv_to_rgb8(*hsv)
                p.setBrush(QColor(*rgb))
            else:
                p.setBrush(QColor("#202020"))
            if i == self.selected_index:
                p.setPen(QPen(QColor("#dcdcdc"), 1.4))
            else:
                p.setPen(QPen(QColor("#4a4a4a"), 1.0))
            p.drawRoundedRect(r, 3, 3)


def _parse_hex_color(text: str) -> QColor:
    raw = text.strip().lstrip("#")
    if len(raw) in (3, 6) and all(ch in "0123456789abcdefABCDEF" for ch in raw):
        return QColor("#" + raw)
    return QColor(text.strip())


def _format_csp_hex(color: QColor) -> str:
    """Return 6-digit uppercase HEX without leading ``#`` for CSP's HEX input box."""
    return color.name().lstrip("#").upper()


class ColorInputDialog(QDialog):
    """Small, explicit RGB/HEX editor opened from the dual swatch."""

    def __init__(self, parent=None, rgb=(255, 0, 0)):
        super().__init__(parent)
        self.setWindowTitle("输入颜色")
        self.setStyleSheet(PICKER_STYLE)
        self.setMinimumWidth(280)
        root = QVBoxLayout(self)
        grid = QGridLayout()
        self.spins = {}
        for row, (label, key) in enumerate((("R", "r"), ("G", "g"), ("B", "b"))):
            grid.addWidget(QLabel(label), row, 0)
            spin = QSpinBox()
            spin.setRange(0, 255)
            spin.setValue(rgb["rgb".index(key)])
            self.spins[key] = spin
            grid.addWidget(spin, row, 1)
        grid.addWidget(QLabel("HEX"), 0, 2)
        self.hex_edit = QLineEdit(_format_csp_hex(QColor(*rgb)))
        grid.addWidget(self.hex_edit, 0, 3, 1, 2)
        root.addLayout(grid)
        self.preview = QLabel()
        self.preview.setMinimumHeight(28)
        root.addWidget(self.preview)
        for spin in self.spins.values():
            spin.editingFinished.connect(self._sync_hex)
        self.hex_edit.editingFinished.connect(self._sync_rgb)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.setMinimumHeight(34)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._sync_hex()

    def _on_accept(self):
        self._sync_hex()
        self.accept()

    def _sync_hex(self):
        color = QColor(self.spins["r"].value(), self.spins["g"].value(), self.spins["b"].value())
        self.hex_edit.setText(_format_csp_hex(color))
        self.preview.setStyleSheet(f"background:{color.name()}; border:1px solid #555;")

    def _sync_rgb(self):
        color = _parse_hex_color(self.hex_edit.text())
        if color.isValid():
            for key, value in zip("rgb", (color.red(), color.green(), color.blue())):
                self.spins[key].setValue(value)
            self._sync_hex()

    def rgb(self):
        return tuple(self.spins[key].value() for key in "rgb")


class ColorPickerDialog(QDialog):
    """Independent SAI-like V-HSV picker with CSP foreground synchronization."""

    def __init__(self, parent=None, embedded=False):
        super().__init__(parent)
        self.embedded = embedded
        self.settings = QSettings("BrushBridge", "BrushBridge")
        self.setWindowTitle("V-HSV 色轮 · CSP 联动")
        self.setStyleSheet(PICKER_STYLE)
        if not embedded:
            self.setWindowFlag(Qt.Tool, True)
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumSize(250, 340)
        self.resize(310, 420)

        # Two independent V-HSV slots for Main Color (0) and Sub Color (1)
        self._slot_hsv: list[list[float]] = [[0.0, 1.0, 1.0], [0.0, 0.0, 1.0]]
        self.hue, self.saturation, self.value = 0.0, 1.0, 1.0
        self._last_color = QColor("#FF0000")
        self.previous_color = QColor("#FFFFFF")
        self._input_dialog = None
        self._updating = False
        self._pending_fields = {}
        self._field_commit_scheduled = False

        saved_sc = str(self.settings.value("picker/csp_shortcut", "")).strip()
        self.csp_shortcut = parse_shortcut_text(saved_sc) if saved_sc else read_csp_screen_picker_shortcut()

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self.wheel = ColorWheelWidget(self)
        self.ring = self.wheel.ring
        self.square = self.wheel.square
        self.ring.changed.connect(self._hue_changed)
        self.square.changed.connect(self._sv_changed)
        self.ring.committed.connect(self._on_pick_committed)
        self.square.committed.connect(self._on_pick_committed)
        root.addWidget(self.wheel, 1)

        # Row 1: Dual swatch (Main / Sub) + HSV summary + inline HEX + numeric toggle
        summary = QHBoxLayout()
        summary.setSpacing(6)
        self.swatch = DualSwatch(self)
        self.swatch.clicked.connect(self._open_color_input)
        self.swatch.slotChanged.connect(self._on_slot_switched)
        self.swatch.swapped.connect(self._on_slots_swapped)
        summary.addWidget(self.swatch)

        info_col = QVBoxLayout()
        info_col.setSpacing(3)
        self.hsv_summary = QLabel("H 000   S 100   V 100")
        self.hsv_summary.setObjectName("hint")
        info_col.addWidget(self.hsv_summary)

        hex_row = QHBoxLayout()
        hex_row.setSpacing(4)
        hex_prefix = QLabel("#")
        hex_prefix.setObjectName("hint")
        hex_row.addWidget(hex_prefix)
        self.hex_edit = QLineEdit("FF0000")
        self.hex_edit.setFixedWidth(66)
        self.hex_edit.setToolTip("不含 # 的 6 位 HEX，可直接粘贴到 CSP 颜色设置 HEX 框（输入框同时兼容带/不带 #）")
        self.hex_edit.editingFinished.connect(self._hex_changed)
        hex_row.addWidget(self.hex_edit)
        self.copy_button = QPushButton("复制")
        self.copy_button.setToolTip("复制 6 位纯 HEX（不含 #，可直接粘贴进 CSP 颜色设置）")
        self.copy_button.clicked.connect(self._copy_hex)
        hex_row.addWidget(self.copy_button)
        self.toggle_fields_button = QPushButton("数值")
        self.toggle_fields_button.setCheckable(True)
        self.toggle_fields_button.setToolTip("展开或收起 R/G/B/H/S/V 精确输入框")
        hex_row.addWidget(self.toggle_fields_button)
        hex_row.addStretch(1)
        info_col.addLayout(hex_row)
        summary.addLayout(info_col, 1)
        root.addLayout(summary)

        # Row 2: 8-slot independent color palette bar + store button
        pal_row = QHBoxLayout()
        pal_row.setSpacing(4)
        self.palette_bar = SwatchPaletteBar(self, count=8)
        saved_pal = self.settings.value("picker/palette_slots", [])
        if isinstance(saved_pal, list):
            self.palette_bar.load_from_list(saved_pal)
        self.palette_bar.colorSelected.connect(self._on_palette_color_selected)
        pal_row.addWidget(self.palette_bar, 1)
        self.save_swatch_button = QPushButton("＋存色")
        self.save_swatch_button.setToolTip("将当前颜色存入下方选中/空白的色板格（也可右键点击任意色格覆盖保存）")
        self.save_swatch_button.clicked.connect(lambda: self._save_current_to_palette(None))
        pal_row.addWidget(self.save_swatch_button)
        root.addLayout(pal_row)

        # Collapsible inline numeric grid (R/G/B/H/S/V)
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        self.fields = {}
        for row, (label, key, maximum) in enumerate((("R", "r", 255), ("G", "g", 255), ("B", "b", 255),
                                                       ("H", "h", 359), ("S", "s", 100), ("V", "v", 100))):
            spin = QSpinBox()
            spin.setRange(0, maximum)
            spin.editingFinished.connect(lambda key=key: self._field_changed(key))
            grid.addWidget(QLabel(label), row // 3, (row % 3) * 2)
            grid.addWidget(spin, row // 3, (row % 3) * 2 + 1)
            self.fields[key] = spin
        self.grid_container = QWidget(self)
        self.grid_container.setLayout(grid)
        self.grid_container.setVisible(False)
        self.toggle_fields_button.toggled.connect(self._toggle_numeric_fields)
        root.addWidget(self.grid_container)

        # Row 3: CSP synchronization & window controls (compact, never truncated)
        controls = QHBoxLayout()
        controls.setSpacing(4)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["自动同步 CSP", "自动同步 Photoshop", "手动同步", "仅复制"])
        saved_mode = int(self.settings.value("picker/sync_mode", 0) or 0)
        if 0 <= saved_mode < self.mode_combo.count():
            self.mode_combo.setCurrentIndex(saved_mode)
        self.mode_combo.currentIndexChanged.connect(
            lambda idx: self.settings.setValue("picker/sync_mode", idx)
        )
        self.mode_combo.currentIndexChanged.connect(self._update_sync_target_ui)
        controls.addWidget(self.mode_combo, 1)

        self.sync_button = QPushButton("同步")
        self.sync_button.setToolTip("立即将当前颜色传给选中的 CSP 或 Photoshop")
        self.sync_button.clicked.connect(lambda: self.sync_to_target(force=True))
        controls.addWidget(self.sync_button)

        self.shortcut_button = QPushButton(f"键:{format_shortcut(self.csp_shortcut)}")
        self.shortcut_button.setToolTip(
            "CSP「编辑 → 获得屏幕颜色」快捷键（已自动从 CSP 配置读取，点击可手动修改）"
        )
        self.shortcut_button.clicked.connect(self._configure_csp_shortcut)
        controls.addWidget(self.shortcut_button)

        controls.addWidget(QLabel("冻结"))
        self.freeze_combo = QComboBox()
        self.freeze_combo.addItems(["关闭", "0.3 秒", "0.6 秒", "1.0 秒", "2.0 秒"])
        saved_freeze = int(self.settings.value("picker/freeze_index", 2) or 2)
        self.freeze_combo.setCurrentIndex(max(0, min(saved_freeze, self.freeze_combo.count() - 1)))
        self.freeze_combo.setToolTip("同步取色时把鼠标/数位笔指针锁在色块上，避免手抖导致取到别处")
        self.freeze_combo.currentIndexChanged.connect(
            lambda idx: self.settings.setValue("picker/freeze_index", idx)
        )
        controls.addWidget(self.freeze_combo)

        self.top_check = QCheckBox("置顶")
        self.top_check.setChecked(True)
        self.top_check.toggled.connect(self._set_topmost)
        controls.addWidget(self.top_check)
        root.addLayout(controls)

        self.status_label = QLabel(
            f"CSP 屏幕取色键：{format_shortcut(self.csp_shortcut)}（自动同步时会短暂冻结指针）"
        )
        self.status_label.setObjectName("status")
        root.addWidget(self.status_label)
        self._update_sync_target_ui(self.mode_combo.currentIndex())

        self._sync_from_hsv()

    def _toggle_numeric_fields(self, checked: bool):
        self.grid_container.setVisible(checked)
        self.toggle_fields_button.setText("收起" if checked else "数值")
        delta = 64
        if checked:
            self.setMinimumHeight(404)
            self.resize(self.width(), self.height() + delta)
        else:
            self.setMinimumHeight(340)
            self.resize(self.width(), max(340, self.height() - delta))

    def _set_topmost(self, checked: bool):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        self.show()

    def _copy_hex(self):
        raw_hex = self.hex_edit.text().strip().lstrip("#").upper()
        QApplication.clipboard().setText(raw_hex)
        self.status_label.setText(f"已复制 {raw_hex}（可直接粘贴到 CSP）")

    def _save_current_to_palette(self, index: Optional[int] = None):
        slot = self.palette_bar.save_to_slot((self.hue, self.saturation, self.value), index)
        self.settings.setValue("picker/palette_slots", self.palette_bar.to_list())
        self.status_label.setText(f"已存入色格 #{slot + 1}（{self.hex_edit.text()}）")

    def _on_palette_color_selected(self, hue: float, saturation: float, value: float):
        self.hue, self.saturation, self.value = hue, saturation, value
        self._sync_from_hsv()
        self._on_pick_committed()

    def _on_slot_switched(self, slot_index: int):
        h, s, v = self._slot_hsv[slot_index]
        self.hue, self.saturation, self.value = h, s, v
        self._sync_from_hsv()
        self.status_label.setText(f"已切换到{'主色' if slot_index == 0 else '副色'}槽位")
        self._on_pick_committed()

    def _on_slots_swapped(self):
        self._slot_hsv[0], self._slot_hsv[1] = self._slot_hsv[1], self._slot_hsv[0]
        h, s, v = self._slot_hsv[self.swatch.active_slot]
        self.hue, self.saturation, self.value = h, s, v
        self._sync_from_hsv()
        self.status_label.setText("已互换主色与副色")
        self._on_pick_committed()

    def _configure_csp_shortcut(self):
        cur = format_shortcut(self.csp_shortcut)
        text, ok = QInputDialog.getText(
            self,
            "设置 CSP 屏幕取色快捷键",
            "请输入 CSP「编辑 → 获得屏幕颜色」对应的快捷键（例如 Ctrl+F）：",
            text="" if cur == "未设置" else cur,
        )
        if not ok:
            return
        parsed = parse_shortcut_text(text) or read_csp_screen_picker_shortcut()
        self.csp_shortcut = parsed
        self.settings.setValue("picker/csp_shortcut", format_shortcut(parsed) if parsed else "")
        self.shortcut_button.setText(f"键:{format_shortcut(self.csp_shortcut)}")
        self.status_label.setText(f"CSP 屏幕取色键已更新为：{format_shortcut(self.csp_shortcut)}")

    def _update_sync_target_ui(self, index: int):
        if not hasattr(self, "shortcut_button"):
            return
        photoshop = index == 1
        self.shortcut_button.setEnabled(not photoshop)
        self.shortcut_button.setText("PS:I" if photoshop else f"键:{format_shortcut(self.csp_shortcut)}")
        self.shortcut_button.setToolTip(
            "Photoshop 使用 I 工具取色，完成后自动切回画笔"
            if photoshop else
            "CSP「编辑 → 获得屏幕颜色」快捷键（已自动从 CSP 配置读取，点击可手动修改）"
        )
        if hasattr(self, "status_label"):
            self.status_label.setText(
                "Photoshop I 工具取色（同步时会短暂冻结指针）"
                if photoshop else
                f"CSP 屏幕取色键：{format_shortcut(self.csp_shortcut)}（同步时会短暂冻结指针）"
            )

    def _on_pick_committed(self):
        raw_hex = self.hex_edit.text().strip().lstrip("#").upper()
        QApplication.clipboard().setText(raw_hex)
        if self.mode_combo.currentIndex() in (0, 1):
            QTimer.singleShot(15, lambda: self.sync_to_target(force=False))

    def _freeze_seconds(self) -> float:
        return (0.0, 0.3, 0.6, 1.0, 2.0)[self.freeze_combo.currentIndex()]

    def sync_to_target(self, force: bool = False) -> bool:
        raw_hex = self.hex_edit.text().strip().lstrip("#").upper()
        QApplication.clipboard().setText(raw_hex)
        if not force and self.mode_combo.currentIndex() == 3:
            self.status_label.setText(f"已复制 {raw_hex}")
            return False
        if not self.isVisible():
            return False
        self.swatch.repaint()
        QApplication.processEvents()
        # Convert the active swatch's client-area center into physical Win32
        # screen coordinates so HiDPI scaling (125%, 150%, etc.) clicks the
        # exact center of the solid color block instead of the background.
        local_pt = self.swatch.mapTo(self, self.swatch.slot_rect(self.swatch.active_slot).center())
        phys_x, phys_y = client_to_physical_screen(
            int(self.winId()),
            local_pt.x(),
            local_pt.y(),
            self.devicePixelRatioF(),
        )
        cur = self.swatch.current
        kwargs = dict(exclude_hwnd=int(self.winId()) if self.isVisible() else 0,
                      expected_rgb=(cur.red(), cur.green(), cur.blue()),
                      freeze_seconds=self._freeze_seconds())
        if self.mode_combo.currentIndex() == 1:
            ok, msg = sync_color_to_photoshop(phys_x, phys_y, **kwargs)
        else:
            ok, msg = sync_color_to_csp(phys_x, phys_y, shortcut=self.csp_shortcut, **kwargs)
        self.status_label.setText(msg)
        return ok

    def sync_to_csp(self, force: bool = False) -> bool:
        """Compatibility wrapper for callers that explicitly target CSP."""
        current = self.mode_combo.currentIndex()
        self.mode_combo.setCurrentIndex(0)
        try:
            return self.sync_to_target(force=force)
        finally:
            self.mode_combo.setCurrentIndex(current)

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
        self._slot_hsv[self.swatch.active_slot] = [self.hue, self.saturation, self.value]
        r, g, b = vhsv_to_rgb8(self.hue, self.saturation, self.value)
        color = QColor(r, g, b)
        self._last_color = QColor(color)
        self.swatch.set_active_color(color)
        self.previous_color = QColor(self.swatch.previous)
        self.fields["r"].setValue(r)
        self.fields["g"].setValue(g)
        self.fields["b"].setValue(b)
        self.fields["h"].setValue(round(self.hue) % 360)
        self.fields["s"].setValue(round(self.saturation * 100))
        self.fields["v"].setValue(round(self.value * 100))
        self.hex_edit.setText(_format_csp_hex(color))
        self.hsv_summary.setText(f"H {round(self.hue) % 360:03d}   S {round(self.saturation * 100):03d}   V {round(self.value * 100):03d}")
        self.square.set_hue(self.hue)
        self.square.saturation, self.square.value = self.saturation, self.value
        self.square.update()
        self.ring.hue = self.hue
        self.ring.update()
        self._updating = False

    def _open_color_input(self):
        color = self._last_color or QColor("#FF0000")
        dialog = ColorInputDialog(None, (color.red(), color.green(), color.blue()))
        dialog.setWindowFlag(Qt.Tool, True)
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._input_dialog = dialog

        def finished(result):
            if result == QDialog.Accepted:
                self.hue, self.saturation, self.value = rgb8_to_vhsv(*dialog.rgb(), preferred_hue=self.hue)
                self._sync_from_hsv()
                self._on_pick_committed()
            if self._input_dialog is dialog:
                self._input_dialog = None
            dialog.deleteLater()

        dialog.finished.connect(finished)
        dialog.open()

    def _field_changed(self, key):
        if self._updating:
            return
        text = self.fields[key].lineEdit().text().strip()
        try:
            typed = int(text)
        except ValueError:
            typed = self.fields[key].value()
        self._pending_fields[key] = typed
        self._schedule_field_commit()

    def _schedule_field_commit(self):
        if self._field_commit_scheduled:
            return
        self._field_commit_scheduled = True
        QTimer.singleShot(0, self._commit_pending_fields)

    def _commit_pending_fields(self):
        self._field_commit_scheduled = False
        if not self._pending_fields:
            return
        # Moving from R to G to B fires editingFinished on each previous
        # spinbox.  Wait until the whole numeric group has lost focus so that
        # intermediate values cannot rewrite the next field while it is being
        # entered.
        if any(spin.lineEdit().hasFocus() for spin in self.fields.values()):
            QTimer.singleShot(40, self._commit_pending_fields)
            return
        pending = self._pending_fields
        self._pending_fields = {}
        committed_rgb = None
        if any(key in pending for key in "rgb"):
            rgb = tuple(pending.get(key, int(self.fields[key].lineEdit().text() or 0)) for key in "rgb")
            committed_rgb = rgb
            self.hue, self.saturation, self.value = rgb8_to_vhsv(*rgb, preferred_hue=self.hue)
        else:
            self.hue = pending.get("h", int(self.fields["h"].lineEdit().text() or 0))
            self.saturation = pending.get("s", int(self.fields["s"].lineEdit().text() or 0)) / 100
            self.value = pending.get("v", int(self.fields["v"].lineEdit().text() or 0)) / 100
        self._sync_from_hsv()
        if committed_rgb is not None:
            # Preserve the exact integers the user entered.
            self._updating = True
            for key, value in zip("rgb", committed_rgb):
                self.fields[key].setValue(value)
            color = QColor(*committed_rgb)
            self.hex_edit.setText(_format_csp_hex(color))
            self.swatch.set_active_color(color)
            self._updating = False
        self._on_pick_committed()

    def _hex_changed(self):
        if self._updating:
            return
        color = _parse_hex_color(self.hex_edit.text())
        if not color.isValid():
            self._sync_from_hsv()
            return
        self.hue, self.saturation, self.value = rgb8_to_vhsv(
            color.red(), color.green(), color.blue(), preferred_hue=self.hue
        )
        self._sync_from_hsv()
        self._on_pick_committed()


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
        if getattr(self.tools, "_picker_dialog", None) is not None:
            self.tools._picker_dialog.close()
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
