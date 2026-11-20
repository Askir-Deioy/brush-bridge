from brush_bridge.core.color_models import rgb8_to_vhsv, vhsv_to_rgb8, vhsv_roundtrip


def test_vhsv_keeps_hsv_top_edge_and_boundaries():
    assert vhsv_to_rgb8(218.35, 1.0, 1.0) == (0, 92, 255)
    assert vhsv_to_rgb8(218.35, 0.0, 0.565) == (144, 144, 144)
    assert vhsv_to_rgb8(218.35, 0.5, 0.0) == (0, 0, 0)


def test_vhsv_dark_side_matches_sai2_probe_samples():
    # Blue screenshots at the right edge of SAI2's square:
    mid = vhsv_to_rgb8(218.35, 1.0, 0.71)
    assert mid[0] == 0 and abs(mid[1] - 43) <= 1 and mid[2] == 181
    dark = vhsv_to_rgb8(218.35, 1.0, 0.294)
    assert dark == (0, 0, 75)


def test_vhsv_matches_sai2_multi_hue_ground_truth():
    # Direct (H, S, V) <-> (R, G, B) readings from SAI2 Ver.2 Preview.2020.06.17
    unclamped = [
        (264, 40, 42, (53, 18, 108)),
        (264, 79, 76, (83, 11, 193)),
        (328, 79, 76, (193, 11, 109)),
        (10,  79, 76, (193, 41, 11)),
        (219, 79, 76, (11, 74, 193)),
        (219, 20, 40, (54, 71, 103)),
    ]
    for h_deg, s_pct, v_pct, expected_rgb in unclamped:
        got_rgb = vhsv_to_rgb8(h_deg, s_pct / 100.0, v_pct / 100.0)
        assert max(abs(a - b) for a, b in zip(got_rgb, expected_rgb)) <= 1, (h_deg, s_pct, v_pct, got_rgb, expected_rgb)

        inv_h, inv_s, inv_v = rgb8_to_vhsv(*expected_rgb)
        assert abs(round(inv_h) % 360 - h_deg) <= 1, (expected_rgb, inv_h, h_deg)
        assert abs(round(inv_s * 100) - s_pct) <= 1, (expected_rgb, inv_s * 100, s_pct)
        assert abs(round(inv_v * 100) - v_pct) <= 1, (expected_rgb, inv_v * 100, v_pct)

    # Clamped dark samples (min channel == 0) with preferred hue preserved
    for h_deg, s_pct, v_pct, expected_rgb in (
        (264, 90, 56, (31, 0, 144)),
        (264, 66, 52, (46, 0, 132)),
    ):
        got_rgb = vhsv_to_rgb8(h_deg, s_pct / 100.0, v_pct / 100.0)
        assert max(abs(a - b) for a, b in zip(got_rgb, expected_rgb)) <= 2, (got_rgb, expected_rgb)
        inv_h, inv_s, inv_v = rgb8_to_vhsv(*expected_rgb, preferred_hue=float(h_deg))
        assert round(inv_h) == h_deg
        assert abs(round(inv_s * 100) - s_pct) <= 3, (expected_rgb, inv_s * 100, s_pct)
        assert abs(round(inv_v * 100) - v_pct) <= 1


def test_vhsv_rgb_roundtrip_is_exact():
    for rgb in (
        (0, 43, 181),
        (32, 73, 146),
        (127, 173, 255),
        (144, 144, 144),
        (11, 35, 77),
        (108, 0, 33),
        (47, 0, 82),
        (31, 0, 144),
        (46, 0, 132),
    ):
        got = vhsv_roundtrip(rgb)
        assert max(abs(a - b) for a, b in zip(rgb, got)) <= 1, (rgb, got)
