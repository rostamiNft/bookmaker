"""Procedural scene renderer (no GPU, no network).

Renders a page illustration from the structured Scene + Character Bible:
background by location/time of day, props, characters with poses and
expressions, then an art-style finishing pass. Returns the image plus metadata
(character bounding boxes, palettes) that the QA stage uses to verify
character presence and colour consistency."""
from __future__ import annotations

import math
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from .figures import OUTLINE, Figure, draw_character
from .palette import hex2rgb, mix, rgba, shade, timed

SS = 2  # supersampling factor for anti-aliasing

SKY = {
    "day": ((126, 196, 240), (220, 240, 252)),
    "sunset": ((112, 92, 170), (255, 186, 120)),
    "night": ((14, 26, 60), (44, 70, 120)),
}
GROUND = {
    "meadow": (132, 196, 104), "forest": (92, 150, 86), "river": (128, 188, 104), "hill": (120, 186, 100),
    "village": (150, 200, 112), "garden": (122, 190, 96), "yard": (110, 170, 90), "field": (140, 200, 110),
}


class Ctx:
    def __init__(self, img, d, W, H, time, rng, ground_y):
        self.img, self.d, self.W, self.H, self.time, self.rng, self.ground_y = img, d, W, H, time, rng, ground_y
        self.anchors: dict = {}
        self.rendered: list[str] = []
        self.skipped: list[str] = []

    def c(self, col, a=255):
        return rgba(timed(col, self.time), a)


# ----------------------------------------------------------------- backgrounds
def gradient(img, top, bottom, y0=0, y1=None):
    d = ImageDraw.Draw(img)
    W, H = img.size
    y1 = H if y1 is None else int(y1)
    for y in range(int(y0), y1):
        t = (y - y0) / max(1, (y1 - y0))
        d.line([(0, y), (W, y)], fill=mix(top, bottom, t))


def hills(ctx: Ctx, base_y, amp, color, seed_off=0, n=3):
    W, H, rng = ctx.W, ctx.H, random.Random(ctx.rng.random() + seed_off)
    pts = [(0, H)]
    phase = rng.random() * 6.28
    for x in range(0, W + 20, 20):
        y = base_y - amp * (0.6 * math.sin(x / W * n * math.pi + phase) + 0.4 * math.sin(x / W * (n + 2.3) * math.pi + phase * 1.7))
        pts.append((x, y))
    pts.append((W, H))
    ctx.d.polygon(pts, fill=ctx.c(color))


def cloud(ctx: Ctx, x, y, s, alpha=230):
    col = (255, 255, 255) if ctx.time == "day" else ((255, 220, 200) if ctx.time == "sunset" else (90, 105, 150))
    for dx, dy, r in ((-1.0, 0, 0.55), (-0.35, -0.35, 0.7), (0.35, -0.2, 0.62), (1.0, 0.05, 0.5), (0, 0.15, 0.6)):
        ctx.d.ellipse([x + (dx - r) * s, y + (dy - r) * s, x + (dx + r) * s, y + (dy + r) * s], fill=rgba(col, alpha))


def stars(ctx: Ctx, n=60):
    for _ in range(n):
        x, y = ctx.rng.random() * ctx.W, ctx.rng.random() * ctx.ground_y * 0.75
        r = ctx.rng.choice([1, 1, 1.5, 2, 2.5]) * SS
        ctx.d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 250, 220, 230))


def star_shape(cx, cy, r_out, r_in, points=5, rot=-math.pi / 2):
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = rot + i * math.pi / points
        pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    return pts


