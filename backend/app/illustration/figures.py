"""Character drawing. Every character is drawn from its Character Bible entry,
so faces, colours, hair and outfit are identical on every page."""
from __future__ import annotations

import math

from PIL import ImageDraw

from .palette import hex2rgb, rgba, shade, timed

OUTLINE = (60, 42, 35)


class Figure:
    """Draws one character in a local unit system: x right, y up, ground = 0, ~100 units tall."""

    def __init__(self, d: ImageDraw.ImageDraw, cx: float, ground: float, height_px: float, time: str = "day", flip: bool = False):
        self.d = d
        self.cx = cx
        self.ground = ground
        self.u = height_px / 100.0
        self.time = time
        self.flip = flip
        self.lw = max(1, int(1.6 * self.u))
        self.bbox = [1e9, 1e9, -1e9, -1e9]

    # coordinate helpers -------------------------------------------------
    def P(self, x, y):
        if self.flip:
            x = -x
        px, py = self.cx + x * self.u, self.ground - y * self.u
        b = self.bbox
        b[0], b[1], b[2], b[3] = min(b[0], px), min(b[1], py), max(b[2], px), max(b[3], py)
        return (px, py)

    def box(self, x0, y0, x1, y1):
        a, b = self.P(x0, y0), self.P(x1, y1)
        return [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]

    def col(self, c):
        return rgba(timed(c, self.time, 0.55) if self.time != "day" else c)

    def ellipse(self, x0, y0, x1, y1, fill, outline=True):
        self.d.ellipse(self.box(x0, y0, x1, y1), fill=self.col(fill), outline=rgba(OUTLINE) if outline else None,
                       width=self.lw if outline else 0)

    def circle(self, x, y, r, fill, outline=True):
        self.ellipse(x - r, y - r, x + r, y + r, fill, outline)

    def rrect(self, x0, y0, x1, y1, r, fill, outline=True):
        self.d.rounded_rectangle(self.box(x0, y0, x1, y1), radius=r * self.u, fill=self.col(fill),
                                 outline=rgba(OUTLINE) if outline else None, width=self.lw if outline else 0)

    def poly(self, pts, fill, outline=True):
        self.d.polygon([self.P(x, y) for x, y in pts], fill=self.col(fill), outline=rgba(OUTLINE) if outline else None,
                       width=self.lw if outline else 0)

    def line(self, pts, fill, w):
        self.d.line([self.P(x, y) for x, y in pts], fill=self.col(fill), width=max(1, int(w * self.u)), joint="curve")

    def limb(self, a, b, color, w):
        # outlined thick limb with round caps
        self.line([a, b], OUTLINE, w + 1.6)
        self.circle(a[0], a[1], (w + 1.6) / 2, OUTLINE, outline=False)
        self.circle(b[0], b[1], (w + 1.6) / 2, OUTLINE, outline=False)
        self.line([a, b], color, w)
        self.circle(a[0], a[1], w / 2, color, outline=False)
        self.circle(b[0], b[1], w / 2, color, outline=False)

    def arc(self, x0, y0, x1, y1, start, end, fill, w):
        bx = self.box(x0, y0, x1, y1)
        if self.flip:
            start, end = 180 - end, 180 - start
        self.d.arc(bx, start, end, fill=self.col(fill), width=max(1, int(w * self.u)))

    def shadow(self, w=26):
        self.d.ellipse(self.box(-w, -3, w, 3), fill=(0, 0, 0, 45))

    # faces ----------------------------------------------------------------
    def face(self, cy, expr: str, eye_dx=8.0, eye_color=(45, 32, 25), scale=1.0, look_up=False):
        s = scale
        ex, ey = eye_dx * s, cy
        dark = eye_color
        if expr in ("sleep", "calm"):
            for sx in (-1, 1):
                self.arc(sx * ex - 3.5 * s, ey - 3 * s, sx * ex + 3.5 * s, ey + 2 * s, 20, 160, dark, 1.4 * s)
        else:
            big = 1.25 if expr in ("surprised", "scared") else 1.0
            for sx in (-1, 1):
                self.ellipse(sx * ex - 3.0 * s * big, ey - 4.0 * s * big, sx * ex + 3.0 * s * big, ey + 4.0 * s * big, dark, outline=False)
                hy = ey + (2.4 if look_up else 1.4) * s
                self.circle(sx * ex + 1.0 * s, hy, 1.2 * s, (255, 255, 255), outline=False)
        # brows
        by = ey + 7 * s
        if expr in ("sad", "worried", "scared"):
            for sx in (-1, 1):
                self.line([(sx * (ex - 3.5 * s), by + 1.5 * s), (sx * (ex + 3.5 * s), by - 1.0 * s)], dark, 1.3 * s)
        elif expr in ("determined", "grumpy"):
            for sx in (-1, 1):
                self.line([(sx * (ex - 3.5 * s), by - 1.5 * s), (sx * (ex + 3.5 * s), by + 1.0 * s)], dark, 1.5 * s)
        elif expr in ("surprised",):
            for sx in (-1, 1):
                self.arc(sx * ex - 4 * s, by - 1 * s, sx * ex + 4 * s, by + 3 * s, 200, 340, dark, 1.2 * s)
        # cheeks
        blush = (255, 140, 140) if expr != "shy" else (255, 110, 120)
        for sx in (-1, 1):
            self.d.ellipse(self.box(sx * (ex + 4 * s) - 3.2 * s, ey - 7.5 * s, sx * (ex + 4 * s) + 3.2 * s, ey - 4.5 * s),
                           fill=rgba(blush, 120 if expr != "shy" else 190))
        # mouth
        my = ey - 9 * s
        if expr in ("happy", "kind", "proud", "content", "calm", "shy", "hopeful", "joyful"):
            w = 4.5 if expr != "shy" else 3
            self.arc(-w * s, my - 2 * s, w * s, my + 3.5 * s, 20, 160, dark, 1.4 * s)
        elif expr in ("laugh", "excited"):
            self.d.chord(self.box(-5 * s, my - 4 * s, 5 * s, my + 3 * s), 0, 180, fill=rgba((150, 50, 60)),
                         outline=rgba(dark), width=max(1, int(1.2 * s * self.u)))
        elif expr in ("sad", "grumpy"):
            self.arc(-4 * s, my - 3.5 * s, 4 * s, my + 2 * s, 200, 340, dark, 1.4 * s)
        elif expr in ("surprised", "scared"):
            self.ellipse(-2.2 * s, my - 2.6 * s, 2.2 * s, my + 2.6 * s, (120, 45, 55), outline=False)
        elif expr in ("worried",):
            self.line([(-3 * s, my), (-1 * s, my + 1 * s), (1 * s, my), (3 * s, my + 1 * s)], dark, 1.2 * s)
        else:  # determined / neutral
            self.line([(-3.5 * s, my), (3.5 * s, my)], dark, 1.4 * s)


