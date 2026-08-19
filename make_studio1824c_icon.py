#!/usr/bin/env python3
"""Generate Studio 1824c icon from PreSonus brand colors (no copyright)."""
import cairo
import os

PRESONUS_BLUE = "#0066CC"
PRESONUS_DARK_BLUE = "#004499"
PRESONUS_LIGHT_BLUE = "#3399FF"
WHITE = "#FFFFFF"

def make_studio1824c_icon(size=64):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)

    r = int(PRESONUS_BLUE[1:3], 16) / 255.0
    g = int(PRESONUS_BLUE[3:5], 16) / 255.0
    b = int(PRESONUS_BLUE[5:7], 16) / 255.0

    ctx.set_source_rgb(r, g, b)
    ctx.arc(size / 2, size / 2, size / 2 - 2, 0, 2 * 3.14159)
    ctx.fill()

    dr = int(PRESONUS_DARK_BLUE[1:3], 16) / 255.0
    dg = int(PRESONUS_DARK_BLUE[3:5], 16) / 255.0
    db = int(PRESONUS_DARK_BLUE[5:7], 16) / 255.0
    ctx.set_source_rgb(dr, dg, db)
    ctx.arc(size / 2, size / 2, size / 2 - 6, 0, 2 * 3.14159)
    ctx.set_line_width(3)
    ctx.stroke()

    lr = int(PRESONUS_LIGHT_BLUE[1:3], 16) / 255.0
    lg = int(PRESONUS_LIGHT_BLUE[3:5], 16) / 255.0
    lb = int(PRESONUS_LIGHT_BLUE[5:7], 16) / 255.0
    ctx.set_source_rgb(lr, lg, lb)
    ctx.arc(size / 2, size / 2 - 8, 12, 0, 2 * 3.14159)
    ctx.fill()

    ctx.set_source_rgb(1.0, 1.0, 1.0)
    ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(size * 0.22)
    ext = ctx.text_extents("1824c")
    ctx.move_to(size / 2 - ext.width / 2, size / 2 + ext.height / 3)
    ctx.show_text("1824c")

    return surface

def save_icons():
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".card_icons")
    os.makedirs(cache_dir, exist_ok=True)

    for size in [48, 64, 128, 256]:
        surf = make_studio1824c_icon(size)
        png_path = os.path.join(cache_dir, f"Studio1824c_{size}.png")
        surf.write_to_png(png_path)
        print(f"Created {png_path}")

    main_surf = make_studio1824c_icon(64)
    main_path = os.path.join(cache_dir, "Studio1824c.png")
    main_surf.write_to_png(main_path)
    print(f"Created {main_path}")

if __name__ == "__main__":
    save_icons()