def glow(ctx: Ctx, x, y, r, color=(255, 240, 160), strength=150):
    layer = Image.new("RGBA", ctx.img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.ellipse([x - r, y - r, x + r, y + r], fill=rgba(color, strength))
    layer = layer.filter(ImageFilter.GaussianBlur(r * 0.45))
    ctx.img.alpha_composite(layer)


def tree(ctx: Ctx, x, base, h, kind="round", col=(70, 150, 80)):
    d = ctx.d
    tw = h * 0.09
    d.rectangle([x - tw, base - h * 0.45, x + tw, base], fill=ctx.c((120, 82, 50)), outline=ctx.c(OUTLINE), width=SS)
    if kind == "pine":
        for i in range(3):
            w = h * (0.42 - i * 0.1)
            top = base - h * (0.35 + i * 0.22)
            d.polygon([(x - w, top + h * 0.28), (x + w, top + h * 0.28), (x, top - h * 0.12)], fill=ctx.c(shade(col, 0.85 + i * 0.08)),
                      outline=ctx.c(OUTLINE), width=SS)
    else:
        r = h * 0.32
        for dx, dy, rr in ((-0.5, 0.1, 0.8), (0.5, 0.1, 0.8), (0, -0.35, 0.9), (0, 0.05, 1.0)):
            d.ellipse([x + dx * r - rr * r, base - h * 0.62 + dy * r - rr * r, x + dx * r + rr * r, base - h * 0.62 + dy * r + rr * r],
                      fill=ctx.c(shade(col, 1.0 + dy * 0.2)))
        d.arc([x - r * 1.5, base - h * 0.62 - r * 1.3, x + r * 1.5, base - h * 0.62 + r * 1.1], 160, 380, fill=ctx.c(shade(col, 0.7)), width=SS * 2)


def house(ctx: Ctx, x, base, w, wall=(245, 225, 190), roof=(200, 90, 70), lit=False):
    d = ctx.d
    h = w * 0.75
    d.rectangle([x - w / 2, base - h, x + w / 2, base], fill=ctx.c(wall), outline=ctx.c(OUTLINE), width=SS * 2)
    d.polygon([(x - w * 0.62, base - h), (x + w * 0.62, base - h), (x, base - h - w * 0.5)], fill=ctx.c(roof), outline=ctx.c(OUTLINE), width=SS * 2)
    win = (255, 220, 120) if (lit or ctx.time != "day") else (170, 215, 240)
    for sx in (-1, 1):
        d.rectangle([x + sx * w * 0.25 - w * 0.09, base - h * 0.75, x + sx * w * 0.25 + w * 0.09, base - h * 0.5],
                    fill=rgba(win), outline=ctx.c(OUTLINE), width=SS)
    d.rounded_rectangle([x - w * 0.09, base - h * 0.45, x + w * 0.09, base], radius=w * 0.05, fill=ctx.c(shade(roof, 0.8)),
                        outline=ctx.c(OUTLINE), width=SS)


def flowers(ctx: Ctx, n, y0, y1, palette=((240, 90, 110), (250, 200, 60), (255, 255, 255), (170, 120, 220))):
    for _ in range(n):
        x = ctx.rng.random() * ctx.W
        y = y0 + ctx.rng.random() * (y1 - y0)
        r = (3 + ctx.rng.random() * 3) * SS
        col = ctx.rng.choice(palette)
        for a in range(5):
            ang = a * 2 * math.pi / 5
            ctx.d.ellipse([x + math.cos(ang) * r - r * 0.7, y + math.sin(ang) * r - r * 0.7, x + math.cos(ang) * r + r * 0.7,
                           y + math.sin(ang) * r + r * 0.7], fill=ctx.c(col))
        ctx.d.ellipse([x - r * 0.5, y - r * 0.5, x + r * 0.5, y + r * 0.5], fill=ctx.c((250, 210, 80)))


def grass_tufts(ctx: Ctx, n, y0, y1, col):
    for _ in range(n):
        x = ctx.rng.random() * ctx.W
        y = y0 + ctx.rng.random() * (y1 - y0)
        s = (4 + ctx.rng.random() * 5) * SS
        for dx in (-1, 0, 1):
            ctx.d.line([(x, y), (x + dx * s * 0.6, y - s)], fill=ctx.c(shade(col, 0.75)), width=SS)


def background(ctx: Ctx, loc: str, weather: str):
    W, H, gy, t = ctx.W, ctx.H, ctx.ground_y, ctx.time
    top, bot = SKY[t]
    if weather in ("rain",):
        top, bot = (150, 165, 185), (200, 210, 220)
    if weather == "hot" and t == "day":
        top, bot = (120, 190, 240), (255, 236, 190)
    if loc == "bedroom":
        wall = (236, 214, 190) if t == "day" else (120, 110, 150)
        gradient(ctx.img, shade(wall, 1.05), shade(wall, 0.92), 0, gy)
        floor = (196, 150, 110) if t == "day" else (110, 90, 100)
        ctx.d.rectangle([0, gy, W, H], fill=rgba(floor))
        for i in range(0, W, int(W / 9)):
            ctx.d.line([(i, gy), (i - W * 0.05, H)], fill=rgba(shade(floor, 0.85)), width=SS)
        # rug
        ctx.d.ellipse([W * 0.2, gy + (H - gy) * 0.35, W * 0.8, H * 1.05], fill=rgba((214, 120, 120) if t == "day" else (120, 80, 110)))
        return
    gradient(ctx.img, top, bot, 0, int(gy + 2))
    if t == "day" and weather != "rain":
        sx, sy = W * (0.12 + ctx.rng.random() * 0.2), H * 0.13
        glow(ctx, sx, sy, H * 0.14, (255, 245, 180), 160)
        ctx.d.ellipse([sx - H * 0.06, sy - H * 0.06, sx + H * 0.06, sy + H * 0.06], fill=(255, 236, 140, 255))
    elif t == "sunset":
        sx, sy = W * (0.7 + ctx.rng.random() * 0.15), gy - H * 0.05
        glow(ctx, sx, sy, H * 0.22, (255, 200, 120), 170)
        ctx.d.ellipse([sx - H * 0.07, sy - H * 0.07, sx + H * 0.07, sy + H * 0.07], fill=(255, 210, 120, 255))
    else:
        stars(ctx, 70 if weather != "dark" else 25)
    for _ in range(2 if t != "night" else 1):
        cloud(ctx, ctx.rng.random() * W, H * (0.08 + ctx.rng.random() * 0.2), H * (0.05 + ctx.rng.random() * 0.03), 200 if t != "night" else 120)
    g = GROUND.get(loc, (130, 190, 100))
    if weather == "hot":
        g = mix(g, (200, 180, 100), 0.35)
    far = mix(g, top, 0.45)
    mid = mix(g, top, 0.2)
    if loc == "forest":
        hills(ctx, gy - H * 0.06, H * 0.03, far)
        for i in range(10):  # far row
            x = (i + ctx.rng.random() * 0.6) * W / 9
            tree(ctx, x, gy - H * 0.02, H * (0.28 + ctx.rng.random() * 0.1), "pine", mix((70, 140, 90), top, 0.35))
        for i in range(6):  # near row
            x = (i + 0.2 + ctx.rng.random() * 0.5) * W / 5.5
            tree(ctx, x, gy + H * 0.02, H * (0.4 + ctx.rng.random() * 0.12), "pine" if i % 2 else "round", (70, 145, 85))
    elif loc == "village":
        hills(ctx, gy - H * 0.08, H * 0.04, far)
        cols = [((245, 225, 190), (200, 90, 70)), ((230, 210, 240), (120, 90, 170)), ((250, 240, 210), (90, 140, 200))]
        for i in range(4):
            w, r = cols[i % 3], ctx.rng.random()
            house(ctx, W * (0.1 + i * 0.27 + r * 0.04), gy + H * 0.02, W * (0.13 + r * 0.03), w[0], w[1])
    elif loc in ("hill", "field"):
        hills(ctx, gy - H * 0.1, H * 0.06, far, 1, 2)
        hills(ctx, gy - H * 0.02, H * 0.03, mid, 2, 2)
    else:
        hills(ctx, gy - H * 0.12, H * 0.05, far, 1)
        hills(ctx, gy - H * 0.04, H * 0.035, mid, 2)
        if loc in ("meadow", "garden", "yard"):
            for i in range(2):
                tree(ctx, W * (0.05 + i * 0.85 + ctx.rng.random() * 0.05), gy, H * 0.28, "round", (80, 150, 85))
    # foreground ground
    if loc == "hill":
        pts = [(0, H)] + [(x, gy + H * 0.08 * (abs((x - W / 2) / (W / 2)) ** 1.6)) for x in range(0, W + 10, 10)] + [(W, H)]
        ctx.d.polygon(pts, fill=ctx.c(g))
    else:
        ctx.d.rectangle([0, gy, W, H], fill=ctx.c(g))
    grass_tufts(ctx, 70, gy + H * 0.02, H * 0.98, g)
    if loc == "river":
        ctx.anchors["water_y"] = gy + (H - gy) * 0.55
        wy = ctx.anchors["water_y"]
        water = (90, 160, 220)
        ctx.d.polygon([(0, wy - H * 0.05), (W, wy - H * 0.03), (W, wy + H * 0.1), (0, wy + H * 0.12)], fill=ctx.c(water))
        for i in range(10):
            x = ctx.rng.random() * W
            ctx.d.arc([x, wy - H * 0.01, x + W * 0.05, wy + H * 0.02], 200, 340, fill=ctx.c((220, 240, 255)), width=SS * 2)
    if loc in ("meadow", "garden", "field") and ctx.time != "night":
        flowers(ctx, 40, gy + H * 0.03, H * 0.97)
    elif loc in ("meadow", "garden", "yard"):
        flowers(ctx, 18, gy + H * 0.03, H * 0.97)


# ----------------------------------------------------------------- props
def _held(ctx, name):
    return ctx.anchors.get("hold_hero") or (ctx.W * 0.45, ctx.ground_y + ctx.H * 0.02)


def p_star_glow(ctx, x, y, r, dim=False):
    glow(ctx, x, y, r * (3.2 if not dim else 1.8), (255, 240, 150), 170 if not dim else 90)
    ctx.d.polygon(star_shape(x, y, r, r * 0.45), fill=(255, 226, 90, 255) if not dim else (225, 205, 130, 255), outline=(170, 120, 40, 255), width=SS)
    for sx in (-1, 1):
        ctx.d.ellipse([x + sx * r * 0.2 - r * 0.07, y - r * 0.1, x + sx * r * 0.2 + r * 0.07, y + r * 0.08], fill=(90, 60, 30, 255))
    ctx.d.arc([x - r * 0.18, y + r * 0.0, x + r * 0.18, y + r * 0.28], 20, 160, fill=(90, 60, 30, 255), width=SS)


def p_basket(ctx, x, y, s, star=None):
    if star:
        p_star_glow(ctx, x, y - s * 0.55, s * 0.55, dim=(star == "dim"))
    ctx.d.chord([x - s, y - s * 0.6, x + s, y + s * 0.8], 0, 180, fill=ctx.c((190, 140, 80)), outline=ctx.c(OUTLINE), width=SS * 2)
    for i in range(1, 4):
        ctx.d.line([(x - s + i * s / 2, y + s * 0.1), (x - s + i * s / 2, y + s * 0.65)], fill=ctx.c((150, 105, 60)), width=SS)
    ctx.d.arc([x - s * 0.8, y - s * 1.1, x + s * 0.8, y + s * 0.4], 190, 350, fill=ctx.c((150, 105, 60)), width=SS * 3)


def p_lantern(ctx, x, y, s, lit=True):
    if lit:
        glow(ctx, x, y, s * 4, (255, 220, 130), 180)
    ctx.d.rectangle([x - s * 0.5, y - s * 0.6, x + s * 0.5, y + s * 0.6], fill=(255, 225, 120, 255) if lit else (120, 120, 120, 255),
                    outline=rgba(OUTLINE), width=SS * 2)
    ctx.d.polygon([(x - s * 0.65, y - s * 0.6), (x + s * 0.65, y - s * 0.6), (x, y - s * 1.1)], fill=(160, 110, 50, 255), outline=rgba(OUTLINE), width=SS)
    ctx.d.arc([x - s * 0.35, y - s * 1.5, x + s * 0.35, y - s * 0.9], 180, 360, fill=rgba(OUTLINE), width=SS * 2)


def p_toy(ctx, x, y, s):
    c = (240, 228, 205)
    for sx in (-1, 1):
        ctx.d.ellipse([x + sx * s * 0.3 - s * 0.15, y - s * 1.3, x + sx * s * 0.3 + s * 0.15, y - s * 0.5], fill=ctx.c(c), outline=ctx.c(OUTLINE), width=SS)
    ctx.d.ellipse([x - s * 0.5, y - s * 0.2, x + s * 0.5, y + s * 0.6], fill=ctx.c(c), outline=ctx.c(OUTLINE), width=SS)
    ctx.d.ellipse([x - s * 0.42, y - s * 0.75, x + s * 0.42, y + s * 0.05], fill=ctx.c(c), outline=ctx.c(OUTLINE), width=SS)
    ctx.d.ellipse([x - s * 0.08, y - s * 0.38, x + s * 0.08, y - s * 0.24], fill=ctx.c((220, 50, 50)))
    for sx in (-1, 1):
        ctx.d.ellipse([x + sx * s * 0.16 - s * 0.05, y - s * 0.52, x + sx * s * 0.16 + s * 0.05, y - s * 0.42], fill=ctx.c((40, 30, 30)))


def p_kite(ctx, x, y, s, torn=False, tail=True):
    red, blue = (230, 70, 70), (60, 120, 220)
    ctx.d.polygon([(x, y - s * 0.3), (x - s * 1.3, y - s * 0.6), (x - s * 0.9, y + s * 0.2)], fill=ctx.c(red), outline=ctx.c(OUTLINE), width=SS * 2)
    if torn:
        ctx.d.polygon([(x, y - s * 0.3), (x + s * 0.9, y - s * 0.55), (x + s * 0.5, y - s * 0.1), (x + s * 0.8, y + s * 0.2)],
                      fill=ctx.c(blue), outline=ctx.c(OUTLINE), width=SS * 2)
        ctx.d.line([(x + s * 0.5, y - s * 0.1), (x + s * 0.2, y + s * 0.05)], fill=ctx.c(OUTLINE), width=SS * 2)
    else:
        ctx.d.polygon([(x, y - s * 0.3), (x + s * 1.3, y - s * 0.6), (x + s * 0.9, y + s * 0.2)], fill=ctx.c(blue), outline=ctx.c(OUTLINE), width=SS * 2)
        hx, hy = x + s * 0.75, y - s * 0.2
        ctx.d.polygon([(hx - s * 0.12, hy), (hx, hy + s * 0.12), (hx + s * 0.12, hy), (hx + s * 0.06, hy - s * 0.08), (hx, hy - s * 0.03), (hx - s * 0.06, hy - s * 0.08)],
                      fill=ctx.c((240, 200, 210)))
    ctx.d.ellipse([x - s * 0.28, y - s * 0.55, x + s * 0.28, y + s * 0.35], fill=ctx.c((250, 210, 90)), outline=ctx.c(OUTLINE), width=SS * 2)
    ctx.d.polygon([(x - s * 0.08, y - s * 0.5), (x + s * 0.08, y - s * 0.5), (x, y - s * 0.75)], fill=ctx.c((240, 150, 50)))
    ctx.d.ellipse([x - s * 0.06, y - s * 0.35, x + s * 0.06, y - s * 0.23], fill=ctx.c((40, 30, 30)))
    if tail:
        cols = [(230, 70, 70), (250, 160, 50), (250, 220, 70), (90, 190, 100), (60, 120, 220), (150, 100, 210)]
        px, py = x, y + s * 0.35
        for i, c in enumerate(cols):
            nx, ny = px + math.sin(i * 1.2) * s * 0.25, py + s * 0.32
            ctx.d.line([(px, py), (nx, ny)], fill=ctx.c(c), width=SS * 4)
            px, py = nx, ny


def p_owl_small(ctx, x, y, s):
    ctx.d.ellipse([x - s * 0.6, y - s, x + s * 0.6, y + s * 0.6], fill=ctx.c((140, 105, 70)), outline=ctx.c(OUTLINE), width=SS)
    for sx in (-1, 1):
        glow(ctx, x + sx * s * 0.25, y - s * 0.45, s * 0.35, (255, 230, 120), 120)
        ctx.d.ellipse([x + sx * s * 0.25 - s * 0.18, y - s * 0.63, x + sx * s * 0.25 + s * 0.18, y - s * 0.27], fill=(255, 230, 120, 255))
        ctx.d.ellipse([x + sx * s * 0.25 - s * 0.07, y - s * 0.52, x + sx * s * 0.25 + s * 0.07, y - s * 0.38], fill=(30, 20, 10, 255))
    ctx.d.polygon([(x - s * 0.08, y - s * 0.28), (x + s * 0.08, y - s * 0.28), (x, y - s * 0.12)], fill=(240, 170, 60, 255))


def p_hedgehog(ctx, x, y, s):
    pts = [(x - s, y)] + [(x + math.cos(math.pi * i / 10) * s * (1 if i % 2 else 0.8) * -1, y - math.sin(math.pi * i / 10) * s * (1 if i % 2 else 0.8)) for i in range(11)] + [(x + s, y)]
    ctx.d.polygon(pts, fill=ctx.c((110, 80, 60)), outline=ctx.c(OUTLINE), width=SS)
    ctx.d.ellipse([x + s * 0.6, y - s * 0.45, x + s * 1.25, y + s * 0.05], fill=ctx.c((220, 190, 160)), outline=ctx.c(OUTLINE), width=SS)
    ctx.d.ellipse([x + s * 1.15, y - s * 0.25, x + s * 1.3, y - s * 0.12], fill=ctx.c((40, 30, 30)))


def p_frog(ctx, x, y, s):
    g = (110, 190, 90)
    ctx.d.ellipse([x - s, y - s * 0.8, x + s, y + s * 0.2], fill=ctx.c(g), outline=ctx.c(OUTLINE), width=SS)
    for sx in (-1, 1):
        ctx.d.ellipse([x + sx * s * 0.5 - s * 0.3, y - s * 1.15, x + sx * s * 0.5 + s * 0.3, y - s * 0.55], fill=ctx.c(g), outline=ctx.c(OUTLINE), width=SS)
        ctx.d.ellipse([x + sx * s * 0.5 - s * 0.12, y - s * 0.97, x + sx * s * 0.5 + s * 0.12, y - s * 0.73], fill=ctx.c((30, 30, 30)))
    ctx.d.arc([x - s * 0.5, y - s * 0.6, x + s * 0.5, y - s * 0.1], 20, 160, fill=ctx.c((40, 70, 40)), width=SS * 2)


def p_duck(ctx, x, y, s, baby=False):
    col = (250, 220, 90) if baby else (250, 250, 245)
    ctx.d.ellipse([x - s, y - s * 0.7, x + s * 0.6, y + s * 0.2], fill=ctx.c(col), outline=ctx.c(OUTLINE), width=SS)
    ctx.d.ellipse([x + s * 0.2, y - s * 1.3, x + s * 0.9, y - s * 0.6], fill=ctx.c(col), outline=ctx.c(OUTLINE), width=SS)
    ctx.d.polygon([(x + s * 0.85, y - s * 1.0), (x + s * 1.3, y - s * 0.92), (x + s * 0.85, y - s * 0.8)], fill=ctx.c((245, 150, 50)))
    ctx.d.ellipse([x + s * 0.55, y - s * 1.1, x + s * 0.68, y - s * 0.97], fill=ctx.c((30, 30, 30)))


def p_sunflower(ctx, x, base, h, droop=False):
    ctx.d.line([(x, base), (x, base - h)], fill=ctx.c((80, 150, 70)), width=SS * 3)
    ctx.d.ellipse([x, base - h * 0.5, x + h * 0.18, base - h * 0.42], fill=ctx.c((90, 165, 80)))
    hx, hy = (x + h * 0.12, base - h * 0.92) if droop else (x, base - h)
    r = h * 0.16
    for a in range(12):
        ang = a * math.pi / 6
        ctx.d.ellipse([hx + math.cos(ang) * r - r * 0.5, hy + math.sin(ang) * r - r * 0.5, hx + math.cos(ang) * r + r * 0.5, hy + math.sin(ang) * r + r * 0.5],
                      fill=ctx.c((250, 200, 50) if not droop else (205, 170, 70)))
    ctx.d.ellipse([hx - r * 0.7, hy - r * 0.7, hx + r * 0.7, hy + r * 0.7], fill=ctx.c((120, 80, 40)), outline=ctx.c(OUTLINE), width=SS)


def p_stream(ctx, dry=False):
    W, H, gy = ctx.W, ctx.H, ctx.ground_y
    col = (95, 170, 225) if not dry else (175, 150, 115)
    pts_top, pts_bot = [], []
    for i in range(0, 21):
        x = W * i / 20
        y = gy + (H - gy) * (0.45 + 0.12 * math.sin(i / 3.0))
        pts_top.append((x, y))
        pts_bot.append((x, y + H * (0.07 + 0.02 * math.cos(i / 2.0))))
    ctx.d.polygon(pts_top + pts_bot[::-1], fill=ctx.c(col))
    if dry:
        for i in range(14):
            x = ctx.rng.random() * W
            y = pts_top[min(20, int(x / W * 20))][1] + H * 0.03
            ctx.d.line([(x, y), (x + W * 0.02, y + H * 0.015), (x + W * 0.035, y)], fill=ctx.c((120, 95, 70)), width=SS)
        for i in range(10):
            x = ctx.rng.random() * W
            y = pts_top[min(20, int(x / W * 20))][1] + H * 0.035
            r = H * 0.012
            ctx.d.ellipse([x - r * 1.4, y - r, x + r * 1.4, y + r], fill=ctx.c((225, 220, 210)), outline=ctx.c(OUTLINE), width=SS)
    else:
        for i in range(12):
            x = ctx.rng.random() * W
            y = pts_top[min(20, int(x / W * 20))][1] + H * 0.03
            ctx.d.arc([x, y, x + W * 0.04, y + H * 0.02], 200, 340, fill=ctx.c((230, 245, 255)), width=SS * 2)
    ctx.anchors["stream_y"] = pts_top[10][1]


def draw_props(ctx: Ctx, props: list[str], phase: str):
    """phase 'back' = behind characters, 'front' = in front / held."""
    W, H, gy, d, rng = ctx.W, ctx.H, ctx.ground_y, ctx.d, ctx.rng
    back = {"house", "blue_house", "apple_tree", "oak", "willows", "star_sky", "moon", "cloud_moon", "falling_star", "rising_star",
            "kites_sky", "stream", "dry_stream", "bridge", "bridge_small", "fence", "gate", "swing", "bed", "window_moon", "door",
            "sunflowers", "sunflowers_droop", "reeds", "bushes", "brambles", "pond", "dam", "crowd", "farmer", "rain", "wind",
            "birds", "rocks", "stones", "kite_high", "kite_low", "kite_launch", "table_craft", "flowers", "owl_fence", "bear"}
    for p in props:
        if (phase == "back") != (p in back):
            continue
        done = True
        if p == "house":
            house(ctx, W * 0.82, gy + H * 0.03, W * 0.24, lit=ctx.time != "day")
        elif p == "blue_house":
            house(ctx, W * 0.8, gy + H * 0.02, W * 0.26, (160, 200, 240), (70, 100, 170))
        elif p in ("apple_tree", "oak"):
            tree(ctx, W * 0.84, gy + H * 0.04, H * 0.62, "round", (80, 160, 80))
            if p == "apple_tree":
                for _ in range(8):
                    ax, ay = W * 0.84 + (rng.random() - 0.5) * H * 0.3, gy - H * 0.38 + (rng.random() - 0.5) * H * 0.2
                    d.ellipse([ax - H * 0.012, ay - H * 0.012, ax + H * 0.012, ay + H * 0.012], fill=ctx.c((220, 50, 50)))
        elif p == "willows":
            for x in (0.1, 0.9):
                tree(ctx, W * x, gy + H * 0.02, H * 0.5, "round", (120, 170, 90))
        elif p == "star_sky":
            stars(ctx, 40)
            p_star_glow(ctx, W * 0.72, H * 0.14, H * 0.03)
        elif p in ("moon", "cloud_moon"):
            mx, my, r = W * 0.8, H * 0.15, H * 0.07
            glow(ctx, mx, my, r * 2.2, (230, 235, 255), 120)
            d.ellipse([mx - r, my - r, mx + r, my + r], fill=(250, 245, 215, 255))
            d.ellipse([mx - r * 0.4, my - r * 0.5, mx - r * 0.1, my - r * 0.2], fill=(230, 222, 190, 255))
            if p == "cloud_moon":
                cloud(ctx, mx - r * 0.3, my + r * 0.5, r * 1.1, 210)
        elif p == "falling_star":
            x0, y0, x1, y1 = W * 0.85, H * 0.06, W * 0.55, H * 0.38
            for i in range(30):
                t = i / 30
                rr = H * 0.004 + t * H * 0.01
                d.ellipse([x0 + (x1 - x0) * t - rr, y0 + (y1 - y0) * t - rr, x0 + (x1 - x0) * t + rr, y0 + (y1 - y0) * t + rr], fill=(255, 240, 180, int(60 + 150 * t)))
            p_star_glow(ctx, x1, y1, H * 0.035)
        elif p == "rising_star":
            x, y = W * 0.5, H * 0.12
            for i in range(25):
                t = i / 25
                rr = H * 0.012 * (1 - t) + H * 0.003
                d.ellipse([x - rr + math.sin(t * 6) * W * 0.02, y + t * H * 0.35 - rr, x + rr + math.sin(t * 6) * W * 0.02, y + t * H * 0.35 + rr], fill=(255, 240, 180, int(200 * (1 - t))))
            p_star_glow(ctx, x, y, H * 0.04)
        elif p == "kites_sky":
            for i, (kx, ky, c) in enumerate(((0.15, 0.12, (240, 120, 60)), (0.35, 0.22, (120, 200, 90)), (0.88, 0.1, (200, 100, 200)), (0.65, 0.07, (250, 210, 60)))):
                x, y, s = W * kx, H * ky, H * 0.04
                d.polygon([(x, y - s), (x + s * 0.7, y), (x, y + s * 1.2), (x - s * 0.7, y)], fill=ctx.c(c), outline=ctx.c(OUTLINE), width=SS)
                d.line([(x, y + s * 1.2), (x + s * 0.3, y + s * 2.2), (x - s * 0.2, y + s * 3)], fill=ctx.c((250, 250, 250)), width=SS)
        elif p in ("kite_high", "kite_low", "kite_launch"):
            kx, ky = {"kite_high": (0.62, 0.16), "kite_low": (0.7, 0.42), "kite_launch": (0.6, 0.3)}[p]
            p_kite(ctx, W * kx, H * ky, H * 0.09)
            ctx.anchors["kite_string"] = (W * kx, H * ky + H * 0.03)
        elif p in ("stream", "dry_stream"):
            p_stream(ctx, dry=(p == "dry_stream"))
        elif p in ("bridge", "bridge_small"):
            by = ctx.anchors.get("stream_y", gy + (H - gy) * 0.45)
            x0, x1 = (W * 0.08, W * 0.92) if p == "bridge" else (W * 0.55, W * 0.85)
            for i in range(9):
                x = x0 + (x1 - x0) * i / 8
                col = (170, 120, 70) if not (p == "bridge" and i == 4) else (130, 90, 50)
                d.rectangle([x - W * 0.015, by - H * 0.03, x + W * 0.015, by + H * 0.04], fill=ctx.c(col), outline=ctx.c(OUTLINE), width=SS)
            d.line([(x0, by - H * 0.1), (x1, by - H * 0.1)], fill=ctx.c((120, 80, 50)), width=SS * 3)
        elif p == "fence":
            for i in range(14):
                x = W * i / 13
                d.rectangle([x - W * 0.012, gy - H * 0.12, x + W * 0.012, gy + H * 0.01], fill=ctx.c((220, 200, 170)), outline=ctx.c(OUTLINE), width=SS)
            d.rectangle([0, gy - H * 0.09, W, gy - H * 0.075], fill=ctx.c((200, 180, 150)))
        elif p == "owl_fence":
            for i in range(14):
                x = W * i / 13
                d.rectangle([x - W * 0.012, gy - H * 0.12, x + W * 0.012, gy + H * 0.01], fill=ctx.c((220, 200, 170)), outline=ctx.c(OUTLINE), width=SS)
            d.rectangle([0, gy - H * 0.09, W, gy - H * 0.075], fill=ctx.c((200, 180, 150)))
            p_owl_small(ctx, W * 0.78, gy - H * 0.16, H * 0.06)
        elif p == "gate":
            gx = W * 0.8
            for i in range(5):
                d.rectangle([gx + i * W * 0.025, gy - H * 0.16, gx + i * W * 0.025 + W * 0.015, gy + H * 0.01], fill=ctx.c((170, 130, 90)), outline=ctx.c(OUTLINE), width=SS)
            d.line([(gx, gy - H * 0.12), (gx + W * 0.115, gy - H * 0.05)], fill=ctx.c((140, 100, 70)), width=SS * 3)
        elif p == "swing":
            sx = W * 0.72
            d.line([(sx - W * 0.1, gy + H * 0.02), (sx - W * 0.06, gy - H * 0.4), (sx + W * 0.06, gy - H * 0.4), (sx + W * 0.1, gy + H * 0.02)], fill=ctx.c((150, 105, 70)), width=SS * 4)
            for dx in (-0.03, 0.03):
                d.line([(sx + W * dx, gy - H * 0.4), (sx + W * dx, gy - H * 0.1)], fill=ctx.c((90, 80, 70)), width=SS * 2)
            d.rectangle([sx - W * 0.04, gy - H * 0.11, sx + W * 0.04, gy - H * 0.09], fill=ctx.c((170, 120, 70)), outline=ctx.c(OUTLINE), width=SS)
            ctx.anchors["swing_ground"] = (sx, gy + H * 0.06)
        elif p == "bed":
            bx0, bx1, by = W * 0.08, W * 0.62, gy + H * 0.08
            d.rounded_rectangle([bx0, by - H * 0.2, bx1, by], radius=H * 0.03, fill=ctx.c((150, 100, 70)), outline=ctx.c(OUTLINE), width=SS * 2)
            d.rounded_rectangle([bx0 - W * 0.02, by - H * 0.42, bx0 + W * 0.03, by + H * 0.04], radius=H * 0.02, fill=ctx.c((130, 85, 60)), outline=ctx.c(OUTLINE), width=SS * 2)
            d.rounded_rectangle([bx0 + W * 0.02, by - H * 0.27, bx1, by - H * 0.12], radius=H * 0.04, fill=ctx.c((120, 170, 230)), outline=ctx.c(OUTLINE), width=SS * 2)
            d.ellipse([bx0 + W * 0.03, by - H * 0.33, bx0 + W * 0.17, by - H * 0.22], fill=ctx.c((250, 250, 245)), outline=ctx.c(OUTLINE), width=SS)
            ctx.anchors["pillow"] = (bx0 + W * 0.1, by - H * 0.3)
        elif p == "window_moon":
            wx0, wy0, wx1, wy1 = W * 0.66, H * 0.1, W * 0.92, H * 0.45
            d.rectangle([wx0, wy0, wx1, wy1], fill=(20, 32, 70, 255), outline=rgba(OUTLINE), width=SS * 3)
            for _ in range(10):
                x, y = wx0 + rng.random() * (wx1 - wx0), wy0 + rng.random() * (wy1 - wy0)
                d.ellipse([x - SS * 1.5, y - SS * 1.5, x + SS * 1.5, y + SS * 1.5], fill=(255, 250, 220, 255))
            r = (wx1 - wx0) * 0.14
            d.ellipse([wx1 - r * 3, wy0 + r, wx1 - r, wy0 + r * 3], fill=(250, 245, 215, 255))
            d.line([((wx0 + wx1) / 2, wy0), ((wx0 + wx1) / 2, wy1)], fill=rgba(OUTLINE), width=SS * 2)
            d.line([(wx0, (wy0 + wy1) / 2), (wx1, (wy0 + wy1) / 2)], fill=rgba(OUTLINE), width=SS * 2)
            d.rectangle([wx0 - W * 0.03, wy0 - H * 0.02, wx0 + W * 0.01, wy1 + H * 0.05], fill=ctx.c((200, 120, 140)))
            d.rectangle([wx1 - W * 0.01, wy0 - H * 0.02, wx1 + W * 0.03, wy1 + H * 0.05], fill=ctx.c((200, 120, 140)))
        elif p == "door":
            d.rectangle([W * 0.82, gy - H * 0.45, W * 0.98, gy + H * 0.02], fill=(255, 220, 140, 255), outline=rgba(OUTLINE), width=SS * 2)
            glow(ctx, W * 0.9, gy - H * 0.2, H * 0.25, (255, 220, 140), 120)
        elif p in ("sunflowers", "sunflowers_droop"):
            for i in range(7):
                p_sunflower(ctx, W * (0.04 + i * 0.15 + rng.random() * 0.03), gy + H * 0.02, H * (0.32 + rng.random() * 0.1), droop=(p == "sunflowers_droop"))
        elif p == "reeds":
            for i in range(25):
                x = W * 0.6 + rng.random() * W * 0.38
                d.line([(x, gy + H * 0.2), (x + rng.random() * W * 0.02, gy - H * 0.05)], fill=ctx.c((110, 150, 70)), width=SS * 2)
        elif p in ("bushes", "brambles"):
            for i in range(6):
                x = W * (0.05 + i * 0.18)
                r = H * (0.08 + rng.random() * 0.04)
                d.ellipse([x - r * 1.3, gy - r * 1.1, x + r * 1.3, gy + r * 0.5], fill=ctx.c((60, 120, 70) if p == "bushes" else (80, 100, 60)), outline=ctx.c(OUTLINE), width=SS)
                if p == "brambles":
                    for _ in range(6):
                        tx, ty = x + (rng.random() - 0.5) * r * 2, gy - rng.random() * r
                        d.polygon([(tx, ty), (tx + r * 0.08, ty - r * 0.18), (tx + r * 0.16, ty)], fill=ctx.c((150, 110, 70)))
        elif p == "pond":
            d.ellipse([W * 0.55, gy + H * 0.02, W * 1.05, gy + H * 0.16], fill=ctx.c((80, 150, 200)), outline=ctx.c(OUTLINE), width=SS)
        elif p == "dam":
            for i in range(16):
                x = W * 0.42 + rng.random() * W * 0.3
                y = gy + H * 0.05 + rng.random() * H * 0.12
                d.line([(x - W * 0.07, y + rng.random() * H * 0.03), (x + W * 0.07, y - rng.random() * H * 0.03)], fill=ctx.c((120, 85, 55)), width=SS * 5)
            for i in range(5):
                x, y = W * 0.45 + rng.random() * W * 0.25, gy + H * 0.08 + rng.random() * H * 0.08
                d.rectangle([x, y, x + W * 0.02, y + H * 0.025], fill=ctx.c(rng.choice([(200, 60, 60), (200, 200, 210), (60, 120, 200)])), outline=ctx.c(OUTLINE), width=SS)
        elif p == "crowd":
            for i in range(9):
                x = W * (0.04 + i * 0.12)
                c = rng.choice([(230, 100, 100), (100, 160, 230), (240, 200, 90), (140, 200, 120), (190, 130, 210)])
                d.ellipse([x - W * 0.02, gy - H * 0.11, x + W * 0.02, gy - H * 0.06], fill=ctx.c((235, 200, 170)))
                d.rounded_rectangle([x - W * 0.025, gy - H * 0.065, x + W * 0.025, gy + H * 0.0], radius=W * 0.01, fill=ctx.c(c))
        elif p == "farmer":
            x = W * 0.8
            d.ellipse([x - W * 0.03, gy - H * 0.32, x + W * 0.03, gy - H * 0.24], fill=ctx.c((220, 180, 150)), outline=ctx.c(OUTLINE), width=SS)
            d.polygon([(x - W * 0.06, gy - H * 0.29), (x + W * 0.06, gy - H * 0.29), (x, gy - H * 0.36)], fill=ctx.c((230, 200, 110)), outline=ctx.c(OUTLINE), width=SS)
            d.rounded_rectangle([x - W * 0.035, gy - H * 0.24, x + W * 0.035, gy - H * 0.05], radius=W * 0.01, fill=ctx.c((90, 140, 190)), outline=ctx.c(OUTLINE), width=SS)
            d.line([(x + W * 0.03, gy - H * 0.2), (x + W * 0.09, gy - H * 0.27)], fill=ctx.c((220, 180, 150)), width=SS * 4)
        elif p == "rain":
            for _ in range(160):
                x, y = rng.random() * W, rng.random() * H
                d.line([(x, y), (x - W * 0.006, y + H * 0.03)], fill=(220, 230, 255, 150), width=SS)
        elif p == "wind":
            for i in range(6):
                y = H * (0.2 + i * 0.1)
                x = rng.random() * W * 0.5
                d.arc([x, y, x + W * 0.35, y + H * 0.08], 200, 340, fill=(255, 255, 255, 170), width=SS * 2)
            for _ in range(10):
                x, y = rng.random() * W, rng.random() * gy
                d.ellipse([x, y, x + W * 0.012, y + H * 0.008], fill=ctx.c((120, 170, 80)))
        elif p == "birds":
            for _ in range(5):
                x, y = rng.random() * W, H * (0.08 + rng.random() * 0.2)
                s = H * 0.02
                d.arc([x - s, y, x, y + s], 200, 340, fill=ctx.c((60, 60, 70)), width=SS * 2)
                d.arc([x, y, x + s, y + s], 200, 340, fill=ctx.c((60, 60, 70)), width=SS * 2)
        elif p in ("rocks", "stones"):
            n = 6 if p == "rocks" else 5
            for i in range(n):
                if p == "stones":
                    x, y = W * (0.12 + i * 0.19), ctx.anchors.get("water_y", gy + (H - gy) * 0.55) + H * 0.02
                else:
                    x, y = W * (0.05 + rng.random() * 0.9), gy + H * (0.02 + rng.random() * 0.2)
                r = H * (0.03 + rng.random() * 0.015)
                d.ellipse([x - r * 1.5, y - r, x + r * 1.5, y + r * 0.6], fill=ctx.c((160, 155, 150)), outline=ctx.c(OUTLINE), width=SS)
        elif p == "bear":
            x, y, s = W * 0.7, gy + H * 0.08, H * 0.12
            d.ellipse([x - s * 1.6, y - s, x + s * 1.6, y + s * 0.4], fill=ctx.c((130, 90, 60)), outline=ctx.c(OUTLINE), width=SS * 2)
            d.ellipse([x + s * 0.9, y - s * 1.1, x + s * 1.9, y - s * 0.1], fill=ctx.c((130, 90, 60)), outline=ctx.c(OUTLINE), width=SS * 2)
            for ex in (1.1, 1.7):
                d.ellipse([x + s * ex - s * 0.18, y - s * 1.25, x + s * ex + s * 0.18, y - s * 0.9], fill=ctx.c((130, 90, 60)), outline=ctx.c(OUTLINE), width=SS)
            d.ellipse([x + s * 1.25, y - s * 0.55, x + s * 1.65, y - s * 0.25], fill=ctx.c((200, 160, 120)))
            for ex in (1.2, 1.6):
                d.arc([x + s * ex - s * 0.1, y - s * 0.8, x + s * ex + s * 0.1, y - s * 0.65], 20, 160, fill=ctx.c((40, 30, 20)), width=SS)
            for i in range(3):
                d.text((x + s * 2.0 + i * s * 0.25, y - s * 1.4 - i * s * 0.25), "z", fill=(255, 255, 255, 0))  # invisible: no text in images
        elif p == "table_craft":
            d.rounded_rectangle([W * 0.15, gy + H * 0.02, W * 0.85, gy + H * 0.06], radius=H * 0.01, fill=ctx.c((180, 130, 80)), outline=ctx.c(OUTLINE), width=SS * 2)
            for x in (0.2, 0.8):
                d.rectangle([W * x - W * 0.01, gy + H * 0.06, W * x + W * 0.01, gy + H * 0.2], fill=ctx.c((150, 105, 65)))
            for i, c in enumerate([(230, 80, 80), (80, 140, 230), (250, 210, 80)]):
                d.rectangle([W * (0.25 + i * 0.12), gy + H * 0.0, W * (0.33 + i * 0.12), gy + H * 0.025], fill=ctx.c(c))
        elif p == "flowers":
            flowers(ctx, 30, gy + H * 0.04, H * 0.98)
        else:
            done = False
        # front props --------------------------------------------------------
        if not done:
            done = True
            hold = ctx.anchors.get("hold_hero")
            hold_comp = ctx.anchors.get("hold_comp")
            s = ctx.anchors.get("hero_h", H * 0.4)
            if p in ("basket_star", "basket_star_dim", "basket"):
                x, y = hold or (W * 0.5, gy)
                p_basket(ctx, x, y, s * 0.13, star=("dim" if p == "basket_star_dim" else ("lit" if p == "basket_star" else None)))
            elif p == "star":
                x, y = W * 0.5, gy + H * 0.12
                p_star_glow(ctx, x, y, H * 0.04, dim=True)
            elif p in ("lantern", "lantern_off"):
                x, y = ctx.anchors.get("hand_r_hero") or (W * 0.5, gy)
                p_lantern(ctx, x, y + s * 0.06, s * 0.07, lit=(p == "lantern"))
            elif p == "toy_held":
                x, y = hold or (W * 0.5, gy)
                p_toy(ctx, x, y, s * 0.11)
            elif p == "toy_ground":
                x, y = ctx.anchors.get("swing_ground", (W * 0.72, gy + H * 0.06))
                glow(ctx, x, y - H * 0.03, H * 0.06, (230, 235, 255), 90)
                p_toy(ctx, x, y, H * 0.05)
            elif p in ("kite", "kite_rest", "kite_plain", "kite_crashed", "kite_torn"):
                if p == "kite_crashed":
                    p_kite(ctx, W * 0.55, gy + H * 0.22, H * 0.07, tail=True)
                elif p == "kite_rest":
                    p_kite(ctx, W * 0.5, gy + H * 0.2, H * 0.06)
                else:
                    x, y = hold or (W * 0.5, gy)
                    p_kite(ctx, x, y - s * 0.08, s * 0.18, torn=(p == "kite_torn"), tail=(p != "kite_plain"))
            elif p == "notebook":
                x, y = hold_comp or hold or (W * 0.5, gy)
                d.rectangle([x - s * 0.08, y - s * 0.07, x + s * 0.08, y + s * 0.05], fill=(250, 245, 230, 255), outline=rgba(OUTLINE), width=SS * 2)
                d.line([(x, y - s * 0.07), (x, y + s * 0.05)], fill=rgba(OUTLINE), width=SS)
                p_kite(ctx, x + s * 0.04, y - s * 0.01, s * 0.03, tail=False)
            elif p in ("string",):
                x, y = ctx.anchors.get("hand_r_helper") or (W * 0.6, gy)
                d.ellipse([x - s * 0.05, y - s * 0.05, x + s * 0.05, y + s * 0.05], fill=ctx.c((245, 240, 225)), outline=ctx.c(OUTLINE), width=SS)
            elif p == "sticks":
                for i in range(4):
                    x = W * (0.3 + i * 0.04)
                    d.line([(x, gy + H * 0.2), (x + W * 0.08, gy + H * 0.17)], fill=ctx.c((150, 105, 60)), width=SS * 3)
            elif p == "paper_red_blue":
                for (hp, c) in ((ctx.anchors.get("hand_r_hero"), (230, 70, 70)), (ctx.anchors.get("hand_l_comp"), (60, 120, 220))):
                    if hp:
                        d.rectangle([hp[0] - s * 0.06, hp[1] - s * 0.1, hp[0] + s * 0.06, hp[1] + s * 0.02], fill=ctx.c(c), outline=ctx.c(OUTLINE), width=SS)
            elif p == "kids":
                for i, c in enumerate([(240, 160, 60), (150, 110, 220)]):
                    x = W * (0.08 + i * 0.84)
                    d.ellipse([x - W * 0.025, gy + H * 0.06, x + W * 0.025, gy + H * 0.12], fill=ctx.c((235, 200, 170)), outline=ctx.c(OUTLINE), width=SS)
                    d.rounded_rectangle([x - W * 0.03, gy + H * 0.12, x + W * 0.03, gy + H * 0.21], radius=W * 0.01, fill=ctx.c(c), outline=ctx.c(OUTLINE), width=SS)
            elif p == "ribbon":
                x, y = ctx.anchors.get("chest_hero") or (W * 0.4, gy)
                d.ellipse([x - s * 0.04, y - s * 0.04, x + s * 0.04, y + s * 0.04], fill=ctx.c((60, 110, 220)), outline=ctx.c(OUTLINE), width=SS)
                d.polygon([(x - s * 0.03, y), (x - s * 0.05, y + s * 0.1), (x, y + s * 0.06), (x + s * 0.05, y + s * 0.1), (x + s * 0.03, y)], fill=ctx.c((60, 110, 220)))
            elif p == "fireflies":
                for _ in range(26):
                    x, y = rng.random() * W, gy - rng.random() * H * 0.45 + H * 0.1
                    glow(ctx, x, y, H * 0.018, (230, 255, 140), 200)
                    d.ellipse([x - SS * 2, y - SS * 2, x + SS * 2, y + SS * 2], fill=(250, 255, 200, 255))
            elif p == "notes":
                for i in range(5):
                    x, y = W * (0.3 + rng.random() * 0.4), H * (0.15 + rng.random() * 0.25)
                    r = H * 0.012
                    d.ellipse([x - r * 1.3, y - r, x + r * 1.3, y + r], fill=ctx.c((255, 230, 140)))
                    d.line([(x + r * 1.2, y), (x + r * 1.2, y - r * 4)], fill=ctx.c((255, 230, 140)), width=SS * 2)
            elif p == "hedgehog_wild":
                p_hedgehog(ctx, W * 0.66, gy + H * 0.2, H * 0.05)
            elif p == "frog":
                x, y = ctx.anchors.get("stream_y") and (W * 0.75, ctx.anchors["stream_y"]) or (W * 0.75, gy + H * 0.22)
                d.ellipse([x - H * 0.05, y - H * 0.01, x + H * 0.05, y + H * 0.03], fill=ctx.c((165, 160, 150)), outline=ctx.c(OUTLINE), width=SS)
                p_frog(ctx, x, y, H * 0.035)
            elif p == "ducks":
                p_duck(ctx, W * 0.72, gy + H * 0.22, H * 0.05)
                for i in range(3):
                    p_duck(ctx, W * (0.8 + i * 0.05), gy + H * 0.25, H * 0.025, baby=True)
            elif p == "trash":
                for i in range(6):
                    x, y = W * (0.55 + rng.random() * 0.4), gy + H * (0.12 + rng.random() * 0.15)
                    d.rectangle([x, y, x + W * 0.02, y + H * 0.03], fill=ctx.c(rng.choice([(200, 60, 60), (200, 200, 210), (240, 240, 240)])), outline=ctx.c(OUTLINE), width=SS)
            elif p == "watering_can":
                x, y = ctx.anchors.get("hand_r_hero") or hold or (W * 0.5, gy)
                c = (80, 170, 100)
                d.rounded_rectangle([x - s * 0.07, y - s * 0.02, x + s * 0.07, y + s * 0.11], radius=s * 0.02, fill=ctx.c(c), outline=ctx.c(OUTLINE), width=SS * 2)
                d.line([(x + s * 0.07, y + s * 0.06), (x + s * 0.16, y - s * 0.02)], fill=ctx.c(c), width=SS * 4)
                if "pour" in str(ctx.anchors.get("pose_hero")):
                    for i in range(6):
                        d.line([(x + s * 0.16 + i * SS * 2, y), (x + s * 0.2 + i * SS * 2, y + s * 0.25)], fill=(150, 200, 255, 200), width=SS)
            elif p == "seeds":
                for _ in range(8):
                    x, y = W * (0.2 + rng.random() * 0.6), gy + H * (0.18 + rng.random() * 0.1)
                    d.line([(x, y), (x, y - H * 0.03)], fill=ctx.c((90, 170, 80)), width=SS * 2)
                    d.ellipse([x, y - H * 0.04, x + H * 0.015, y - H * 0.025], fill=ctx.c((110, 190, 90)))
            elif p == "splash":
                x, y = W * 0.6, gy + H * 0.12
                for i in range(14):
                    a = math.pi * (0.1 + 0.8 * i / 13)
                    d.ellipse([x + math.cos(a) * H * 0.15 - H * 0.015, y - math.sin(a) * H * 0.15 - H * 0.015, x + math.cos(a) * H * 0.15 + H * 0.015, y - math.sin(a) * H * 0.15 + H * 0.015],
                              fill=(170, 215, 250, 230))
            elif p == "strawberries":
                for _ in range(7):
                    x, y = W * (0.1 + rng.random() * 0.8), gy + H * (0.15 + rng.random() * 0.15)
                    d.polygon([(x - H * 0.012, y), (x + H * 0.012, y), (x, y + H * 0.02)], fill=ctx.c((230, 50, 60)))
                    d.ellipse([x - H * 0.008, y - H * 0.006, x + H * 0.008, y + H * 0.002], fill=ctx.c((90, 170, 80)))
            elif p == "leaves":
                y = ctx.anchors.get("stream_y", gy + H * 0.3)
                for i in range(4):
                    x = W * (0.15 + i * 0.2)
                    d.ellipse([x - H * 0.018, y + H * 0.01, x + H * 0.018, y + H * 0.03], fill=ctx.c((220, 140, 50)), outline=ctx.c(OUTLINE), width=SS)
            elif p == "cups":
                x, y = ctx.anchors.get("hand_r_hero") or (W * 0.45, gy)
                d.rounded_rectangle([x - s * 0.04, y - s * 0.05, x + s * 0.04, y + s * 0.04], radius=s * 0.01, fill=ctx.c((250, 250, 250)), outline=ctx.c(OUTLINE), width=SS)
            elif p == "fence":
                pass
            else:
                done = False
        if done:
            ctx.rendered.append(p)


# ----------------------------------------------------------------- composition
SHOT_SCALE = {"wide": 0.34, "medium": 0.46, "close": 0.6}
SLOTS = {1: [0.42], 2: [0.36, 0.6], 3: [0.26, 0.48, 0.7], 4: [0.2, 0.4, 0.6, 0.8]}


SPECIES_HEIGHT = {"human": 1.0, "human_old": 1.22, "owl": 0.6}


def character_height(spec: dict, base: float) -> float:
    """Species proportions x the user's size override (Character Bible `appearance.scale`)."""
    app = spec.get("appearance", {}) or {}
    factor = SPECIES_HEIGHT.get(app.get("species", "human"), 0.68)
    try:
        scale = max(0.5, min(1.8, float(app.get("scale", 1.0) or 1.0)))
    except (TypeError, ValueError):
        scale = 1.0
    return base * factor * scale


def finish_style(img: Image.Image, style: str, seed: int) -> Image.Image:
    rng = random.Random(seed)
    W, H = img.size
    rgb = img.convert("RGB")
    if style == "watercolor":
        soft = rgb.filter(ImageFilter.GaussianBlur(1.6))
        edges = ImageOps.invert(rgb.convert("L").filter(ImageFilter.FIND_EDGES)).filter(ImageFilter.GaussianBlur(1.2))
        rgb = ImageChops.multiply(soft, Image.merge("RGB", [edges] * 3).point(lambda v: 160 + v * 95 // 255))
        rgb = Image.blend(rgb, Image.new("RGB", rgb.size, (255, 250, 240)), 0.08)
        rgb = ImageChops.multiply(rgb, paper_texture(W, H, rng, 18))
    elif style == "crayon":
        rgb = ImageChops.multiply(rgb, hatch_texture(W, H, rng))
        rgb = ImageChops.multiply(rgb, paper_texture(W, H, rng, 26))
    elif style == "paper_cutout":
        rgb = ImageChops.multiply(rgb, paper_texture(W, H, rng, 22))
    elif style == "gouache":
        from PIL import ImageEnhance
        rgb = ImageEnhance.Color(rgb).enhance(1.15)
        rgb = ImageChops.multiply(rgb.filter(ImageFilter.SMOOTH_MORE), paper_texture(W, H, rng, 14))
    # gentle vignette for every style except flat vector
    if style != "flat_vector":
        vig = Image.new("L", (W, H), 0)
        ImageDraw.Draw(vig).ellipse([-W * 0.15, -H * 0.2, W * 1.15, H * 1.2], fill=255)
        vig = vig.filter(ImageFilter.GaussianBlur(min(W, H) * 0.08)).point(lambda v: 215 + v * 40 // 255)
        rgb = ImageChops.multiply(rgb, Image.merge("RGB", [vig] * 3))
    return rgb


def paper_texture(W, H, rng: random.Random, strength: int) -> Image.Image:
    small = Image.effect_noise((max(8, W // 3), max(8, H // 3)), strength).resize((W, H), Image.BILINEAR)
    return Image.merge("RGB", [small.point(lambda v: 255 - strength + v * strength // 255)] * 3)


def hatch_texture(W, H, rng: random.Random) -> Image.Image:
    t = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(t)
    for i in range(-H, W, 6):
        d.line([(i, 0), (i + H, H)], fill=225 + rng.randint(0, 25), width=1)
    return Image.merge("RGB", [t] * 3)


def render_scene(scene: dict, characters: dict[str, dict], width: int, height: int, style: str = "watercolor",
                 seed: int = 0, variant: int = 0) -> tuple[Image.Image, dict]:
    W, H = width * SS, height * SS
    rng = random.Random(seed * 1000 + variant)
    time = scene.get("time", "day")
    loc = scene.get("location", "meadow")
    img = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    d = ImageDraw.Draw(img, "RGBA")
    ground_y = H * (0.6 if loc != "bedroom" else 0.62) + (rng.random() - 0.5) * H * 0.04
    ctx = Ctx(img, d, W, H, time, rng, ground_y)
    background(ctx, loc, scene.get("weather", ""))
    props = list(scene.get("props", []))
    draw_props(ctx, props, "back")
    d = ctx.d = ImageDraw.Draw(img, "RGBA")  # alpha_composite in glow() keeps the same image object

    ids = [c for c in scene.get("characters", []) if c in characters]
    shot = scene.get("shot", "medium")
    base_h = H * SHOT_SCALE.get(shot, 0.46)
    slots = SLOTS.get(len(ids), SLOTS[4])[:]
    flip_layout = (variant % 2 == 1)
    if flip_layout:
        slots = [1 - s for s in slots][::-1]
        ids = ids[::-1]
    jitter = (rng.random() - 0.5) * 0.06
    boxes, expected = {}, {}
    in_bed = "bed" in props and any(scene.get("poses", {}).get(c) == "sleep" for c in ids)
    for cid, sx in zip(ids, slots):
        spec = characters[cid]
        pose = scene.get("poses", {}).get(cid, "stand")
        expr = scene.get("expressions", {}).get(cid) or ("happy" if scene.get("mood") in ("happy", "joyful", "content") else "happy")
        h = character_height(spec, base_h)
        foot = ground_y + H * (0.12 if shot != "close" else 0.2) + (H * 0.02 if cid != "hero" else 0)
        x = W * (sx + jitter)
        if in_bed and cid == "hero":
            # sleeping: draw the figure on its own layer, keep only the head and lay it on the pillow
            px, py = ctx.anchors.get("pillow", (W * 0.2, ground_y))
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            lfig = Figure(ImageDraw.Draw(layer, "RGBA"), W / 2, H * 0.95, h * 0.95, time)
            draw_character(lfig, spec, "sleep", "sleep")
            bx0, by0, bx1, by1 = [int(v) for v in lfig.bbox]
            head_h = int((by1 - by0) * 0.40)
            head = layer.crop((bx0, by0, bx1, by0 + head_h))
            head = head.rotate(-14, expand=True, resample=Image.BICUBIC)
            hx0, hy0 = int(px - head.width * 0.45), int(py - head.height * 0.78)
            img.alpha_composite(head, (max(0, hx0), max(0, hy0)))
            d.rounded_rectangle([px + W * 0.03, py - H * 0.02, W * 0.62, py + H * 0.12], radius=H * 0.04,
                                fill=rgba((120, 170, 230) if time == "day" else (96, 120, 180)), outline=rgba(OUTLINE), width=SS * 2)
            d.rounded_rectangle([px - W * 0.03, py + H * 0.02, W * 0.62, py + H * 0.12], radius=H * 0.04,
                                fill=rgba((120, 170, 230) if time == "day" else (96, 120, 180)), outline=rgba(OUTLINE), width=SS * 2)
            boxes[cid] = [int(max(0, hx0) / SS), int(max(0, hy0) / SS), int((hx0 + head.width) / SS), int(min(H, py + H * 0.12) / SS)]
            expected[cid] = spec.get("palette", [])[:2]  # only skin + hair are visible
            ctx.anchors["hold_hero"] = (px + W * 0.08, py + H * 0.02)
            continue
        if pose == "sit" and in_bed:
            foot = ground_y + H * 0.02
        fig = Figure(d, x, foot, h, time, flip=False)
        anchors = draw_character(fig, spec, pose, expr)
        boxes[cid] = [int(max(0, v) / SS) for v in fig.bbox]
        expected[cid] = spec.get("palette", [])
        ctx.anchors[f"hold_{cid}"] = anchors.get("hold")
        ctx.anchors[f"hand_r_{cid}"] = anchors.get("hand_r")
        ctx.anchors[f"hand_l_{cid}"] = anchors.get("hand_l")
        ctx.anchors[f"pose_{cid}"] = pose
        if cid == "hero":
            ctx.anchors["hero_h"] = h
            hx, hy = anchors.get("head")
            ctx.anchors["chest_hero"] = (hx + h * 0.05, hy + h * 0.35)
            if pose == "pull" and ctx.anchors.get("kite_string"):
                d.line([anchors["hand_r"], ctx.anchors["kite_string"]], fill=(250, 250, 250, 220), width=SS * 2)
    draw_props(ctx, props, "front")
    out = img.resize((width, height), Image.LANCZOS)
    out = finish_style(out, style, seed + variant)
    meta = {"renderer": "procedural-v1", "style": style, "location": loc, "time": time, "variant": variant,
            "character_boxes": boxes, "expected_palettes": expected,
            "props_rendered": ctx.rendered, "props_skipped": [p for p in props if p not in ctx.rendered]}
    return out, meta


def render_reference_sheet(spec: dict, width: int = 1024, height: int = 768, style: str = "watercolor",
                           mode: str = "multi_view") -> tuple[Image.Image, dict]:
    """Approved character reference. multi_view: three full-body poses on top and an
    expression row (happy, sad, surprised, laughing, sleepy) below; single: three poses."""
    W, H = width * SS, height * SS
    img = Image.new("RGBA", (W, H), (250, 246, 238, 255))
    d = ImageDraw.Draw(img, "RGBA")
    boxes, expressions = [], []
    norm = character_height({"appearance": {**spec.get("appearance", {}), "scale": 1.0}}, 1.0)  # sheet ignores size override
    if mode == "multi_view":
        d.ellipse([W * 0.05, H * 0.5, W * 0.95, H * 0.58], fill=(230, 222, 210, 255))
        d.line([(W * 0.04, H * 0.62), (W * 0.96, H * 0.62)], fill=(215, 205, 190, 255), width=SS * 2)
        h = H * 0.44 * norm / max(norm, 1.0)
        for x, pose, expr in ((0.2, "stand", "happy"), (0.5, "wave", "laugh"), (0.8, "point", "surprised")):
            fig = Figure(d, W * x, H * 0.55, h * (0.92 if x != 0.5 else 1.0), "day")
            draw_character(fig, spec, pose, expr)
            boxes.append([int(v / SS) for v in fig.bbox])
        hs = H * 0.3 * norm / max(norm, 1.0)
        for i, expr in enumerate(("happy", "sad", "surprised", "laugh", "sleep")):
            fig = Figure(d, W * (0.12 + i * 0.19), H * 0.97, hs, "day")
            draw_character(fig, spec, "stand", expr)
            expressions.append({"expression": expr, "box": [int(v / SS) for v in fig.bbox]})
    else:
        d.ellipse([W * 0.05, H * 0.82, W * 0.95, H * 0.95], fill=(230, 222, 210, 255))
        h = H * 0.62 * norm / max(norm, 1.0)
        for x, pose, expr in ((0.2, "stand", "happy"), (0.5, "wave", "laugh"), (0.8, "point", "surprised")):
            fig = Figure(d, W * x, H * 0.88, h * (0.92 if x != 0.5 else 1.0), "day")
            draw_character(fig, spec, pose, expr)
            boxes.append([int(v / SS) for v in fig.bbox])
    out = img.resize((width, height), Image.LANCZOS)
    out = finish_style(out, style if style != "watercolor" else "flat_vector", 1)
    return out, {"renderer": "procedural-v1", "reference_boxes": boxes, "expression_boxes": expressions, "mode": mode,
                 "palette": spec.get("palette", [])}


def render_cast_lineup(specs: list[dict], width: int = 1280, height: int = 720, style: str = "flat_vector") -> tuple[Image.Image, dict]:
    """All characters side by side on one ground line at their true relative sizes
    (species proportions x size override) with height guides: the scale reference
    used by the procedural renderer and sent to AI image models."""
    W, H = width * SS, height * SS
    img = Image.new("RGBA", (W, H), (248, 245, 238, 255))
    d = ImageDraw.Draw(img, "RGBA")
    ground = H * 0.9
    heights = [character_height(sp, 1.0) for sp in specs]
    tallest = max(heights) if heights else 1.0
    unit = H * 0.72 / tallest
    for k in range(1, 5):  # guides at 25 % steps of the tallest character
        y = ground - unit * tallest * k / 4
        d.line([(W * 0.03, y), (W * 0.97, y)], fill=(222, 214, 200, 255), width=SS)
    d.line([(W * 0.03, ground), (W * 0.97, ground)], fill=(150, 130, 110, 255), width=SS * 3)
    n = max(1, len(specs))
    boxes = {}
    for i, (sp, hgt) in enumerate(zip(specs, heights)):
        fig = Figure(d, W * (i + 0.5) / n, ground, unit * hgt, "day")
        draw_character(fig, sp, "stand", "happy")
        boxes[sp.get("id", str(i))] = [int(v / SS) for v in fig.bbox]
    out = img.resize((width, height), Image.LANCZOS)
    out = finish_style(out, style, 2)
    rel = {sp.get("id", str(i)): round(h / tallest, 3) for i, (sp, h) in enumerate(zip(specs, heights))}
    return out, {"renderer": "procedural-v1", "boxes": boxes, "relative_heights": rel}
