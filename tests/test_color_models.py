from brush_bridge.core.color_models import rgb8_to_vhsv, vhsv_to_rgb8, vhsv_roundtrip


def test_vhsv_keeps_hsv_top_edge_and_boundaries():
    assert vhsv_to_rgb8(218.35, 1.0, 1.0) == (0, 92, 255)
    assert vhsv_to_rgb8(218.35, 0.0, 0.565) == (144, 144, 144)
    assert vhsv_to_rgb8(218.35, 0.5, 0.0) == (0, 0, 0)


def test_vhsv_dark_side_matches_sai2_probe_samples():
    # The blue screenshots show these two values at the same hue near the
    # right edge of the square.  Quantization and click placement account for
    # a few levels of difference in the middle sample.
    assert vhsv_to_rgb8(218.35, 1.0, 0.71) == (0, 43, 181)
    dark = vhsv_to_rgb8(218.35, 1.0, 0.294)
    assert dark[2] == 75 and dark[0] <= 2 and dark[1] <= 3


def test_vhsv_rgb_roundtrip_stays_quantized_close():
    for rgb in ((0, 43, 181), (32, 73, 146), (127, 173, 255), (144, 144, 144), (11, 35, 77)):
        got = vhsv_roundtrip(rgb)
        assert max(abs(a - b) for a, b in zip(rgb, got)) <= 2, (rgb, got)


def test_vhsv_inverse_keeps_dark_saturation_coordinate():
    # Direct HSV inversion would report S=100 for these colors because the
    # smallest channel rounded to zero.  V-HSV coordinates remain below 100.
    assert 0.80 < rgb8_to_vhsv(108, 0, 33)[1] < 0.92
    assert 0.62 < rgb8_to_vhsv(47, 0, 82)[1] < 0.78

