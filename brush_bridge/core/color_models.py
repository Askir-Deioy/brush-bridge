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

    # Undo the value-dependent exponent to recover the top-edge RGB color.
    top = []
    for channel in values:
        normalized = _clamp(channel / value)
        top.append(0.0 if normalized <= 0.0 else normalized ** value)
    hue, saturation, _ = colorsys.rgb_to_hsv(*top)
    return (0.0 if saturation <= 1e-9 else hue * 360.0), _clamp(saturation), value


def vhsv_roundtrip(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Round-trip helper used by tests and callers that need a quantized check."""
    hue, saturation, value = rgb8_to_vhsv(*rgb)
    return vhsv_to_rgb8(hue, saturation, value)

