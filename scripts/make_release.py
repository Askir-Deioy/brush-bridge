"""Assemble a local reviewable Windows release, sources, notices and evidence."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(root.parent / "BrushBridge-0.5.0-Windows"))
    p.add_argument("--force", action="store_true",
                   help="replace the one canonical release directory and its matching ZIP")
    args = p.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        if not args.force:
            raise SystemExit(f"Refusing to overwrite an existing release: {out}; use --force to refresh it")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    app_dirs = []
    for name in ("BrushBridge", "BrushBridgeCLI"):
        bundled = root / "dist" / name
        direct = root / "dist" / f"{name}.exe"
        if bundled.is_dir() and (bundled / f"{name}.exe").is_file():
            shutil.copytree(bundled, out / name)
            app_dirs.append(name)
        elif direct.is_file():
            shutil.copy2(direct, out / direct.name)
        else:
            raise SystemExit(f"冻结程序不存在：{bundled} 或 {direct}")
    for name in ("LICENSE", "requirements-dev.lock.txt"):
        shutil.copy2(root / name, out / name)
    shutil.copy2(root / "docs/QUICKSTART_ZH.md", out / "使用说明.md")
    shutil.copytree(root / "docs", out / "docs")
    example = out / "示例笔刷"
    example.mkdir()
    for f in (root / "examples/verification").glob("*.brushpkg"):
        shutil.copy2(f, example / f.name)
    experiments = out / "实验笔刷测试包"
    if (root / "examples/experiments").is_dir():
        shutil.copytree(root / "examples/experiments", experiments)
    evidence = out / "验收记录"
    evidence.mkdir()
    # Keep the stable aggregate; generated route files can be locked by the
    # antivirus/file monitor while a release is assembled and are already
    # reproducible from scripts/verify_routes.py.
    verification = root / "examples/verification"
    if (verification / "compatibility.json").is_file():
        shutil.copy2(verification / "compatibility.json", evidence / "compatibility.json")
    for source, dest in ((".cache/test-final.txt", "test-results.txt"),
                         (".cache/test-results.xml", "test-results.xml"),
                         (".cache/frozen-smoke-release/packaged-smoke.json", "packaged-smoke.json"),
                         (".cache/frozen-smoke-release/gui/window.png", "界面检查.png")):
        shutil.copy2(root / source, evidence / dest)
    public_roundtrip = root / ".cache/public-roundtrips/public-roundtrips.json"
    if public_roundtrip.is_file():
        shutil.copy2(public_roundtrip, evidence / "public-roundtrips.json")
    public_matrix = root / ".cache/public-matrix/public-matrix.json"
    if public_matrix.is_file():
        shutil.copy2(public_matrix, evidence / "public-matrix.json")
    user_evidence = root / ".cache/user-route-evidence.json"
    if user_evidence.is_file():
        shutil.copy2(user_evidence, evidence / "user-route-evidence.json")
    source_root = out / "source"
    source_root.mkdir()
    for folder in ("brush_bridge", "scripts", "tests", "docs", "packaging"):
        shutil.copytree(root / folder, source_root / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "local", "downloaded", "local_*"))
    for name in ("pyproject.toml", "README.md", "LICENSE", "requirements-dev.lock.txt"):
        shutil.copy2(root / name, source_root / name)
    licenses = out / "third-party-licenses"
    licenses.mkdir()
    dependencies = {}
    for name in ("pillow", "numpy", "PySide6-Essentials", "shiboken6", "pyinstaller"):
        d = importlib.metadata.distribution(name)
        dependencies[name] = {"version": d.version, "license": d.metadata.get("License", "See copied metadata"),
                              "project_urls": d.metadata.get_all("Project-URL", [])}
        dest = licenses / name
        dest.mkdir()
        for f in d.files or []:
            if "licenses" in f.parts or f.name in {"METADATA", "LICENSE", "LICENSE.txt", "COPYING"}:
                src = Path(d.locate_file(f))
                if src.is_file():
                    target = dest / Path(*f.parts[1:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
    (licenses / "dependency-metadata.json").write_text(json.dumps(dependencies, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "第三方说明.md").write_text(
        "# 第三方说明\n\n作者：askir。本软件完全免费。\n\n本程序基于 CanderFlower/brush-bridge 的 MIT 源码修改，保留 LICENSE。"
        "依赖维持各自许可，相关文本与元数据在 third-party-licenses；MIT 不替代依赖许可。\n\n"
        "PySide6/Qt 与 Shiboken 来源：https://code.qt.io/cgit/pyside/pyside-setup.git/ ，"
        "Qt 来源：https://code.qt.io/cgit/qt/ 。版本见依赖清单。\n\n"
        "本次修改源码在 source，可按 source/docs/QUICKSTART_ZH.md 重建，并使用自己的依赖构建。"
        "Windows 应用采用 onedir 目录，BrushBridge/BrushBridge.exe 是图形界面，"
        "BrushBridgeCLI/BrushBridgeCLI.exe 是命令行入口；整个发布目录只对应一个 ZIP。"
        "主存档示例为程序生成的原创控制样本。本包不附带 Procreate 官方笔尖库或商业笔刷。\n",
        encoding="utf-8")
    files = sorted(f for f in out.rglob("*") if f.is_file())
    (out / "SHA256SUMS.txt").write_text("\n".join(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(out).as_posix()}"
                                                  for f in files) + "\n", encoding="utf-8")
    archive = out.parent / (out.name + ".zip")
    if archive.exists():
        if not args.force:
            raise SystemExit(f"Refusing to overwrite archive: {archive}; use --force to refresh it")
        archive.unlink()
    if args.force:
        # Keep one deliverable ZIP for this version.  Older runs used suffixes
        # such as -release and -release-final; remove only matching sibling
        # archives beside the selected canonical output.
        for sibling in out.parent.glob(out.name + "*.zip"):
            if sibling.resolve() != archive.resolve():
                sibling.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(out.parent).as_posix())
    print(f"Release: {out}\nArchive: {archive}")


if __name__ == "__main__":
    main()
