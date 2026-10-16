"""Word-level timing for read-along narration (highlight, video captions, EPUB SMIL).

Two sources, always labelled where they are used:

* **exact** - eSpeak NG reports a WORD event per spoken word: its character range
  (shifted by one character, see :func:`timings_from_events`) and its offset in the
  generated audio. Words without an event are interpolated between their anchors.
* **estimated** - every other engine. The audio duration is shared between the words
  in proportion to their length (:func:`proportional_timings`). This is *not* forced
  alignment, and every export that uses it says so.

A timing list is ``[{"w": word, "t": start_s, "d": duration_s}, ...]`` covering the
whole page text in order.
"""
from __future__ import annotations

import re

WORD_RE = re.compile(r"\S+")
ESPEAK_EVENT_WORD = 1


def split_words(text: str) -> list[tuple[int, int, str]]:
    """[(start_char, end_char, word)] in logical order."""
    return [(m.start(), m.end(), m.group()) for m in WORD_RE.finditer(text)]


def proportional_timings(text: str, duration_s: float) -> list[dict]:
    """Share the duration between words by length (estimated, not aligned)."""
    spans = split_words(text)
    if not spans or duration_s <= 0:
        return []
    weights = [len(w) + 1.0 for _a, _b, w in spans]
    total = sum(weights)
    out, t = [], 0.0
    for wt, (_a, _b, w) in zip(weights, spans):
        d = duration_s * wt / total
        out.append({"w": w, "t": round(t, 3), "d": round(d, 3)})
        t += d
    return out


def _anchors(spans: list[tuple], events: list[tuple]) -> dict[int, float]:
    """token index -> start time in seconds, from eSpeak WORD events.

    eSpeak reports ``text_position`` one character too far and never emits an event
    for the very first word, so the reported range is shifted back and matched against
    the real token spans; anything unmatched is interpolated later."""
    seen, out = set(), {}
    for kind, tp, tl, ms in events:
        if kind != ESPEAK_EVENT_WORD or tl <= 0:
            continue
        if (tp, tl, ms) in seen:  # the same event list arrives with every audio chunk
            continue
        seen.add((tp, tl, ms))
        for cand in (tp - 1, tp, tp + 1):  # tolerate the off-by-one in both directions
            hit = next((i for i, (a, b, _w) in enumerate(spans) if a <= cand < b), None)
            if hit is not None:
                t = max(0.0, ms) / 1000.0
                if hit not in out or t < out[hit]:
                    out[hit] = t
                break
    return out


def timings_from_events(text: str, events: list[tuple], duration_s: float) -> list[dict]:
    """eSpeak NG WORD events -> timings; words without an event are interpolated."""
    spans = split_words(text)
    if not spans or duration_s <= 0:
        return []
    anchors = _anchors(spans, events)
    if not anchors:
        return proportional_timings(text, duration_s)
    anchors.setdefault(0, 0.0)  # the first word has no event and starts at 0
    starts: list[float | None] = [anchors.get(i) for i in range(len(spans))]
    # interpolate the gaps between two anchors, proportionally to character position
    for i in range(len(starts)):
        if starts[i] is not None:
            continue
        prev = next((j for j in range(i - 1, -1, -1) if starts[j] is not None), None)
        nxt = next((j for j in range(i + 1, len(starts)) if starts[j] is not None), None)
        if prev is None and nxt is not None:
            starts[i] = max(0.0, starts[nxt] * (i + 1) / (nxt + 1))
        elif nxt is None and prev is not None:
            starts[i] = min(duration_s, starts[prev] + (duration_s - starts[prev]) * (i - prev) / max(1, len(starts) - prev))
        elif prev is not None and nxt is not None:
            f = (i - prev) / (nxt - prev)
            starts[i] = starts[prev] + f * (starts[nxt] - starts[prev])
        else:
            starts[i] = 0.0
    starts = [max(0.0, min(duration_s, float(s))) for s in starts]
    for i in range(1, len(starts)):  # strictly non-decreasing
        if starts[i] < starts[i - 1]:
            starts[i] = starts[i - 1]
    out = []
    for i, (_a, _b, w) in enumerate(spans):
        end = starts[i + 1] if i + 1 < len(starts) else duration_s
        out.append({"w": w, "t": round(starts[i], 3), "d": round(max(0.05, end - starts[i]), 3)})
    return out


def timings_quality(timings: list[dict], text: str, duration_s: float) -> dict:
    """Sanity metrics used by QA: coverage, monotonicity, no overshoot past the audio."""
    if not timings:
        return {"words": 0, "ok": False, "reason": "no timings"}
    words = [w for _a, _b, w in split_words(text)]
    covered = sum(1 for w in words if any(t["w"] == w for t in timings))
    mono = all(timings[i]["t"] <= timings[i + 1]["t"] + 1e-6 for i in range(len(timings) - 1))
    last = timings[-1]["t"] + timings[-1]["d"]
    return {"words": len(timings), "text_words": len(words), "coverage": round(covered / max(1, len(words)), 3),
            "monotonic": bool(mono), "ends_at": round(last, 2), "duration_s": round(duration_s, 2),
            "overshoot": round(max(0.0, last - duration_s), 2),
            "ok": bool(mono and covered / max(1, len(words)) >= 0.9 and last <= duration_s + 1.5)}
