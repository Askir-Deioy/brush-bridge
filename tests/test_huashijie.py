from pathlib import Path

import pytest
from PIL import Image

from brush_bridge.core.model import Brush
from brush_bridge.core.plugin import codec, sniff


def test_huashijie_write_roundtrip(tmp_path):
    b = Brush("画世界测试", size_px=140, spacing=0.08, opacity=0.73, flow=0.91,
              tips=[Image.new("L", (32, 24), 180)])
    path = codec("huashijie").write([b], tmp_path / "out.bru")[0]
    info = sniff(path)
    assert info and info.format_id == "huashijie" and info.brush_count == 1
    r = codec("huashijie").read(path)[0]
    assert r.name == "画世界测试"
    assert r.size_px == pytest.approx(140)
    assert r.opacity == pytest.approx(0.73, abs=1e-5)
    assert r.flow == pytest.approx(0.91, abs=1e-5)
    assert r.tips[0].size == (200, 200)


def test_huashijie_external_sample_if_available():
    path = Path.home() / "Downloads" / "罐牌厚涂混色.bru"
    if not path.is_file():
        pytest.skip("user supplied Huashijie sample is not available")
    info = sniff(path)
    assert info and info.brush_count == 11
    brushes = codec("huashijie").read(path)
    assert brushes[7].name == "果冻"
    assert brushes[7].size_px == pytest.approx(140)
    assert brushes[7].opacity == pytest.approx(0.97, abs=1e-4)
    assert brushes[7].flow == pytest.approx(1.0)
    assert brushes[7].tips[0].size == (200, 200)


def test_huashijie_rejects_non_bru(tmp_path):
    p = tmp_path / "bad.bru"
    p.write_bytes(b"not hsjbru")
    assert sniff(p) is None

