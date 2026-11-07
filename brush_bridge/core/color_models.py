"""Color models used by the standalone color picker.

The V-HSV model here is a screenshot-derived SAI-like model.  It keeps the
top edge equal to HSV, then darkens each top-edge RGB channel with a value
dependent exponent.  Keeping this math outside the widget makes it possible
to replace the model after more SAI2 measurements without rewriting the UI.
"""

from __future__ import annotations

import colorsys
import math


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def hsv_to_rgb8(hue: float, saturation: float, value: float) -> tuple[int, int, int]:
    """Convert normalized HSV to 8-bit RGB."""
    r, g, b = colorsys.hsv_to_rgb((hue % 360.0) / 360.0, _clamp(saturation), _clamp(value))
    return tuple(round(_clamp(channel) * 255.0) for channel in (r, g, b))


def vhsv_to_rgb8(hue: float, saturation: float, value: float) -> tuple[int, int, int]:
    """Convert the screenshot-derived V-HSV coordinates to 8-bit RGB.

    The top edge (``value == 1``) is ordinary HSV.  For lower values, each
    normalized top-edge channel ``t`` is evaluated as ``value * t ** (1/value)``.
    A zero channel stays zero.  This produces the dark-side saturation seen in
    the SAI2 screenshots while preserving white, gray, and black boundaries.
    """
    v = _clamp(value)
    if v <= 0.0:
        return 0, 0, 0
    top = hsv_to_rgb8(hue, saturation, 1.0)
    rgb = []
    for channel in top:
        normalized = channel / 255.0
        if normalized <= 0.0:
            rgb.append(0)
        else:
            rgb.append(round(_clamp(v * (normalized ** (1.0 / v))) * 255.0))
    return tuple(rgb)


def rgb8_to_vhsv(red: int, green: int, blue: int) -> tuple[float, float, float]:
    """Convert an 8-bit RGB color back to the V-HSV picker coordinates."""
    values = [_clamp(channel / 255.0) for channel in (red, green, blue)]
    value = max(values)
    if value <= 0.0:
        return 0.0, 0.0, 0.0

    # Quantization makes a direct inverse unreliable in dark saturated colors:
    # several different S coordinates can round the smallest channel to zero.
    # Search around the ordinary HSV hue and choose the V-HSV coordinates whose
    # forward-rendered 8-bit RGB is closest.  This is only used for RGB/HEX
    # edits, not for the pointer-drag path, so the small bounded search keeps
    # the fields aligned with the visible model without making dragging slow.
    hue0, _, _ = colorsys.rgb_to_hsv(*values)
    hue0 *= 360.0
    if max(values) - min(values) <= 1.0 / 255.0:
        return 0.0, 0.0, value

    target = (red, green, blue)

    def error(hue: float, saturation: float) -> int:
        got = vhsv_to_rgb8(hue, saturation, value)
        return sum((a - b) ** 2 for a, b in zip(got, target))

    # A two-level score prefers the lowest S that reproduces the quantized
    # RGB within two levels, matching SAI2's displayed coordinate better than
    # blindly choosing a saturated exact match after 8-bit rounding.
    best = (10**9, 10**9, hue0, 0.0)
    for h_step in (2.0, 0.25):
        if h_step == 2.0:
            h_values = [hue0 - 24.0 + i * h_step for i in range(25)]
            s_values = [i / 50.0 for i in range(51)]
        else:
            h_values = [best[2] - 2.0 + i * h_step for i in range(17)]
            s_values = [max(0.0, best[3] - 0.04) + i * 0.005 for i in range(17)]
        for hue in h_values:
            for saturation in s_values:
                current = error(hue, _clamp(saturation))
                sat = _clamp(saturation)
                score = max(0, current - 2) * 1000 + sat
                if score < best[1]:
                    best = current, score, hue, sat
    return best[2] % 360.0, best[3], value


def vhsv_roundtrip(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Round-trip helper used by tests and callers that need a quantized check."""
    hue, saturation, value = rgb8_to_vhsv(*rgb)
    return vhsv_to_rgb8(hue, saturation, value)

