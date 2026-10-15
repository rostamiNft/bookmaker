from __future__ import annotations


def hex2rgb(h: str, default=(200, 200, 200)) -> tuple[int, int, int]:
    if not h or not isinstance(h, str):
        return default
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore
    except ValueError:
        return default


def mix(a, b, t: float):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def shade(c, f: float):
    """f<1 darker, f>1 lighter (towards white)."""
    if f <= 1:
        return tuple(max(0, int(v * f)) for v in c)
    t = min(1.0, f - 1)
    return mix(c, (255, 255, 255), t)


def rgba(c, a: int = 255):
    return (c[0], c[1], c[2], a)


NIGHT_TINT = (52, 74, 150)


def night(c, amount: float = 0.42):
    return mix(c, NIGHT_TINT, amount)


def sunset(c, amount: float = 0.25):
    return mix(c, (255, 150, 90), amount)


def timed(c, time: str, strength: float = 1.0):
    if time == "night":
        return night(c, 0.42 * strength)
    if time == "sunset":
        return sunset(c, 0.22 * strength)
    return c
