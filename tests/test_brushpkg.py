import copy
import hashlib
import json
import threading
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from brush_bridge.core import convert
from brush_bridge.core.model import Brush, Dynamic, Texture, DualTip, WetMix
from brush_bridge.core.plugin import codec, load_plugins
from brush_bridge.core.storage import fingerprint
from brush_bridge.formats.brushpkg import read_package, write_package, PackageError

load_plugins()


def controls():
    tip = Image.new("L", (64, 48), 0)
    d = ImageDraw.Draw(tip)
    d.polygon([(3, 3), (58, 8), (18, 42)], fill=255)
    grain = Image.new("L", (32, 32), 255)
    ImageDraw.Draw(grain).rectangle((0, 0, 12, 31), fill=90)
    return [Brush("硬圆", size_px=24, spacing=0.2),
            Brush("软圆", hardness=0.3, size_px=40, spacing=0.15),
            Brush("非对称印章", tips=[tip], size_px=64, spacing=0.3, angle=20),
            Brush("纹理压感", tips=[tip], size_px=48, opacity=0.8, spacing=0.25,
                  texture=Texture(grain, name="原创颗粒"), size_dynamic=Dynamic(pressure=0.7),
                  group="原创 / 控制样本")]


def test_package_full_roundtrip_and_dedup(tmp_path):
    b = controls()[-1]
    b.opacity = b.flow = 0.0
    b.size_dynamic = Dynamic(pressure=0.0, enabled_inputs=["pressure"],
                             pressure_curve=[(0, 1), (1, 0)], response_curves={"tilt": [(0, 0), (1, 1)]})
    b.dual = DualTip(b.tips[0].copy())
    b.wet = WetMix(True, 0.5, 0.2, 0.8, 0.4)
    b.extensions = {"future": {"unknown": [0, False, None, -3.5], "$type": "user-data"}}
    b.source.native = {"bytes": b"\x00\xff", "tuple": (1, b"test"), "unknown": {17: "整数键"}}
    b.source.snapshots = {"source.brush": b"source bytes"}
    p = write_package([b, copy.deepcopy(b)], tmp_path / "a.brushpkg")
    reopened = read_package(p)
    r = reopened[0]
    assert r.opacity == r.flow == 0 and r.size_dynamic.enabled_inputs == ["pressure"]
    assert r.size_dynamic.pressure_curve == [(0, 1), (1, 0)]
    assert r.size_dynamic.response_curves == b.size_dynamic.response_curves
    assert r.extensions == b.extensions and r.source.native == b.source.native
    assert r.source.snapshots == b.source.snapshots and r.tips[0].tobytes() == b.tips[0].tobytes()
    assert r.dual.image.tobytes() == b.dual.image.tobytes() and r.group == b.group
    with zipfile.ZipFile(p) as z:
        m = json.loads(z.read("manifest.json"))
        assert len([n for n in m["members"] if n.startswith("assets/")]) == 2
    write_package(reopened, tmp_path / "b.brushpkg")
    assert fingerprint(read_package(tmp_path / "b.brushpkg")[0]) == fingerprint(b)


def test_rgba_tip_coverage_survives_target_mask_writers(tmp_path):
    """Target formats that only store grayscale masks must use RGBA coverage."""
    alpha = Image.new("L", (64, 64), 0)
    alpha.paste(192, (16, 16, 48, 48))
    rgba = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
    rgba.putalpha(alpha)
    brush = Brush("透明边缘回归", tips=[rgba], size_px=64, spacing=.2)
    targets = {
        "abr": ("abr", ".abr"), "csp": ("csp", ".sut"), "procreate": ("procreate", ".brushset"),
        "sai2": ("sai2", ".saitdat"), "huashijie": ("huashijie", ".bru"),
        "krita": ("krita", ".bundle"), "gimp": ("gimp", ".gbr"),
    }
    expected_mean = float(np.asarray(alpha).mean())
    for name, (cid, ext) in targets.items():
        files = codec(cid).write([brush], tmp_path / name / ("alpha" + ext))
        readable = [p for p in files if p.suffix.lower() == ext]
        if cid == "sai2":
            readable = [p for p in files if p.suffix.lower() == ".saitdat"]
        decoded = [x for p in readable for x in codec(cid).read(p)]
        assert len(decoded) == 1, name
        assert decoded[0].tips, name
        actual = float(np.asarray(decoded[0].tips[0].convert("L")).mean())
        assert actual == pytest.approx(expected_mean, abs=3.0), (name, expected_mean, actual)


def rewrite_package(path, mutate):
    with zipfile.ZipFile(path) as z:
        data = {n: z.read(n) for n in z.namelist()}
    mutate(data)
    with zipfile.ZipFile(path, "w") as z:
        for n, value in data.items():
            z.writestr(n, value)


