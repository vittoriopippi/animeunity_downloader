"""Draws the 16x16 pixel logo (a TV playing) as SVG and PNG icons. Standard library only.

    python3 tools/make_icons.py
"""
import struct
import zlib
from pathlib import Path

SPRITE = """
....I......I....
.....I....I.....
......I..I......
.......II.......
.PPPPPPPPPPPPPP.
.PDDDDDDDDDDDDP.
.PDDDDYDDDDDDDP.
.PDDDDYYDDDDDDP.
.PDDDDYYYDDDDDP.
.PDDDDYYYYDDDDP.
.PDDDDYYYDDDDDP.
.PDDDDYYDDDDDDP.
.PDDDDYDDDDDDDP.
.PPPPPPPPPPPGPP.
..PP........PP..
................
""".split()
COLORS = {"P": "#9d7bff", "D": "#06051a", "Y": "#ffd23f", "I": "#f4efe1", "G": "#45f08a"}
BG = "#0c0b1d"
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "downloader" / "static" / "downloader" / "icons"
TEMPLATES = ROOT / "downloader" / "templates" / "downloader"


def runs():
    """(x, y, width, color) for each horizontal run of one color."""
    for y, row in enumerate(SPRITE):
        x = 0
        while x < len(row):
            c = row[x]
            w = 1
            while x + w < len(row) and row[x + w] == c:
                w += 1
            if c in COLORS:
                yield x, y, w, COLORS[c]
            x += w


def svg(size, background=False):
    rects = "".join(f'<rect x="{x}" y="{y}" width="{w}" height="1" fill="{c}"/>' for x, y, w, c in runs())
    bg = f'<rect width="16" height="16" fill="{BG}"/>' if background else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="{size}" height="{size}" '
            f'shape-rendering="crispEdges" aria-hidden="true">{bg}{rects}</svg>\n')


def png(size, path):
    scale = size // 18  # 16 pixels plus a 1-pixel margin each side
    off = (size - 16 * scale) // 2
    rgb = lambda h: bytes(int(h[i:i + 2], 16) for i in (1, 3, 5))
    rows = []
    for py in range(size):
        line = bytearray(b"\x00")
        for px in range(size):
            sx, sy = (px - off) // scale, (py - off) // scale
            c = SPRITE[sy][sx] if 0 <= sx < 16 and 0 <= sy < 16 and px >= off and py >= off else "."
            line += rgb(COLORS.get(c, BG))
        rows.append(bytes(line))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))
    path.write_bytes(data)


if __name__ == "__main__":
    STATIC.mkdir(parents=True, exist_ok=True)
    (STATIC / "favicon.svg").write_text(svg(32, background=True))
    (TEMPLATES / "logo.svg").write_text(svg(48))
    png(180, STATIC / "icon-180.png")
    png(512, STATIC / "icon-512.png")
    print("icons written")
