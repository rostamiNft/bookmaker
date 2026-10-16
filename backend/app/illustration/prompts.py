"""Prompt construction for AI image models. Character identity comes from the
Character Bible (fixed descriptions + reference sheets + fixed seed), not from
free text re-invented on every page."""
from __future__ import annotations

from ..schemas import ILLUSTRATION_STYLES

NEGATIVE = ("text, letters, words, captions, watermark, signature, logo, speech bubble, extra people, extra characters, "
            "duplicate character, deformed hands, extra fingers, missing limbs, distorted face, blurry, low quality, "
            "photorealistic, scary, violent")


def lora_prefixes(characters: list[dict]) -> str:
    """Trained per-character LoRAs ("<lora:name:weight> trigger"), once per prompt."""
    from ..export.lora import lora_token
    toks = [t for t in (lora_token(c) for c in characters or []) if t]
    return " ".join(dict.fromkeys(toks)) + " " if toks else ""


def latin_name(c: dict) -> str:
    return (c.get("relationships") or {}).get("_latin_name") or c.get("name", "")


def relative_heights(characters: list[dict]) -> dict[str, float]:
    from .scene import character_height
    hs = {c["id"]: character_height(c, 1.0) for c in characters}
    top = max(hs.values()) if hs else 1.0
    return {k: round(v / top, 2) for k, v in hs.items()}


def scale_and_relations(present: list[dict], all_chars: list[dict]) -> list[str]:
    """Logic rules from the Character Bible: relative sizes and relationships of the
    characters on this page (keeps scale consistent across pages for AI models)."""
    lines = []
    if len(present) >= 2:
        rel = relative_heights(all_chars)
        tallest = max(present, key=lambda c: rel.get(c["id"], 1))
        parts = [f"{latin_name(c)} is about {round(rel[c['id']] / rel[tallest['id']] * 100)}% of {latin_name(tallest)}'s height"
                 for c in present if c is not tallest]
        lines.append(f"Relative sizes (keep consistent): {latin_name(tallest)} is the tallest; " + "; ".join(parts) + ".")
    ids = {c["id"]: c for c in present}
    rels = []
    for c in present:
        for other, how in (c.get("relationships") or {}).items():
            if other in ids and not other.startswith("_") and how:
                rels.append(f"{latin_name(c)} - {how} of {latin_name(ids[other])}")
    if rels:
        lines.append("Relationships: " + "; ".join(sorted(set(rels))[:4]) + ".")
    return lines


def build_page_prompt(page: dict, characters: list[dict], style: str, text_in_image: bool = False,
                      aspect: str = "4:3") -> tuple[str, str]:
    present = [c for c in characters if c["id"] in page.get("characters_present", [])]
    scene = page.get("scene", {})
    lines = [
        f"Children's picture-book illustration, {ILLUSTRATION_STYLES.get(style, style)}.",
        f"Scene: {page.get('image_description', '').strip()}",
        f"Setting: {scene.get('location', '')}, time of day: {scene.get('time', 'day')}, mood: {scene.get('mood', '')}.",
    ]
    if present:
        lines.append(f"Exactly {len(present)} main character{'s' if len(present) > 1 else ''} visible:")
        for c in present:
            pose = scene.get("poses", {}).get(c["id"], "")
            expr = scene.get("expressions", {}).get(c["id"], "")
            lines.append(f"- {latin_name(c)}: {c.get('description', '')}. Always the same look: {', '.join(c.get('distinctive', []))}."
                         + (f" Pose: {pose}." if pose else "") + (f" Expression: {expr}." if expr else ""))
    lines += scale_and_relations(present, characters)
    lines.append(f"Composition: {scene.get('shot', 'medium')} shot, aspect ratio {aspect}, clear readable staging, all characters fully inside the frame.")
    if not text_in_image:
        lines.append("Absolutely no text, letters or writing anywhere in the image.")
    return lora_prefixes(present) + "\n".join(lines), NEGATIVE + ("" if not text_in_image else "").replace("text, letters, words, captions, ", "" if text_in_image else "text, letters, words, captions, ")


def build_reference_prompt(c: dict, style: str, mode: str = "multi_view") -> tuple[str, str]:
    if mode == "multi_view":
        views = ("Character turnaround sheet: the same character shown four times side by side - front view, three-quarter view, "
                 "side profile and back view - full body, neutral standing pose, identical proportions, outfit and colours in every view")
    else:
        views = "Full body, front view, three poses side by side (standing, waving, pointing)"
    p = (f"Character reference sheet for a children's picture book, {ILLUSTRATION_STYLES.get(style, style)}. "
         f"{latin_name(c)}: {c.get('description', '')}. {views}, "
         f"plain light background, consistent proportions and colours. Distinctive: {', '.join(c.get('distinctive', []))}. No text, no labels.")
    return lora_prefixes([c]) + p, NEGATIVE


def build_cast_prompt(characters: list[dict], style: str) -> tuple[str, str]:
    rel = relative_heights(characters)
    who = "; ".join(f"{latin_name(c)} ({c.get('description', '')}, {round(rel[c['id']] * 100)}% height)" for c in characters)
    p = (f"Cast line-up for a children's picture book, {ILLUSTRATION_STYLES.get(style, style)}. All characters standing side by side on one "
         f"ground line, front view, full body, true relative sizes: {who}. Plain light background. No text, no labels.")
    return lora_prefixes(characters) + p, NEGATIVE


def build_cover_prompt(plan: dict, characters: list[dict], style: str) -> tuple[str, str]:
    hero = next((c for c in characters if c.get("role") == "protagonist"), characters[0] if characters else {})
    comp = next((c for c in characters if c.get("role") == "companion"), None)
    who = f"{latin_name(hero)} ({hero.get('description', '')})" + (f" and {latin_name(comp)} ({comp.get('description', '')})" if comp else "")
    p = (f"Front cover illustration for a children's picture book, {ILLUSTRATION_STYLES.get(style, style)}. "
         f"{who} in an inviting key scene that captures the story: {plan.get('summary', '')}. "
         "Leave calm empty sky in the upper third for the title. No text or letters.")
    return lora_prefixes(characters) + p, NEGATIVE
