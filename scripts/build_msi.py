"""Build a small, dependency-free MSI around the onedir Windows release.

Run with the project's Windows Python 3.11 environment:
    .venv-win\\Scripts\\python.exe scripts\\build_msi.py \\
        --source ..\\BrushBridge-0.5.0-Windows \\
        --out ..\\BrushBridge-0.5.0-Windows.msi

The MSI embeds the already-built onedir files. It does not download or
execute anything during installation.
"""
from __future__ import annotations

import argparse
import shutil
import uuid
from pathlib import Path

import msilib
import msilib.schema
import msilib.sequence


PRODUCT_CODE = "{8C7A3D8B-1F42-4E5A-9B0A-5B8B4A2C0F50}"
USER_PRODUCT_CODE = "{D7D1D8CE-7C66-4EA6-AE78-BD4D4D5E9B70}"
PRODUCT_NAME = "Brush Bridge"
PRODUCT_VERSION = "0.5.0"
MANUFACTURER = "askir"


def _add_tree(db, cab, parent, source: Path, logical: str, default: str | None = None):
    """Mirror a release directory into the MSI Directory/File tables."""
    directory = msilib.Directory(db, cab, parent, source.name, logical, default or source.name)
    entries = sorted(source.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
    files = [path for path in entries if path.is_file()]
    if files:
        directory.start_component(f"{logical}_cmp", None, 0, files[0].name)
    for path in entries:
        if path.is_dir():
            _add_tree(db, cab, directory, path, msilib.make_id(logical + "_" + path.name))
        elif path.is_file():
            directory.add_file(path.name)
    return directory


def _add_file(db, cab, directory, path: Path):
    return directory.add_file(path.name, src=path.name)


def _add_contents(db, cab, directory, source: Path, logical: str):
    """Add source contents directly below an existing MSI directory."""
    entries = sorted(source.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
    files = [path for path in entries if path.is_file()]
    if files and not directory.component:
        directory.start_component(f"{logical}_cmp", None, 0, files[0].name)
    for path in entries:
        if path.is_dir():
            child = msilib.Directory(
                db, cab, directory, path.name, msilib.make_id(logical + "_" + path.name), path.name
            )
            _add_contents(db, cab, child, path, logical + "_" + path.name)
        elif path.is_file():
            directory.add_file(path.name)
    return directory


def build(source: Path, output: Path) -> Path:
    source = source.resolve()
    output = output.resolve()
    compact = (source / "BrushBridge.exe").is_file() and (source / "_internal").is_dir()
    full = (source / "BrushBridge" / "BrushBridge.exe").is_file()
    if not compact and not full:
        raise SystemExit(f"源发布目录缺少 BrushBridge.exe 或 BrushBridge/BrushBridge.exe：{source}")
    product_code = USER_PRODUCT_CODE if compact else PRODUCT_CODE
    output.parent.mkdir(parents=True, exist_ok=True)
    db = msilib.init_database(
        str(output), msilib.schema, PRODUCT_NAME, product_code, PRODUCT_VERSION, MANUFACTURER
    )
    cab = msilib.CAB("BrushBridge.cab")
    feature = msilib.Feature(
        db,
        "MainFeature",
        "Brush Bridge",
        "Brush Bridge 笔刷格式转换工具（作者：askir，本软件完全免费）",
        1,
    )
    feature.set_current()

    # The physical paths under TARGETDIR are source paths; DefaultDir values
    # describe the installed Windows locations.
    target = msilib.Directory(db, cab, None, str(source), "TARGETDIR", "SourceDir")
    program_files = msilib.Directory(db, cab, target, ".", "ProgramFiles64Folder", ".")
    install = msilib.Directory(db, cab, program_files, ".", "INSTALLDIR", "BrushBridge")

    if compact:
        gui = _add_contents(db, cab, install, source, "User")
    else:
        gui = _add_tree(db, cab, install, source / "BrushBridge", "GUI", "BrushBridge")
        _add_tree(db, cab, install, source / "BrushBridgeCLI", "CLI", "BrushBridgeCLI")
        for name in ("示例笔刷", "实验笔刷测试包"):
            path = source / name
            if path.is_dir():
                _add_tree(db, cab, install, path, msilib.make_id(name), name)
        for name in ("使用说明.md", "第三方说明.md", "LICENSE"):
            path = source / name
            if path.is_file():
                if not install.component:
                    install.start_component("Docs", feature, 0)
                install.add_file(path.name)

    # Use the GUI executable as the component key path for shortcuts.
    gui_component = gui.component
    gui_key = gui.keyfiles.get("BrushBridge.exe")
    if not gui_component or not gui_key:
        raise SystemExit("无法找到 GUI 主程序的 MSI 组件键路径")

    menu = msilib.Directory(db, cab, target, ".", "ProgramMenuFolder", ".")
    icon_path = Path(__file__).resolve().parents[1] / "packaging" / "icon.ico"
    if icon_path.is_file():
        msilib.add_data(db, "Icon", [("BrushBridgeIcon", msilib.Binary(str(icon_path)))])
        icon_name = "BrushBridgeIcon"
    else:
        icon_name = None
    msilib.add_data(
        db,
        "Shortcut",
        [
            (
                "BrushBridgeStart",
                menu.logical,
                "Brush Bridge",
                gui_component,
                f"[#{gui_key}]",
                None,
                "Brush Bridge 笔刷格式转换（作者 askir，完全免费）",
                None,
                icon_name,
                0,
                1,
                "INSTALLDIR",
            )
        ],
    )

    # Standard MSI execute sequence: install files, shortcuts, registration,
    # and the corresponding uninstall actions.
    msilib.add_data(db, "InstallExecuteSequence", msilib.sequence.InstallExecuteSequence)
    msilib.add_data(db, "InstallUISequence", msilib.sequence.InstallUISequence)
    msilib.add_data(
        db,
        "Property",
        [
            ("ARPPRODUCTICON", "BrushBridgeIcon"),
            ("ARPHELPLINK", "https://github.com/CanderFlower/brush-bridge"),
            ("ARPURLINFOABOUT", "https://github.com/CanderFlower/brush-bridge"),
            ("ARPNOMODIFY", "1"),
            ("ARPNOREPAIR", "1"),
        ],
    )
    cab.commit(db)
    db.Close()
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="onedir release directory")
    parser.add_argument("--out", required=True, help="output .msi path")
    args = parser.parse_args()
    path = build(Path(args.source), Path(args.out))
    print(f"MSI: {path}\nSize: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
