"""Smooth shapes for the window, and the program's icon, with the standard
library only.

Tk draws rounded shapes with jagged edges on Linux and Windows, so the
window's cards, buttons and status light are painted here pixel by pixel,
with soft (anti-aliased) edges, onto the colour behind them, and handed to Tk
as images. The icon is painted the same way and can be saved as a PNG file
(used by the installer for the menus and the Dock).
"""

from __future__ import annotations

import math
import struct
import zlib
from typing import List, Optional, Sequence, Tuple

Colour = Tuple[int, int, int]
Rows = List[List[Colour]]


def rgb(value: str) -> Colour:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def hex_colour(colour: Colour) -> str:
    return "#%02x%02x%02x" % colour


def mix(under: Colour, over: Colour, amount: float) -> Colour:
    """``over`` laid on ``under`` with the given opacity (0 to 1)."""
    if amount <= 0:
        return under
    if amount >= 1:
        return over
    return (round(under[0] + (over[0] - under[0]) * amount),
            round(under[1] + (over[1] - under[1]) * amount),
            round(under[2] + (over[2] - under[2]) * amount))


def _coverage(distance: float) -> float:
    """How much of a pixel a shape covers, from the signed distance of the
    pixel's centre to the shape's edge (negative: inside)."""
    return min(1.0, max(0.0, 0.5 - distance))


def _box_distance(x: float, y: float, cx: float, cy: float, hw: float, hh: float, r: float) -> float:
    """Signed distance from (x, y) to a rounded box centred on (cx, cy)."""
    qx = abs(x - cx) - (hw - r)
    qy = abs(y - cy) - (hh - r)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - r


def rounded_box(width: int, height: int, radius: float, fill: Colour, back: Colour,
                line: Optional[Colour] = None, line_width: float = 1.0) -> Rows:
    """A box with rounded corners on a plain background, as rows of colours.
    Only the corners are worked out pixel by pixel; the rest is copied."""
    radius = max(0.0, min(radius, width / 2, height / 2))
    cx, cy, hw, hh = width / 2, height / 2, width / 2, height / 2
    inner_r = max(0.0, radius - line_width)
    corner = int(math.ceil(radius)) + 1

    def at(x: int, y: int) -> Colour:
        px, py = x + 0.5, y + 0.5
        outer = _coverage(_box_distance(px, py, cx, cy, hw, hh, radius))
        if line is None:
            return mix(back, fill, outer)
        inner = _coverage(_box_distance(px, py, cx, cy, hw - line_width, hh - line_width, inner_r))
        return mix(mix(back, line, outer), fill, inner)

    rows: Rows = []
    edge = int(math.ceil(line_width)) if line is not None else 0
    plain = [line if (line is not None and (x < edge or x >= width - edge)) else fill for x in range(width)]
    for y in range(height):
        near_top, near_bottom = y < corner, y >= height - corner
        if near_top or near_bottom:
            rows.append([at(x, y) for x in range(width)])
        elif line is not None and (y < edge or y >= height - edge):
            rows.append([line] * width)
        else:
            row = list(plain)
            if line is not None:  # soft vertical edges of a fractional line
                for x in list(range(edge + 1)) + list(range(width - edge - 1, width)):
                    row[x] = at(x, y)
            rows.append(row)
    return rows


def light(size: int, dot: Colour, halo: Colour, back: Colour, inner: float = 0.42) -> Rows:
    """A status light: a dot inside a soft round halo."""
    c, outer_r, inner_r = size / 2, size / 2, size / 2 * inner
    rows: Rows = []
    for y in range(size):
        row = []
        for x in range(size):
            d = math.hypot(x + 0.5 - c, y + 0.5 - c)
            row.append(mix(mix(back, halo, _coverage(d - outer_r)), dot, _coverage(d - inner_r)))
        rows.append(row)
    return rows


def tk_data(rows: Rows) -> str:
    """Rows of colours in the form ``PhotoImage.put`` takes."""
    return " ".join("{" + " ".join(hex_colour(c) for c in row) + "}" for row in rows)


# -- the icon ---------------------------------------------------------------

