"""Assemble the small end-user Windows package.

The developer/evidence release is intentionally kept separate.  This folder is
only the GUI, its PyInstaller runtime, the short instructions and the field
test sheet that users can fill in.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import zipfile
from pathlib import Path


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(root.parent / "BrushBridge-0.5.2.1-User"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    if out.exists():
        if not args.force:
            raise SystemExit(f"Refusing to overwrite existing user release: {out}; use --force")
        shutil.rmtree(out)
    out.mkdir(parents=True)

    bundled = root / "dist" / "BrushBridge"
    exe = bundled / "BrushBridge.exe"
    internal = bundled / "_internal"
    if not exe.is_file() or not internal.is_dir():
        raise SystemExit(f"GUI onedir build is missing: {bundled}")

    # Put the executable directly at the install/package root so it is the
    # first thing a user sees after opening the folder or installing the MSI.
    copy_file(exe, out / "BrushBridge.exe")
    shutil.copytree(internal, out / "_internal")

    copy_file(root / "LICENSE", out / "LICENSE")
    copy_file(root / "docs" / "QUICKSTART_ZH.md", out / "使用说明.md")
    (out / "版本说明.md").write_text(
        "# Brush Bridge 0.5.2.1 简洁用户版\n\n"
        "作者：askir。本软件完全免费。\n\n"
        "这是面向普通用户的精简安装包，安装目录根部的 `BrushBridge.exe` 就是图形界面。\n"
        "本包只包含运行所需文件、简短使用说明、一个示例笔刷和实机回填表；\n"
        "源码、完整验收证据和开发工具保留在项目仓库的源码与开发版发布包中。\n\n"
        "如需回填截图，请参考 `回填表_含原始示例.docx`，并填写 `回填表_空白模板.docx`。\n",
        encoding="utf-8",
    )

    sample = root / "examples" / "verification" / "原创控制笔刷.brushpkg"
    if sample.is_file():
        copy_file(sample, out / "示例笔刷" / sample.name)
    experiments = root / "examples" / "experiments"
    for name in ("实机回填表.xlsx", "实机回填表.csv", "实机回填表说明.md",
                 "回填表_含原始示例.docx", "回填表_空白模板.docx"):
        source = experiments / name
        if source.is_file():
            copy_file(source, out / name)

    files = sorted(path for path in out.rglob("*") if path.is_file())
    (out / "SHA256SUMS.txt").write_text(
        "\n".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(out).as_posix()}"
            for path in files
            if path.name != "SHA256SUMS.txt"
        )
        + "\n",
        encoding="utf-8",
    )

    archive = out.parent / f"{out.name}.zip"
    if archive.exists():
        if not args.force:
            raise SystemExit(f"Refusing to overwrite existing user archive: {archive}; use --force")
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(out.parent).as_posix())

    print(f"User release: {out}")
    print(f"Archive: {archive}")
    print(f"Files: {len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