def test_unknown_fields_survive(tmp_path):
    p = write_package(controls()[:1], tmp_path / "a.brushpkg")
    def mutate(data):
        m = json.loads(data["manifest.json"])
        m["future_optional"] = {"author": "未来版本", "v": 7}
        name = m["brushes"][0]["path"]
        v = json.loads(data[name])
        v["fields"]["future_field"] = "preserved"
        data[name] = json.dumps(v).encode()
        m["members"][name] = {"size": len(data[name]), "sha256": hashlib.sha256(data[name]).hexdigest()}
        data["manifest.json"] = json.dumps(m).encode()
    rewrite_package(p, mutate)
    q = write_package(read_package(p), tmp_path / "b.brushpkg")
    with zipfile.ZipFile(q) as z:
        m = json.loads(z.read("manifest.json"))
        assert m["future_optional"]["v"] == 7
        assert json.loads(z.read(m["brushes"][0]["path"]))["fields"]["future_field"] == "preserved"


@pytest.mark.parametrize("damage", ["hash", "traversal", "version", "capability", "missing"])
def test_bad_packages_rejected(tmp_path, damage):
    p = write_package(controls()[:1], tmp_path / "bad.brushpkg")
    def mutate(data):
        m = json.loads(data["manifest.json"])
        if damage == "hash":
            data[m["brushes"][0]["path"]] += b" "
        elif damage == "traversal":
            data["../evil"] = b"bad"
        elif damage == "version":
            m["version"] = "99.0"
        elif damage == "capability":
            m["required_features"].append("run-code")
        else:
            del data[m["brushes"][0]["path"]]
        data["manifest.json"] = json.dumps(m).encode()
    rewrite_package(p, mutate)
    with pytest.raises(ValueError):
        read_package(p)


def test_limits_and_invalid_values(tmp_path, monkeypatch):
    from brush_bridge.formats import brushpkg
    p = write_package(controls()[:1], tmp_path / "a.brushpkg")
    monkeypatch.setattr(brushpkg, "MAX_TOTAL", 10)
    with pytest.raises(PackageError):
        read_package(p)
    b = Brush("无效", size_px=float("nan"))
    with pytest.raises(ValueError):
        write_package([b], tmp_path / "invalid.brushpkg")
    assert not (tmp_path / "invalid.brushpkg").exists()


def test_modified_native_is_not_replayed(tmp_path):
    p = tmp_path / "source.abr"
    codec("abr").write([Brush("旧名", size_px=20)], p)
    original = codec("abr").read(p)[0]
    pkg = write_package([original], tmp_path / "master.brushpkg")
    b = read_package(pkg)[0]
    assert b.source.snapshots["source.abr"] == p.read_bytes()
    b.name, b.size_px = "新名", 77
    result = convert.export_detailed([b], "photoshop", tmp_path / "edited.abr")
    assert result.ok
    after = codec("abr").read(result.files[0])[0]
    assert after.name == "新名" and after.size_px == pytest.approx(77)
    assert b.source.native and not b.notes
    direct = tmp_path / "direct.abr"
    codec("abr").write([b], direct)
    assert codec("abr").read(direct)[0].name == "新名"


def test_missing_partial_cancel_and_no_overwrite(tmp_path):
    good = Brush("正常")
    missing = Brush("缺图", resources={"tip": {"status": "missing", "name": "笔尖 A", "required": True}})
    r = convert.export_detailed([good, missing], "csp", tmp_path / "out.sut")
    assert not r.ok and r.succeeded == 1 and r.entries[1]["status"] == "failed"
    old = r.files[0].read_bytes()
    again = convert.export_detailed([good], "csp", tmp_path / "out.sut")
    assert again.ok and again.files[0] != r.files[0] and r.files[0].read_bytes() == old
    event = threading.Event()
    r = convert.export_detailed([good, good], "abr", tmp_path / "cancel.abr", cancel_event=event,
                                progress=lambda done, total: event.set())
    assert r.cancelled and r.succeeded == 1 and r.entries[1]["status"] == "cancelled"
    b = Brush("缺颗粒", resources={"grain": {"status": "missing", "required": False}})
    assert not convert.export_detailed([b], "abr", tmp_path / "blocked.abr").ok
    assert convert.export_detailed([b], "abr", tmp_path / "allowed.abr", ignore_missing_optional=True).ok


