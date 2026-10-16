"""LLM-driven story planning (any provider implementing LLMProvider).

Flow: plan prompt -> strict JSON -> normalise into StoryPlan -> independent
validation -> targeted revision of only the failing pages (bounded attempts).
Characters and scenes use the same structured contract as the offline engine,
so the procedural illustrator, QA and layout work unchanged."""
from __future__ import annotations

import json
import re
from typing import Callable

from pydantic import ValidationError

from ..schemas import Appearance, BookSettings, CharacterSpec, LocationSpec, Outfit, PageSpec, PropSpec, Scene, StoryPlan
from .validator import validate_plan

LANG_NAMES = {"en": "English", "fa": "Persian (Farsi), natural modern Persian for children, using ZWNJ (نیم‌فاصله) correctly"}
SPECIES = ["human", "human_old", "fox", "rabbit", "hedgehog", "squirrel", "puppy", "cat", "owl"]
LOCATIONS = ["meadow", "forest", "river", "hill", "village", "garden", "bedroom", "yard", "field"]
POSES = ["stand", "wave", "point", "hold", "walk", "walk_hold", "run", "jump", "sit", "kneel", "hug", "lift", "pull", "give",
         "pour", "reach", "tiptoe", "look_up", "sleep"]
EXPRESSIONS = ["happy", "laugh", "sad", "worried", "surprised", "determined", "scared", "kind", "shy", "grumpy", "calm", "sleep"]
ROLES = ["setup", "inciting", "rising", "climax", "resolution", "ending"]

SYSTEM = ("You are an award-winning children's picture-book author and editor. You write original stories (never copy existing "
          "books or characters), age-appropriate, warm, coherent, with a clear beginning, middle and end. The lesson must emerge "
          "from events, never be preached. You answer with strict JSON only.")

LLMCall = Callable[[str, str, bool], str]  # (prompt, system, json_mode) -> text


def describe_settings(s: BookSettings) -> str:
    fantasy = ["realistic (no magic, animals do not talk)", "light fantasy", "magical", "very imaginative"][s.fantasy_level]
    humor = ["none", "gentle", "playful", "very funny"][s.humor_level]
    words = {"short": "15-35", "medium": "30-60", "long": "50-90"}[s.text_amount]
    if s.age_group == "3-5":
        words = {"short": "8-20", "medium": "15-35", "long": "25-50"}[s.text_amount]
    return (f"- Topic / idea: {s.topic or 'free choice'}\n- Title: {s.title or 'suggest a short, catchy title'}\n"
            f"- Moral or learning goal: {s.moral or 'choose a fitting one'}\n- Age group: {s.age_group}\n"
            f"- Language of all story text: {LANG_NAMES.get(s.language, s.language)}\n- Exactly {s.page_count} story pages\n"
            f"- Words per page: {words}\n- Complexity: {s.complexity}\n- Narration: {s.narration.replace('_', ' ')}\n"
            f"- Fantasy level: {fantasy}\n- Humour: {humor}\n- Ending type: {s.ending}\n- Main characters: {s.main_characters}"
            + (f"\n- Protagonist name: {s.hero_name}" if s.hero_name else ""))


SCHEMA_HINT = """Return JSON with this exact shape:
{
 "title": str, "summary": str (2 sentences, book language), "moral": str (book language), "back_cover_blurb": str (book language),
 "characters": [ {"id": "hero" | "comp" | "c3" ..., "name": str (book language), "latin_name": str, "role": "protagonist"|"companion"|"supporting",
     "pronoun": "she"|"he"|"they"|"it", "age": str, "personality": str,
     "description": str (ENGLISH visual description: species, age, skin/fur, hair, outfit with colours, one distinctive item),
     "species": one of SPECIES, "skin": "#rrggbb", "hair_color": "#rrggbb", "hair_style": "short|curly|ponytail|braids|bun|bob|spiky|none",
     "top": "t-shirt|dress|hoodie|overalls|sweater|cardigan", "top_color": "#rrggbb", "bottom": "shorts|trousers|skirt|", "bottom_color": "#rrggbb",
     "accessory": "scarf|hat|backpack|bow|glasses|", "accessory_color": "#rrggbb", "distinctive": [str] } ],
 "locations": [ {"id": str, "name": str, "kind": one of LOCATIONS, "description": str (English)} ],
 "pages": [ {"number": 1.., "role": one of ROLES, "text": str (book language),
     "image_description": str (ENGLISH, concrete visual scene for an illustrator, mention characters by latin_name, no text in image),
     "characters_present": [character ids], "location": one of LOCATIONS, "time": "day"|"sunset"|"night",
     "mood": str, "poses": {id: one of POSES}, "expressions": {id: one of EXPRESSIONS}, "shot": "wide"|"medium"|"close", "props": [short english nouns] } ]
}
Rules: the first page(s) have role "setup", one page has role "inciting", the climax comes in the last third, the last page has role "ending".
Every character in characters_present must exist in characters. Keep names, looks and facts identical on every page.
Do not pad with filler; every page must move the story forward. No two pages may repeat sentences."""