ARM_POSES = {
    "stand": ((-23, 36), (23, 36)), "wave": ((-23, 36), (27, 93)), "point": ((-23, 36), (40, 64)),
    "hold": ((-9, 45), (9, 45)), "walk_hold": ((-9, 45), (9, 45)), "lift": ((-20, 112), (20, 112)),
    "jump": ((-30, 90), (30, 90)), "walk": ((-25, 40), (21, 33)), "run": ((-28, 50), (24, 30)),
    "sit": ((-22, 38), (22, 38)), "kneel": ((-9, 44), (9, 44)), "hug": ((7, 52), (-7, 52)),
    "pull": ((18, 62), (28, 70)), "give": ((-23, 36), (38, 52)), "pour": ((-23, 36), (34, 46)),
    "reach": ((-34, 58), (34, 58)), "tiptoe": ((-25, 46), (25, 46)), "look_up": ((-23, 36), (23, 36)),
    "sleep": ((-20, 36), (20, 36)),
}
LIFT = {"jump": 12, "tiptoe": 3}
LOWER = {"sit": 24, "kneel": 14}


def draw_human(fig: Figure, spec: dict, pose: str = "stand", expr: str = "happy", elder: bool = False) -> dict:
    app, out = spec.get("appearance", {}), spec.get("outfit", {})
    skin = hex2rgb(app.get("skin"), (240, 200, 170))
    hair = hex2rgb(app.get("hair_color"), (60, 40, 30))
    top = hex2rgb(out.get("top_color"), (80, 140, 220))
    bottom = hex2rgb(out.get("bottom_color"), (60, 80, 140)) if out.get("bottom") else top
    shoes = hex2rgb(out.get("shoes_color"), (90, 60, 40))
    acc, acc_c = out.get("accessory", ""), hex2rgb(out.get("accessory_color"), (230, 80, 80))
    style = app.get("hair_style", "short")
    top_kind = out.get("top", "t-shirt")
    lift = LIFT.get(pose, 0) - LOWER.get(pose, 0)
    hands = ARM_POSES.get(pose, ARM_POSES["stand"])
    fig.shadow(24 if not elder else 22)

    def Y(y):
        return y + lift

    # backpack behind
    if acc == "backpack":
        fig.rrect(-21, Y(34), 21, Y(62), 6, acc_c)
    # long hair behind head
    if style in ("bob", "braids", "ponytail") or (elder and style == "bun"):
        if style == "bob":
            fig.rrect(-24, Y(64), 24, Y(92), 10, hair)
        if style == "braids":
            for sx in (-1, 1):
                fig.ellipse(sx * 22 - 5, Y(52), sx * 22 + 5, Y(80), hair)
                fig.circle(sx * 22, Y(51), 3.5, acc_c if acc == "bow" else hair)
        if style == "ponytail":
            fig.ellipse(18, Y(62), 32, Y(92), hair)
    # legs
    if pose == "sit":
        for sx in (-1, 1):
            fig.limb((sx * 7, Y(30)), (sx * 9, Y(14)), bottom if out.get("bottom") in ("trousers",) else skin, 8)
            fig.ellipse(sx * 9 - 6, Y(8), sx * 9 + 6, Y(16), shoes)
    elif pose == "kneel":
        for sx in (-1, 1):
            fig.limb((sx * 7, Y(30)), (sx * 9, Y(16)), skin if out.get("bottom") != "trousers" else bottom, 8)
            fig.ellipse(sx * 10 - 7, Y(12), sx * 10 + 7, Y(18), shoes)
    else:
        spread = {"walk": 7, "run": 11, "walk_hold": 6, "tiptoe": 4, "jump": 4}.get(pose, 0)
        for sx in (-1, 1):
            fx = sx * (8 + spread)
            leg_col = bottom if out.get("bottom") == "trousers" or top_kind == "overalls" else skin
            if elder:
                leg_col = bottom
            fig.limb((sx * 7, Y(28)), (fx, Y(6)), leg_col, 8)
            fig.ellipse(fx - 7, Y(0), fx + 7, Y(7), shoes)
    # torso / outfit
    torso_top = 63 if not elder else 66
    if top_kind == "dress":
        fig.poly([(-14, Y(torso_top)), (14, Y(torso_top)), (23, Y(24)), (-23, Y(24))], top)
    elif top_kind == "overalls":
        fig.rrect(-16, Y(36), 16, Y(torso_top), 7, top)
        fig.rrect(-17, Y(24), 17, Y(46), 6, bottom)
        fig.rrect(-12, Y(44), 12, Y(56), 3, bottom)
        for sx in (-1, 1):
            fig.line([(sx * 9, Y(56)), (sx * 11, Y(torso_top - 1))], shade(bottom, 0.8), 2.2)
    else:
        if out.get("bottom") in ("shorts", "skirt") and not elder:
            if out.get("bottom") == "skirt":
                fig.poly([(-15, Y(36)), (15, Y(36)), (21, Y(22)), (-21, Y(22))], bottom)
            else:
                fig.rrect(-16, Y(22), 16, Y(38), 5, bottom)
        else:
            fig.rrect(-16, Y(24), 16, Y(38), 5, bottom)
        fig.rrect(-17, Y(34), 17, Y(torso_top), 8, top)
        if top_kind == "hoodie":
            fig.arc(-10, Y(torso_top - 14), 10, Y(torso_top + 2), 20, 160, shade(top, 0.75), 2)
        if top_kind == "sweater" or elder:
            for yy in (40, 48):
                fig.line([(-15, Y(yy)), (15, Y(yy))], shade(top, 0.85), 1.4)
    # arms
    sleeve = top if top_kind not in ("dress", "overalls") else skin
    if elder:
        sleeve = top
    for sx, (hx, hy) in zip((-1, 1), hands):
        shoulder = (sx * 15, Y(torso_top - 5))
        fig.limb(shoulder, (hx, Y(hy)), sleeve, 7.5)
        fig.circle(hx, Y(hy), 4.4, skin)
    # neck + head
    head_cy = Y(82 if not elder else 86)
    for sx in (-1, 1):  # ears, half hidden behind the head
        fig.circle(sx * 19.5, head_cy - 1, 4.2, skin)
    fig.circle(0, head_cy, 21, skin)
    # hair cap
    if elder:
        fig.d.chord(fig.box(-22, head_cy - 2, 22, head_cy + 23), 180 if not fig.flip else 180, 360, fill=fig.col(hair),
                    outline=rgba(OUTLINE), width=fig.lw)
        if spec.get("pronoun") == "he" or style == "beard":
            fig.poly([(-15, head_cy - 8), (15, head_cy - 8), (9, head_cy - 22), (0, head_cy - 26), (-9, head_cy - 22)], hair)
        else:
            fig.circle(0, head_cy + 24, 8, hair)
    else:
        fig.d.chord(fig.box(-22.5, head_cy - 6, 22.5, head_cy + 23), 180, 360, fill=fig.col(hair), outline=rgba(OUTLINE), width=fig.lw)
        if style == "curly":
            for i in range(9):
                a = math.pi * i / 8
                fig.circle(math.cos(a) * 20, head_cy + 4 + math.sin(a) * 18, 6, hair, outline=False)
        elif style == "spiky":
            for i in range(5):
                x = -16 + i * 8
                fig.poly([(x - 5, head_cy + 16), (x + 5, head_cy + 16), (x + 1, head_cy + 27)], hair, outline=False)
        elif style == "bun":
            fig.circle(0, head_cy + 25, 8, hair)
        # fringe
        fig.poly([(-20, head_cy + 6), (-6, head_cy + 13), (4, head_cy + 9), (20, head_cy + 7), (20, head_cy + 16), (-20, head_cy + 16)],
                 hair, outline=False)
    look_up = pose == "look_up"
    fig.face(head_cy + 0, "sleep" if pose == "sleep" else expr, look_up=look_up)
    # accessories
    if acc == "scarf":
        fig.rrect(-15, Y(torso_top - 4), 15, Y(torso_top + 3), 3, acc_c)
        fig.rrect(6, Y(torso_top - 18), 13, Y(torso_top - 2), 3, acc_c)
    elif acc == "hat":
        fig.d.chord(fig.box(-23, head_cy + 4, 23, head_cy + 30), 180, 360, fill=fig.col(acc_c), outline=rgba(OUTLINE), width=fig.lw)
        fig.circle(0, head_cy + 29, 4.5, shade(acc_c, 1.4))
    elif acc == "bow" and style != "braids":
        fig.poly([(8, head_cy + 18), (20, head_cy + 25), (20, head_cy + 11)], acc_c)
        fig.poly([(8, head_cy + 18), (-4, head_cy + 25), (-4, head_cy + 11)], acc_c)
    if acc == "glasses" or elder:
        gc = (60, 60, 60) if elder else acc_c
        for sx in (-1, 1):
            fig.d.ellipse(fig.box(sx * 8 - 5.5, head_cy - 5, sx * 8 + 5.5, head_cy + 5), outline=rgba(gc), width=max(1, int(1.4 * fig.u)))
        fig.line([(-2.5, head_cy + 1), (2.5, head_cy + 1)], gc, 1.2)
    # where the hands are (for held props)
    (lx, ly), (rx, ry) = hands
    hold_y = (ly + ry) / 2 + (10 if pose == "lift" else 0)
    return {"hand_l": fig.P(lx, Y(ly)), "hand_r": fig.P(rx, Y(ry)), "hold": fig.P((lx + rx) / 2, Y(hold_y)),
            "head": fig.P(0, head_cy), "top": fig.P(0, head_cy + 26)}