def test_procreate_missing_builtin_assets_use_explicit_approximation(tmp_path):
    from brush_bridge.core.model import BrushSource, Texture

    b = Brush("Procreate 缺内置素材", tips=[Image.new("L", (32, 32), 180)],
              texture=Texture(Image.new("L", (16, 16), 120)),
              resources={
                  "tip": {"status": "missing", "name": "Builtin-Shape.jpg", "required": True},
                  "grain": {"status": "missing", "name": "Builtin-Grain.jpg", "required": False},
              }, extensions={"procreate": {"parameters": {"name": "Procreate 缺内置素材"}}},
              source=BrushSource(format="procreate"))
    csp = convert.export_detailed([b], "csp", tmp_path / "approx.sut")
    assert csp.ok and any("缺少原始tip素材" in n for n in csp.entries[0]["warnings"])
    assert any(x["kind"] == "missing_resource" for x in csp.entries[0]["losses"])
    same = convert.export_detailed([b], "procreate", tmp_path / "approx.brushset")
    assert same.ok


APPS = {"photoshop": "abr", "csp": "csp", "udongman": "csp", "procreate": "procreate", "sai2": "sai2"}


@pytest.mark.parametrize("source,target", [(a, b) for a in APPS for b in APPS if a != b])
def test_twenty_routes_via_persistent_package(tmp_path, source, target):
    original = controls()
    sc = codec(APPS[source])
    path = tmp_path / "source" / ("controls" + sc.extensions[0])
    path.parent.mkdir()
    files = sc.write(copy.deepcopy(original), path)
    if sc.id == "sai2":
        source_files = [p for p in files if p.suffix == ".saitdat"]
    else:
        source_files = files
    loaded = [b for p in source_files for b in sc.read(p)]
    assert len(loaded) == len(original)
    pkg = write_package(loaded, tmp_path / "master.brushpkg")
    reopened = read_package(pkg)
    before = [fingerprint(b) for b in reopened]
    target_codec = codec(APPS[target])
    result = convert.export_detailed(reopened, target, tmp_path / "export" / ("converted" + target_codec.extensions[0]))
    assert result.ok, result.entries
    target_files = [p for p in result.files if p.suffix == ".saitdat"] if target == "sai2" else result.files
    decoded = [b for p in target_files for b in target_codec.read(p)]
    assert len(decoded) == len(original)
    assert sorted(b.name for b in decoded) == sorted(b.name for b in loaded)
    assert all(b.size_px > 0 and b.spacing > 0 for b in decoded)
    for b in decoded:
        if b.name in {"非对称印章", "纹理压感"}:
            assert b.tips and max(b.tips[0].getextrema()) > 0
        if b.name == "纹理压感":
            assert b.size_dynamic.pressure > 0 and b.texture is not None
    assert [fingerprint(b) for b in reopened] == before
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert all(e["file_validation"] == "passed" and e["app_import"] == "not_verified" for e in report["brushes"])
    assert all("losses" in e for e in report["brushes"])


def test_negative_procreate_and_zero_survive(tmp_path):
    from brush_bridge.formats import procreate as p
    params = p.template_params("负向规则")
    params.update(dynamicsPressureSize=-0.6, dynamicsGlazedFlow=0, paintOpacity=0)
    b = p.params_to_brush(params, shape=controls()[2].tips[0], grain=None, thumb=None)
    r = read_package(write_package([b], tmp_path / "negative.brushpkg"))[0]
    assert r.opacity == r.flow == 0
    assert r.extensions["procreate"]["parameters"]["dynamicsPressureSize"] == -0.6
    assert r.size_dynamic.response_curves["pressure"][-1] == (1.0, 0.4)


def test_read_failures_are_recorded_even_without_success(tmp_path):
    result = convert.export_detailed([], "abr", tmp_path / "failed.abr", load_errors=["损坏的源笔刷"])
    assert not result.ok and result.entries[0]["status"] == "failed"
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["summary"]["total"] == 1 and report["brushes"][0]["error"] == "损坏的源笔刷"


def test_source_snapshot_survives_source_removal(tmp_path):
    src = tmp_path / "original.abr"
    codec("abr").write([controls()[2]], src)
    b = codec("abr").read(src)[0]
    expected = src.read_bytes()
    pkg = write_package([b], tmp_path / "portable.brushpkg")
    src.unlink()
    reopened = read_package(pkg)
    assert reopened[0].source.snapshots["original.abr"] == expected
    assert convert.export_detailed(reopened, "procreate", tmp_path / "portable.brushset").ok


def test_colour_assets_preserve_pixels_and_alpha(tmp_path):
    im = Image.new("RGBA", (12, 9), (255, 20, 80, 100))
    b = Brush("彩色原图", tips=[im])
    reopened = read_package(write_package([b], tmp_path / "colour.brushpkg"))[0]
    assert reopened.tips[0].mode == "RGBA" and reopened.tips[0].tobytes() == im.tobytes()