def _schema_text() -> str:
    return (SCHEMA_HINT.replace("SPECIES", json.dumps(SPECIES)).replace("LOCATIONS", json.dumps(LOCATIONS))
            .replace("ROLES", json.dumps(ROLES)).replace("POSES", json.dumps(POSES)).replace("EXPRESSIONS", json.dumps(EXPRESSIONS)))


def extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object in model output")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError("unterminated JSON object in model output")


def _hex(v, default):
    return v if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v or "") else default


def normalise(raw: dict, s: BookSettings) -> StoryPlan:
    chars = []
    for i, c in enumerate(raw.get("characters") or []):
        cid = re.sub(r"[^a-z0-9_]", "", str(c.get("id") or f"c{i + 1}").lower()) or f"c{i + 1}"
        species = c.get("species") if c.get("species") in SPECIES else "human"
        app = Appearance(species=species, skin=_hex(c.get("skin"), "#eabf98"), hair_color=_hex(c.get("hair_color"), "#5a3825"),
                         hair_style=c.get("hair_style") or "short")
        out = Outfit(top=c.get("top") or "t-shirt", top_color=_hex(c.get("top_color"), "#3d8bd9"), bottom=c.get("bottom") or "",
                     bottom_color=_hex(c.get("bottom_color"), "#5a3825"), accessory=c.get("accessory") or "",
                     accessory_color=_hex(c.get("accessory_color"), "#e94f4f"), shoes_color="#5a3825")
        palette = [app.skin] + ([app.hair_color] if species in ("human", "human_old") else []) + [out.top_color] + ([out.accessory_color] if out.accessory else [])
        if species not in ("human", "human_old"):
            palette = [app.skin] + ([out.accessory_color] if out.accessory else [])
        chars.append(CharacterSpec(id=cid, name=str(c.get("name") or cid), role=c.get("role") if c.get("role") in ("protagonist", "companion", "supporting") else "supporting",
                                   pronoun=c.get("pronoun") if c.get("pronoun") in ("she", "he", "they", "it") else "they", age=str(c.get("age") or ""),
                                   description=str(c.get("description") or ""), personality=str(c.get("personality") or ""), appearance=app, outfit=out,
                                   distinctive=[str(x) for x in (c.get("distinctive") or [])][:5], palette=palette,
                                   relationships={"_latin_name": str(c.get("latin_name") or c.get("name") or cid)}))
    if chars and not any(c.role == "protagonist" for c in chars):
        chars[0].role = "protagonist"
    ids = {c.id for c in chars}
    pages, arc = [], {}
    for i, p in enumerate(raw.get("pages") or [], start=1):
        role = p.get("role") if p.get("role") in ROLES else "rising"
        present = [x for x in (p.get("characters_present") or []) if x in ids]
        loc = p.get("location") if p.get("location") in LOCATIONS else "meadow"
        scene = Scene(location=loc, time=p.get("time") if p.get("time") in ("day", "sunset", "night") else "day", characters=present,
                      props=[re.sub(r"[^a-z_]", "_", str(x).lower()) for x in (p.get("props") or [])][:6], mood=str(p.get("mood") or "happy"),
                      poses={k: v for k, v in (p.get("poses") or {}).items() if k in ids and v in POSES},
                      expressions={k: v for k, v in (p.get("expressions") or {}).items() if k in ids and v in EXPRESSIONS},
                      shot=p.get("shot") if p.get("shot") in ("wide", "medium", "close") else "medium")
        pages.append(PageSpec(number=i, beat=role, text=str(p.get("text") or "").strip(), image_description=str(p.get("image_description") or "").strip(),
                              characters_present=present, scene=scene))
        arc.setdefault(role, []).append(i)
    return StoryPlan(title=str(raw.get("title") or s.title or "").strip(), summary=str(raw.get("summary") or ""), moral=str(raw.get("moral") or s.moral),
                     theme="llm", language=s.language, age_group=s.age_group, characters=chars,
                     locations=[LocationSpec(id=str(l.get("id") or l.get("kind") or "loc"), name=str(l.get("name") or ""), kind=l.get("kind") if l.get("kind") in LOCATIONS else "meadow",
                                             description=str(l.get("description") or "")) for l in (raw.get("locations") or [])],
                     props=[], arc=arc, pages=pages, back_cover_blurb=str(raw.get("back_cover_blurb") or ""), engine="llm")