ANIMAL_EARS = {"fox": "pointed", "cat": "pointed", "rabbit": "long", "puppy": "floppy", "squirrel": "tufted",
               "hedgehog": "small", "bear": "round", "owl": "tufts"}


def draw_animal(fig: Figure, spec: dict, pose: str = "stand", expr: str = "happy") -> dict:
    app, out = spec.get("appearance", {}), spec.get("outfit", {})
    sp = app.get("species", "fox")
    fur = hex2rgb(app.get("skin"), (220, 120, 60))
    light = shade(fur, 1.55) if sp not in ("rabbit",) else (255, 250, 245)
    dark = shade(fur, 0.6)
    acc_c = hex2rgb(out.get("accessory_color"), (230, 80, 80))
    lift = {"jump": 14}.get(pose, 0)
    asleep = pose == "sleep" or expr == "sleep"
    fig.shadow(26)
    Y = lambda y: y + lift  # noqa: E731

    if sp == "owl":
        for sx in (-1, 1):
            fig.ellipse(sx * 22 - 9, Y(18), sx * 22 + 9, Y(62), dark)
        fig.ellipse(-26, Y(2), 26, Y(84), fur)
        fig.ellipse(-17, Y(8), 17, Y(52), light, outline=False)
        for i in range(3):
            for j in range(3):
                fig.arc(-12 + j * 9, Y(14 + i * 11), -6 + j * 9, Y(20 + i * 11), 20, 160, dark, 1.0)
        for sx in (-1, 1):
            fig.poly([(sx * 12, Y(80)), (sx * 24, Y(96)), (sx * 22, Y(76))], fur)
            fig.circle(sx * 10, Y(64), 10, (250, 240, 220))
            fig.circle(sx * 10, Y(64), 5.5 if not asleep else 0.1, (40, 30, 20), outline=False)
            if not asleep:
                fig.circle(sx * 10 + 1.5, Y(66), 1.6, (255, 255, 255), outline=False)
            else:
                fig.arc(sx * 10 - 5, Y(61), sx * 10 + 5, Y(67), 20, 160, (40, 30, 20), 1.3)
        fig.poly([(-4, Y(58)), (4, Y(58)), (0, Y(50))], (240, 170, 60))
        for sx in (-1, 1):
            fig.line([(sx * 8, Y(2)), (sx * 8, Y(-1))], (240, 170, 60), 3)
        if out.get("accessory") == "glasses":
            for sx in (-1, 1):
                fig.d.ellipse(fig.box(sx * 10 - 11, Y(53), sx * 10 + 11, Y(75)), outline=rgba((60, 60, 60)), width=max(1, int(1.3 * fig.u)))
        return {"hold": fig.P(0, Y(40)), "head": fig.P(0, Y(64)), "top": fig.P(0, Y(96)), "hand_r": fig.P(26, Y(40)), "hand_l": fig.P(-26, Y(40))}

    # tail (behind)
    if sp == "fox":
        fig.poly([(18, Y(10)), (44, Y(30)), (50, Y(52)), (36, Y(46)), (22, Y(26))], fur)
        fig.poly([(44, Y(40)), (50, Y(52)), (40, Y(48))], (255, 252, 245))
    elif sp == "squirrel":
        fig.ellipse(16, Y(18), 46, Y(78), fur)
        fig.ellipse(22, Y(30), 40, Y(70), shade(fur, 1.2), outline=False)
    elif sp == "cat":
        fig.line([(18, Y(8)), (36, Y(14)), (42, Y(34)), (36, Y(46))], OUTLINE, 7.5)
        fig.line([(18, Y(8)), (36, Y(14)), (42, Y(34)), (36, Y(46))], fur, 5.5)
    elif sp == "puppy":
        fig.line([(18, Y(14)), (32, Y(26)), (36, Y(38))], OUTLINE, 6.5)
        fig.line([(18, Y(14)), (32, Y(26)), (36, Y(38))], fur, 4.5)
    elif sp == "rabbit":
        fig.circle(22, Y(12), 7, (255, 255, 255))
    # body
    if asleep and pose == "sleep":
        fig.ellipse(-30, Y(0), 30, Y(34), fur)
    elif sp == "hedgehog":
        spikes = []
        for i in range(15):
            a = math.pi * i / 14
            r = 34 if i % 2 == 0 else 26
            spikes.append((math.cos(a) * r, Y(6 + math.sin(a) * r * 1.2)))
        fig.poly([(-34, Y(6))] + spikes + [(34, Y(6))], dark)
        fig.ellipse(-22, Y(0), 22, Y(36), light)
    else:
        fig.ellipse(-22, Y(0), 22, Y(52), fur)
        fig.ellipse(-13, Y(4), 13, Y(40), light, outline=False)
    # paws
    if not (asleep and pose == "sleep"):
        for sx in (-1, 1):
            fig.ellipse(sx * 11 - 7, Y(-1), sx * 11 + 7, Y(8), light if sp in ("cat",) else shade(fur, 0.9))
    # head
    head_y = 64 if not (asleep and pose == "sleep") else 30
    hx = 0 if not (asleep and pose == "sleep") else -14
    ears = ANIMAL_EARS.get(sp, "pointed")
    if ears == "pointed":
        for sx in (-1, 1):
            fig.poly([(hx + sx * 8, Y(head_y + 14)), (hx + sx * 22, Y(head_y + 32)), (hx + sx * 22, Y(head_y + 8))], fur)
            fig.poly([(hx + sx * 12, Y(head_y + 15)), (hx + sx * 20, Y(head_y + 26)), (hx + sx * 20, Y(head_y + 13))],
                     (60, 40, 35) if sp == "fox" else (240, 170, 170), outline=False)
    elif ears == "long":
        for sx in (-1, 1):
            fig.ellipse(hx + sx * 10 - 6, Y(head_y + 12), hx + sx * 10 + 6, Y(head_y + 48), fur)
            fig.ellipse(hx + sx * 10 - 3, Y(head_y + 17), hx + sx * 10 + 3, Y(head_y + 43), (245, 175, 185), outline=False)
    elif ears == "tufted":
        for sx in (-1, 1):
            fig.poly([(hx + sx * 8, Y(head_y + 14)), (hx + sx * 17, Y(head_y + 34)), (hx + sx * 20, Y(head_y + 10))], fur)
    elif ears == "round" or ears == "small":
        for sx in (-1, 1):
            fig.circle(hx + sx * 15, Y(head_y + 15), 6, fur if sp != "hedgehog" else dark)
    fig.circle(hx, Y(head_y), 20, fur if sp != "hedgehog" else light)
    if ears == "floppy":
        for sx in (-1, 1):
            fig.ellipse(hx + sx * 22 - 7, Y(head_y - 14), hx + sx * 22 + 7, Y(head_y + 14), dark)
    # muzzle
    if sp == "fox":
        fig.poly([(hx - 18, Y(head_y - 2)), (hx, Y(head_y - 18)), (hx + 18, Y(head_y - 2)), (hx, Y(head_y + 2))], (255, 252, 245), outline=False)
    elif sp in ("puppy", "cat", "rabbit", "squirrel", "bear"):
        fig.ellipse(hx - 9, Y(head_y - 16), hx + 9, Y(head_y - 3), light, outline=False)
    fig.circle(hx, Y(head_y - 6), 2.8 if sp != "puppy" else 3.6, (50, 35, 35), outline=False)
    sub = fig
    old_cx = sub.cx
    sub.cx = old_cx + (hx * sub.u if not sub.flip else -hx * sub.u)
    sub.face(Y(head_y + 2) - 0, "sleep" if asleep else expr, eye_dx=7.5, scale=0.9)
    sub.cx = old_cx
    if sp in ("cat", "fox", "puppy", "rabbit", "squirrel") and not asleep:
        for sx in (-1, 1):
            for dy in (-1, 2):
                fig.line([(hx + sx * 7, Y(head_y - 8 + dy * 0.6)), (hx + sx * 17, Y(head_y - 8 + dy * 1.6))], (90, 70, 60), 0.7)
    # scarf (signature item for consistency)
    if out.get("accessory") == "scarf" and not (asleep and pose == "sleep"):
        fig.rrect(-17, Y(42), 17, Y(49), 3, acc_c)
        fig.rrect(-12, Y(30), -5, Y(45), 3, acc_c)
    return {"hold": fig.P(0, Y(30)), "head": fig.P(hx, Y(head_y)), "top": fig.P(hx, Y(head_y + 30)),
            "hand_r": fig.P(16, Y(20)), "hand_l": fig.P(-16, Y(20))}


def draw_character(fig: Figure, spec: dict, pose: str, expr: str) -> dict:
    sp = spec.get("appearance", {}).get("species", "human")
    if sp == "human":
        return draw_human(fig, spec, pose, expr)
    if sp == "human_old":
        return draw_human(fig, spec, pose if pose not in ("jump", "run") else "stand", expr, elder=True)
    return draw_animal(fig, spec, pose, expr)
