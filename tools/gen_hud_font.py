#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""Regenerate gb_hud_font.h , the overlay's 8x8 bitmap font.

An 8x8 cell is small enough that hinting and antialiasing fight you: the
threshold below is picked so stems survive at 1px rather than dropping out.
Run this only when changing the source face or the cell size; the header it
writes is checked in so building the layer needs no font tooling.

    python3 tools/gen_hud_font.py
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
]
CELL = 8
THRESHOLD = 110
FIRST, LAST = 0x20, 0x7E


def pick_font() -> str:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    raise SystemExit("no candidate monospace font found")


def render(path: str, size: int) -> list[list[int]]:
    face = ImageFont.truetype(path, size)
    glyphs = []
    for code in range(FIRST, LAST + 1):
        im = Image.new("L", (CELL, CELL), 0)
        # y=-1 pulls the glyph body up so descenders and caps both fit.
        ImageDraw.Draw(im).text((0, -1), chr(code), font=face, fill=255)
        glyphs.append([
            sum(1 << (7 - x) for x in range(CELL)
                if im.getpixel((x, y)) > THRESHOLD)
            for y in range(CELL)
        ])
    return glyphs


def main() -> None:
    path = pick_font()
    glyphs = render(path, CELL)
    words = []
    for rows in glyphs:
        words.append(rows[0] | rows[1] << 8 | rows[2] << 16 | rows[3] << 24)
        words.append(rows[4] | rows[5] << 8 | rows[6] << 16 | rows[7] << 24)

    out = Path("gb_hud_font.h")
    with out.open("w") as fh:
        fh.write(
            "/* GreenBoost overlay font , 8x8 bitmap, ASCII 0x20..0x7E.\n"
            " *\n"
            f" * Generated from {Path(path).name} at {CELL}px by "
            "tools/gen_hud_font.py.\n"
            " * Two uint32 per glyph, four 8-pixel rows packed per word, which is\n"
            " * the layout shaders/gb_hud.comp unpacks with\n"
            " *   (w >> ((y % 4) * 8)) & 0xFF\n"
            " * Do not hand-edit; regenerate instead.\n"
            " */\n#ifndef GB_HUD_FONT_H\n#define GB_HUD_FONT_H\n\n"
            "#define GB_HUD_FONT_FIRST 0x20u\n"
            f"#define GB_HUD_FONT_COUNT {len(glyphs)}u\n\n"
            "static const unsigned int gb_hud_font[GB_HUD_FONT_COUNT * 2] = {\n")
        for i in range(0, len(words), 6):
            fh.write("    " + " ".join(f"0x{w:08x}u," for w in words[i:i + 6]) + "\n")
        fh.write("};\n\n#endif /* GB_HUD_FONT_H */\n")
    print(f"wrote {out} , {len(glyphs)} glyphs from {path}")


if __name__ == "__main__":
    main()