ICON_TOP, ICON_BOTTOM = rgb("#5b6ff0"), rgb("#3343b5")
ICON_PAGE, ICON_BAND, ICON_RING = rgb("#ffffff"), rgb("#dfe4fc"), rgb("#1f2b7d")
ICON_CELL, ICON_MARK = rgb("#c9d1f7"), rgb("#4056d6")


def icon_pixels(size: int) -> List[List[Tuple[int, int, int, int]]]:
    """The program's icon: a calendar page on a blue rounded square (RGBA)."""
    n = float(size)
    pixels = [[(0, 0, 0, 0)] * size for _ in range(size)]

    def layer(x0: float, y0: float, x1: float, y1: float, r: float, colour) -> None:
        """Paint a rounded box (fractions of the size) over what is there."""
        cx, cy, hw, hh, rr = (x0 + x1) / 2 * n, (y0 + y1) / 2 * n, (x1 - x0) / 2 * n, (y1 - y0) / 2 * n, r * n
        for y in range(max(0, int(y0 * n) - 1), min(size, int(math.ceil(y1 * n)) + 1)):
            row = pixels[y]
            for x in range(max(0, int(x0 * n) - 1), min(size, int(math.ceil(x1 * n)) + 1)):
                a = _coverage(_box_distance(x + 0.5, y + 0.5, cx, cy, hw, hh, rr))
                if a <= 0:
                    continue
                c = colour(y / n) if callable(colour) else colour
                r0, g0, b0, a0 = row[x]
                out = a + a0 / 255 * (1 - a)
                if out <= 0:
                    continue
                keep = a0 / 255 * (1 - a)
                row[x] = (round((c[0] * a + r0 * keep) / out), round((c[1] * a + g0 * keep) / out),
                          round((c[2] * a + b0 * keep) / out), round(out * 255))

    layer(0.06, 0.06, 0.94, 0.94, 0.22, lambda t: mix(ICON_TOP, ICON_BOTTOM, (t - 0.06) / 0.88))
    layer(0.22, 0.27, 0.78, 0.79, 0.07, ICON_PAGE)
    layer(0.22, 0.27, 0.78, 0.40, 0.07, ICON_BAND)
    layer(0.22, 0.33, 0.78, 0.40, 0.0, ICON_BAND)
    for left in (0.34, 0.60):
        layer(left, 0.20, left + 0.06, 0.33, 0.03, ICON_RING)
    for row, top in enumerate((0.47, 0.62)):
        for column, left in enumerate((0.29, 0.445, 0.60)):
            marked = (row, column) == (0, 1)
            layer(left, top, left + 0.11, top + 0.10, 0.025, ICON_MARK if marked else ICON_CELL)
    return pixels


def png(pixels: Sequence[Sequence[Tuple[int, int, int, int]]]) -> bytes:
    """RGBA pixels as a PNG file."""
    height, width = len(pixels), len(pixels[0])
    raw = b"".join(b"\x00" + bytes(v for p in row for v in p) for row in pixels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def icon_png(size: int) -> bytes:
    return png(icon_pixels(size))


def main(argv=None) -> int:
    """For the installer:
    ``python3 -m rotaplanner.paint icon FILE [SIZE]``: save the icon;
    ``python3 -m rotaplanner.paint iconset FOLDER``: every size a Mac icon needs."""
    import os
    import sys
    args = sys.argv[1:] if argv is None else argv
    if len(args) >= 2 and args[0] == "icon":
        size = int(args[2]) if len(args) > 2 else 256
        with open(args[1], "wb") as handle:
            handle.write(icon_png(size))
        return 0
    if len(args) == 2 and args[0] == "iconset":
        made = {}
        for size in (16, 32, 128, 256, 512):
            for scale, suffix in ((1, ""), (2, "@2x")):
                pixels = size * scale
                if pixels not in made:
                    made[pixels] = icon_png(pixels)
                with open(os.path.join(args[1], f"icon_{size}x{size}{suffix}.png"), "wb") as handle:
                    handle.write(made[pixels])
        return 0
    print("usage: python3 -m rotaplanner.paint icon FILE [SIZE] | iconset FOLDER")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
