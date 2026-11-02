"""Smoke-test frozen executables from a separate cwd with no Python environment."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dist", default="dist")
    p.add_argument("--out", default=".cache/frozen-smoke")
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    dist, out = Path(args.dist).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    runtime = out / "separate-working-directory"
    runtime.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PATH"] = os.environ["SystemRoot"] + "\\System32;" + os.environ["SystemRoot"]
    env["PYTHONIOENCODING"] = "utf-8"
    env["QT_QPA_PLATFORM"] = "offscreen"
    sample = root / "examples/verification/原创控制笔刷.brushpkg"
    def exe(name: str) -> Path:
        direct = dist / f"{name}.exe"
        bundled = dist / name / f"{name}.exe"
        if direct.is_file():
            return direct
        if bundled.is_file():
            return bundled
        raise FileNotFoundError(f"找不到冻结程序：{direct} 或 {bundled}")
    gui_exe, cli_exe = exe("BrushBridge"), exe("BrushBridgeCLI")
    results = []
    def run(name, argv):
        proc = subprocess.Popen([str(x) for x in argv], cwd=runtime, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            stdout, stderr = proc.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                proc.kill()
            raise RuntimeError(f"{name}: startup/operation timeout")
        r = subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
        results.append({"check": name, "returncode": r.returncode, "stdout": r.stdout, "stderr": r.stderr})
        if r.returncode:
            raise RuntimeError(f"{name}: {r.returncode}\n{r.stderr}")
        return r
    run("gui-open-select-save", [gui_exe, "--smoke-test", out / "gui", sample])
    gui = json.loads((out / "gui/gui-smoke.json").read_text(encoding="utf-8"))
    assert gui["export_ok"] and gui["brush_count"] == 4
    listing = run("cli-reopen-gui-package", [cli_exe, "list", out / "gui/gui-roundtrip.brushpkg"])
    assert "硬圆" in listing.stdout, "CLI did not preserve Chinese output"
    run("cli-package-to-photoshop", [cli_exe, "convert", sample,
                                      "--to", "photoshop", "-o", out / "converted.abr"])
    run("cli-reopen-abr", [cli_exe, "list", out / "converted.abr"])
    run("cli-package-to-udongman", [cli_exe, "convert", sample,
                                     "--to", "udongman", "-o", out / "udongman.sut"])
    run("cli-package-to-huashijie", [cli_exe, "convert", sample,
                                      "--to", "huashijie", "-o", out / "huashijie.bru"])
    run("cli-reopen-huashijie", [cli_exe, "list", out / "huashijie.bru"])
    formats = run("cli-formats", [cli_exe, "formats"])
    assert "brushpkg" in formats.stdout and "udongman" in formats.stdout and "huashijie" in formats.stdout
    (out / "packaged-smoke.json").write_text(json.dumps({"checks": results, "gui": gui,
        "isolated_path": env["PATH"], "note": "Frozen executables ran without project Python on PATH; no painting app was tested."},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Frozen build checks: {len(results)} passed. Evidence: {out / 'packaged-smoke.json'}")


if __name__ == "__main__":
    main()
