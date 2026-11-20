"""Color models used by the standalone color picker.

The V-HSV model here matches PaintTool SAI Ver.2's Vivid-HSV color wheel:

- Pure hue at the top-right corner is ``P = hsv_to_rgb(H, 1, 1)``.
- Effective chroma at ``(S, V)`` is ``C = S * (1 - 0.5 * S * (1 - V))``.
- Each RGB channel is ``clamp(V - C * (1 - P_c), 0, 1)``.

Verified across multiple hues against lossless 108x108 SAI2 color-wheel
screenshots (mean absolute error ~0.12 / 255, median 0 / 255, max 1 / 255).
Keeping this math outside the widget makes it fast, exact, and independently
testable.
"""

from __future__ import annotations

import colorsys
import math
from typing import Optional


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def hsv_to_rgb8(hue: float, saturation: float, value: float) -> tuple[int, int, int]:
    """Convert normalized HSV to 8-bit RGB."""
    r, g, b = colorsys.hsv_to_rgb((hue % 360.0) / 360.0, _clamp(saturation), _clamp(value))
    return tuple(round(_clamp(channel) * 255.0) for channel in (r, g, b))


def vhsv_chroma(saturation: float, value: float) -> float:
    """Return SAI2 V-HSV chroma ``C = s * (1 - 0.5 * s * (1 - v))``."""
    s = _clamp(saturation)
    v = _clamp(value)
    return s * (1.0 - 0.5 * s * (1.0 - v))


def _invert_chroma(chroma: float, value: float) -> float:
    """Solve ``s * (1 - 0.5 * s * (1 - v)) = chroma`` for ``s`` in ``[0, 1]``."""
    v = _clamp(value)
    c = max(0.0, float(chroma))
    dv = 1.0 - v
    if dv <= 1e-9:
        return _clamp(c)
    disc = 1.0 - 2.0 * dv * c
    if disc <= 0.0:
        return 1.0
    return _clamp((1.0 - math.sqrt(disc)) / dv)


def vhsv_to_rgb8(hue: float, saturation: float, value: float) -> tuple[int, int, int]:
    """Convert SAI2 V-HSV coordinates ``(H in [0,360), S in [0,1], V in [0,1])`` to 8-bit RGB."""
    s = _clamp(saturation)
    v = _clamp(value)
    if v <= 0.0:
        return 0, 0, 0
    pure = colorsys.hsv_to_rgb((hue % 360.0) / 360.0, 1.0, 1.0)
    chroma = vhsv_chroma(s, v)
    return tuple(
        round(_clamp(v - chroma * (1.0 - p)) * 255.0)
        for p in pure
    )


def rgb8_to_vhsv(
    red: int,
    green: int,
    blue: int,
    preferred_hue: Optional[float] = None,
) -> tuple[float, float, float]:
    """Convert an 8-bit RGB color back to SAI2 V-HSV picker coordinates.

    When all three RGB channels are above zero (the unclamped region), the
    mapping is strictly one-to-one and solved analytically with zero hue drift:
    ``H = standard HSV hue``, ``V = max(r, g, b)``, and ``S`` is the exact root
    of ``s * (1 - 0.5 * s * (1 - v)) = max - min``.

    When the smallest channel is clamped at ``0`` (``C >= V``), multiple
    ``(H, S)`` pairs map to the same quantized RGB.  If ``preferred_hue`` is
    provided and can reproduce ``(red, green, blue)`` within 1 level while
    keeping the current hue fixed, that exact ``(preferred_hue, S, V)`` is
    returned; otherwise the canonical boundary solution (``H = HSV hue``,
    ``C = V``) is returned.
    """
    r = _clamp(red / 255.0)
    g = _clamp(green / 255.0)
    b = _clamp(blue / 255.0)
    c_max = max(r, g, b)
    c_min = min(r, g, b)
    if c_max <= 0.0:
        return (preferred_hue % 360.0) if preferred_hue is not None else 0.0, 0.0, 0.0
    if c_max - c_min <= 1e-9:
        return (preferred_hue % 360.0) if preferred_hue is not None else 0.0, 0.0, c_max

    h_std, _, _ = colorsys.rgb_to_hsv(r, g, b)
    h_deg = (h_std * 360.0) % 360.0

    # Unclamped region: min channel > 0 => exact closed-form inverse.
    if c_min > 0.0:
        chroma = c_max - c_min
        return h_deg, _invert_chroma(chroma, c_max), c_max

    # Clamped region (min channel == 0): if caller supplies preferred_hue in the
    # same 60-degree sector, check if solving C from the middle channel matches
    # the target RGB while preserving the artist's current Hue ring angle.
    if preferred_hue is not None:
        h_pref = preferred_hue % 360.0
        pure = colorsys.hsv_to_rgb(h_pref / 360.0, 1.0, 1.0)
        vals = (r, g, b)
        # Identify middle channel (the one where 0 < p < 1)
        mid_idx = [i for i, p in enumerate(pure) if 1e-6 < p < 1.0 - 1e-6]
        if len(mid_idx) == 1:
            idx = mid_idx[0]
            p_mid = pure[idx]
            c_mid = vals[idx]
            chroma_cand = (c_max - c_mid) / (1.0 - p_mid)
            if chroma_cand >= c_max - 1e-6:
                s_cand = _invert_chroma(chroma_cand, c_max)
                got = vhsv_to_rgb8(h_pref, s_cand, c_max)
                if max(abs(a - b_) for a, b_ in zip(got, (red, green, blue))) <= 1:
                    return h_pref, s_cand, c_max

    # Canonical boundary solution at C = c_max (guaranteed exact RGB reproduction).
    return h_deg, _invert_chroma(c_max, c_max), c_max


def vhsv_roundtrip(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Round-trip helper used by tests and callers that need a quantized check."""
    hue, saturation, value = rgb8_to_vhsv(*rgb)
    return vhsv_to_rgb8(hue, saturation, value)
