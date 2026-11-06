"""Build the Windows application folders: python scripts/build_exe.py

Output: dist/BrushBridge/BrushBridge.exe and dist/BrushBridgeCLI/BrushBridgeCLI.exe.
The release script places both folders in one ZIP so Windows does not need a
PyInstaller one-file temporary extraction step.
"""
import subprocess
import sys
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sync_build_preview(name: str) -> None:
    """Make the requested build/<name> path runnable before packaging.

    PyInstaller uses ``build`` as a work directory and puts the self-contained
    onedir application under ``dist``.  Copying the runtime beside the workdir
    executable gives local development a stable preview path without changing
    the release assembly flow.
    """
    source = ROOT / "dist" / name
    target = ROOT / "build" / name
    executable = source / f"{name}.exe"
    internal = source / "_internal"
    if not executable.is_file() or not internal.is_dir():
        raise SystemExit(f"无法同步 {name} 预览目录：缺少 {executable} 或 {internal}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(executable, target / executable.name)
    shutil.copytree(internal, target / "_internal", dirs_exist_ok=True)


def main() -> int:
    env = dict(os.environ)
    # Do not let a globally installed Conda/Qt supply incompatible native libraries.
    win = Path(os.environ["SystemRoot"])
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), sys.base_prefix,
                                  str(Path(sys.base_prefix) / "DLLs"), str(win / "System32"), str(win)])
    env.pop("PYTHONPATH", None)
    env.pop("CONDA_PREFIX", None)
    if not (ROOT / "packaging" / "icon.ico").exists():
        subprocess.check_call([sys.executable, str(ROOT / "scripts" / "make_icon.py")])
    launcher = ROOT / "packaging" / "launcher.py"
    launcher.write_text(
        "import sys, traceback\nfrom pathlib import Path\ntry:\n"
        "    from brush_bridge.gui.app import main\n    sys.exit(main())\n"
        "except Exception:\n"
        "    if '--smoke-test' in sys.argv:\n"
        "        dest = Path(sys.argv[sys.argv.index('--smoke-test') + 1])\n"
        "        dest.mkdir(parents=True, exist_ok=True)\n"
        "        (dest / 'startup-error.txt').write_text(traceback.format_exc(), encoding='utf-8')\n"
        "        sys.exit(1)\n    raise\n", encoding="utf-8")
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", "BrushBridge",
        "--icon", str(ROOT / "packaging" / "icon.ico"),
        "--collect-submodules", "brush_bridge",
        "--add-data", f"{ROOT / 'brush_bridge' / 'gui' / 'icon.png'};brush_bridge/gui",
        "--add-data", f"{ROOT / 'brush_bridge' / 'formats' / 'csp_material_template.json'};brush_bridge/formats",
        "--exclude-module", "tkinter",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtMultimedia",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        str(launcher),
    ]
    # Pillow's conda build keeps codec DLLs under Library/bin.  The clean
    # build PATH above intentionally omits that directory, so PyInstaller
    # cannot discover these dependencies automatically.  Bundle the small
    # set used by Image.open/save explicitly; otherwise the frozen GUI fails
    # before it can show an error message.
    pillow_dll_dir = Path(sys.base_prefix) / "Library" / "bin"
    for dll in ("libjpeg.dll", "libpng16.dll", "libtiff.dll", "libwebp.dll",
                "libwebpdecoder.dll", "libwebpdemux.dll", "libwebpmux.dll",
                "openjp2.dll", "lcms2.dll", "zlib.dll", "freetype.dll"):
        path = pillow_dll_dir / dll
        if path.is_file():
            args.append(f"--add-binary={path};.")
    result = 0 if "--cli-only" in sys.argv else subprocess.call(args, cwd=ROOT, env=env)
    if result:
        return result
    cli = ROOT / "packaging" / "cli_launcher.py"
    cli.write_text("import sys\nfrom brush_bridge.cli import main\n\nsys.exit(main())\n", encoding="utf-8")
    cli_args = list(args)
    cli_args[cli_args.index("--windowed")] = "--console"
    cli_args[cli_args.index("BrushBridge")] = "BrushBridgeCLI"
    cli_args[cli_args.index(str(launcher))] = str(cli)
    result = subprocess.call(cli_args, cwd=ROOT, env=env)
    if result:
        return result
    _sync_build_preview("BrushBridge")
    _sync_build_preview("BrushBridgeCLI")
    print(f"Build preview: {ROOT / 'build' / 'BrushBridge' / 'BrushBridge.exe'}")
    print(f"Build CLI preview: {ROOT / 'build' / 'BrushBridgeCLI' / 'BrushBridgeCLI.exe'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
