"""Prompt construction for AI image models. Character identity comes from the
Character Bible (fixed descriptions + reference sheets + fixed seed), not from
free text re-invented on every page."""
from __future__ import annotations

from ..schemas import ILLUSTRATION_STYLES

NEGATIVE = ("text, letters, words, captions, watermark, signature, logo, speech bubble, extra people, extra characters, "
            "duplicate character, deformed hands, extra fingers, missing limbs, distorted face, blurry, low quality, "
            "photorealistic, scary, violent")


def latin_name(c: dict) -> str:
    return (c.get("relationships") or {}).get("_latin_name") or c.get("name", "")


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
    lines.append(f"Composition: {scene.get('shot', 'medium')} shot, aspect ratio {aspect}, clear readable staging, all characters fully inside the frame.")
    if not text_in_image:
        lines.append("Absolutely no text, letters or writing anywhere in the image.")
    return "\n".join(lines), NEGATIVE + ("" if not text_in_image else "").replace("text, letters, words, captions, ", "" if text_in_image else "text, letters, words, captions, ")


def build_reference_prompt(c: dict, style: str) -> tuple[str, str]:
    p = (f"Character reference sheet for a children's picture book, {ILLUSTRATION_STYLES.get(style, style)}. "
         f"{latin_name(c)}: {c.get('description', '')}. Full body, front view, three poses side by side (standing, waving, pointing), "
         f"plain light background, consistent proportions and colours. Distinctive: {', '.join(c.get('distinctive', []))}. No text.")
    return p, NEGATIVE


def build_cover_prompt(plan: dict, characters: list[dict], style: str) -> tuple[str, str]:
    hero = next((c for c in characters if c.get("role") == "protagonist"), characters[0] if characters else {})
    comp = next((c for c in characters if c.get("role") == "companion"), None)
    who = f"{latin_name(hero)} ({hero.get('description', '')})" + (f" and {latin_name(comp)} ({comp.get('description', '')})" if comp else "")
    p = (f"Front cover illustration for a children's picture book, {ILLUSTRATION_STYLES.get(style, style)}. "
         f"{who} in an inviting key scene that captures the story: {plan.get('summary', '')}. "
         "Leave calm empty sky in the upper third for the title. No text or letters.")
    return p, NEGATIVE
