#!/usr/bin/env python3
"""Draw the integration icon: a shield with a person on it.

The shapes below are the single source. This script writes them as an SVG
(assets/icon.svg, for editing and previews) and rasterises the same shapes
into the PNG files Home Assistant serves from the integration's brand/
folder. Plain Python, no image library needed.

    python tools/brand.py          # write the files
    python tools/brand.py --check  # fail if the files are out of date
"""

from __future__ import annotations

import math
import pathlib
import struct
import sys
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
BRAND = ROOT / "custom_components" / "login_detail" / "brand"
SVG = ROOT / "assets" / "icon.svg"

# The canvas is 256 x 256 units; icon.png is 1 px per unit, icon@2x.png 2.
UNITS = 256
SHIELD = "#1565c0"
PERSON = "#ffffff"

# Paths are lists of ("M", x, y), ("L", x, y) and ("C", x1, y1, x2, y2, x, y).
SHIELD_PATH = [
    ("M", 128, 4),
    ("C", 162, 22, 202, 30, 242, 30),
    ("L", 242, 112),
    ("C", 242, 182, 194, 228, 128, 252),
    ("C", 62, 228, 14, 182, 14, 112),
    ("L", 14, 30),
    ("C", 54, 30, 94, 22, 128, 4),
]
HEAD = (128, 96, 30)  # centre x, centre y, radius
BODY_PATH = [
    ("M", 72, 190),
    ("C", 72, 156, 96, 138, 128, 138),
    ("C", 160, 138, 184, 156, 184, 190),
]
LAYERS = [
    (SHIELD, [SHIELD_PATH]),
    (PERSON, [BODY_PATH, "head"]),
]

SAMPLES = 16  # sub-rows per pixel row, for vertical anti-aliasing
SEGMENTS = 96  # line segments per curve or circle


def svg_path(path: list[tuple]) -> str:
    """One path as SVG path data."""
    return (
        " ".join(f"{cmd} " + " ".join(f"{v:g}" for v in args) for cmd, *args in path)
        + " Z"
    )


def render_svg() -> str:
    """The icon as an SVG document."""
    shapes = []
    for color, parts in LAYERS:
        for part in parts:
            if part == "head":
                cx, cy, r = HEAD
                shapes.append(f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}"/>')
            else:
                shapes.append(f'  <path d="{svg_path(part)}" fill="{color}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{UNITS}" height="{UNITS}" '
        f'viewBox="0 0 {UNITS} {UNITS}">\n' + "\n".join(shapes) + "\n</svg>\n"
    )


def polygon(path: list[tuple]) -> list[tuple[float, float]]:
    """Flatten a path into a closed polygon."""
    points: list[tuple[float, float]] = []
    for cmd, *args in path:
        if cmd in ("M", "L"):
            points.append((args[0], args[1]))
            continue
        x0, y0 = points[-1]
        x1, y1, x2, y2, x3, y3 = args
        for i in range(1, SEGMENTS + 1):
            t = i / SEGMENTS
            u = 1 - t
            points.append(
                (
                    u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
                    u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3,
                )
            )
    return points


def circle(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    """A circle as a polygon."""
    return [
        (
            cx + r * math.cos(2 * math.pi * i / SEGMENTS),
            cy + r * math.sin(2 * math.pi * i / SEGMENTS),
        )
        for i in range(SEGMENTS)
    ]


def coverage(polys: list[list[tuple[float, float]]], size: int) -> list[float]:
    """Fraction of each pixel covered by the polygons (even-odd per polygon)."""
    scale = size / UNITS
    cover = [0.0] * (size * size)
    weight = 1 / SAMPLES
    for poly in polys:
        pts = [(x * scale, y * scale) for x, y in poly]
        edges = list(zip(pts, pts[1:] + pts[:1], strict=True))
        for row in range(size):
            base = row * size
            for sub in range(SAMPLES):
                y = row + (sub + 0.5) / SAMPLES
                xs = sorted(
                    x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                    for (x0, y0), (x1, y1) in edges
                    if (y0 <= y < y1) or (y1 <= y < y0)
                )
                for a, b in zip(xs[::2], xs[1::2], strict=True):
                    a, b = max(a, 0.0), min(b, float(size))
                    for px in range(int(a), min(math.ceil(b), size)):
                        overlap = min(b, px + 1) - max(a, px)
                        if overlap > 0:
                            cover[base + px] += overlap * weight
    return [min(c, 1.0) for c in cover]


def rgb(color: str) -> tuple[int, int, int]:
    """Parse #rrggbb."""
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


def render_png(size: int) -> bytes:
    """The icon as an RGBA PNG of size x size pixels."""
    pixels = [(0.0, 0.0, 0.0, 0.0)] * (size * size)
    for color, parts in LAYERS:
        polys = [circle(*HEAD) if p == "head" else polygon(p) for p in parts]
        r, g, b = rgb(color)
        for i, a in enumerate(coverage(polys, size)):
            if a <= 0:
                continue
            pr, pg, pb, pa = pixels[i]
            out = a + pa * (1 - a)
            pixels[i] = (
                (r * a + pr * pa * (1 - a)) / out,
                (g * a + pg * pa * (1 - a)) / out,
                (b * a + pb * pa * (1 - a)) / out,
                out,
            )
    raw = bytearray()
    for row in range(size):
        raw.append(0)  # filter: none
        for r, g, b, a in pixels[row * size : (row + 1) * size]:
            raw += bytes((round(r), round(g), round(b), round(a * 255)))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def outputs() -> dict[pathlib.Path, bytes]:
    """Every generated file and its content."""
    return {
        SVG: render_svg().encode(),
        BRAND / "icon.png": render_png(UNITS),
        BRAND / "icon@2x.png": render_png(UNITS * 2),
    }


def main() -> int:
    """Write the files, or with --check, verify they are current."""
    check = "--check" in sys.argv[1:]
    stale = []
    for path, content in outputs().items():
        rel = path.relative_to(ROOT)
        if check:
            if not path.is_file() or path.read_bytes() != content:
                stale.append(str(rel))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        print(f"wrote {rel}")
    if stale:
        print(f"out of date, run python tools/brand.py: {stale}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