def generate_plan_llm(call: LLMCall, s: BookSettings, max_attempts: int = 3, log: Callable[[str, dict], None] | None = None) -> tuple[StoryPlan, dict]:
    prompt = f"Write a complete children's picture book plan.\nSettings:\n{describe_settings(s)}\n\n{_schema_text()}"
    plan, report, raw = None, None, None
    history = []
    for attempt in range(1, max_attempts + 1):
        text = call(prompt, SYSTEM, True)
        try:
            raw = extract_json(text)
            cand = normalise(raw, s)
        except (ValueError, ValidationError, json.JSONDecodeError) as e:
            history.append({"attempt": attempt, "error": f"unparseable: {e}"})
            if log:
                log("story_parse_error", {"attempt": attempt, "error": str(e)[:300]})
            prompt = (f"Your previous answer was not valid JSON for the schema ({e}). Produce the full plan again.\n"
                      f"Settings:\n{describe_settings(s)}\n\n{_schema_text()}")
            continue
        report = validate_plan(cand, s)
        plan = cand
        history.append({"attempt": attempt, "errors": report["errors"], "warnings": report["warnings"]})
        if log:
            log("story_validated", {"attempt": attempt, "errors": report["errors"], "warnings": report["warnings"]})
        if report["ok"] and report["warnings"] <= max(2, s.page_count // 4):
            break
        if attempt == max_attempts:
            break
        # targeted revision: ask to fix only the listed problems
        problems = "\n".join(f"- page {i.get('page') or '-'}: [{i['code']}] {i['message']}" for i in report["issues"][:25])
        prompt = (f"Here is a children's book plan as JSON:\n{json.dumps(raw, ensure_ascii=False)}\n\nAn editor found these problems:\n{problems}\n\n"
                  f"Fix ONLY what is needed (keep characters, names and good pages unchanged) and return the complete corrected JSON "
                  f"with exactly {s.page_count} pages.\n{_schema_text()}")
    if plan is None:
        raise ValueError("The language model did not return a usable story plan")
    return plan, {"attempts": history, "final": report}


def regenerate_page_llm(call: LLMCall, plan: StoryPlan, s: BookSettings, number: int, instruction: str = "", shorten: bool = False) -> PageSpec:
    page = next(p for p in plan.pages if p.number == number)
    prev_t = next((p.text for p in plan.pages if p.number == number - 1), "")
    next_t = next((p.text for p in plan.pages if p.number == number + 1), "")
    bible = [{"id": c.id, "name": c.name, "latin_name": c.relationships.get("_latin_name", c.name), "description": c.description} for c in plan.characters]
    prompt = (f"Rewrite page {number} of this children's book (language: {LANG_NAMES.get(s.language)}; age {s.age_group}).\n"
              f"Title: {plan.title}\nSummary: {plan.summary}\nCharacters: {json.dumps(bible, ensure_ascii=False)}\n"
              f"Previous page: {prev_t}\nCurrent page: {page.text}\nNext page: {next_t}\n"
              + (f"Editor instruction: {instruction}\n" if instruction else "")
              + ("Make it clearly SHORTER (about half the words) while keeping the events.\n" if shorten else "")
              + 'Keep events consistent with neighbouring pages. Return JSON {"text": str, "image_description": str (English)}.')
    raw = extract_json(call(prompt, SYSTEM, True))
    text = str(raw.get("text") or "").strip()
    if not text:
        raise ValueError("empty rewrite")
    return page.model_copy(update={"text": text, "image_description": str(raw.get("image_description") or page.image_description)})
