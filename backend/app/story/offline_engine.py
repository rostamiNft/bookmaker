"""Built-in offline story engine.

It does NOT use AI. It assembles hand-written narrative arcs (themes.py) into a
complete, coherent book, adapted to page count, age group, text amount, humour
and ending type. It guarantees the pipeline works with zero external services;
LLM providers (Ollama / OpenAI-compatible / Anthropic) produce free-form stories.
"""
from __future__ import annotations

import hashlib
import random
import re

from ..schemas import BookSettings, LocationSpec, PageSpec, PropSpec, Scene, StoryPlan
from .characters import NAMES, SPECIES, make_animal, make_child, make_elder
from .themes import THEMES

MIN_PAGES = 6
MAX_PAGES = max(len(t["beats"]) for t in THEMES.values())

AGE_NUM = {"3-5": "5", "6-8": "7", "9-12": "10"}


class OfflineEngineError(ValueError):
    pass


def settings_seed(s: BookSettings) -> int:
    if s.seed is not None:
        return int(s.seed)
    key = f"{s.topic}|{s.title}|{s.language}|{s.age_group}|{s.hero_name}|{s.moral}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


def choose_theme(s: BookSettings, rng: random.Random) -> str:
    text = f"{s.topic} {s.moral} {s.title}".lower()
    allowed = [k for k, t in THEMES.items() if t["fantasy_min"] <= s.fantasy_level] or ["kite_festival"]
    scored = []
    for k in allowed:
        score = sum(1 for kw in THEMES[k]["keywords"] if kw in text)
        scored.append((score, k))
    best = max(sc for sc, _ in scored)
    candidates = sorted(k for sc, k in scored if sc == best)
    return rng.choice(candidates)


def _pronouns(p: str) -> dict:
    if p == "she":
        return {"s": "she", "S": "She", "o": "her", "p": "her", "P": "Her"}
    if p == "he":
        return {"s": "he", "S": "He", "o": "him", "p": "his", "P": "His"}
    return {"s": "they", "S": "They", "o": "them", "p": "their", "P": "Their"}


def build_cast(s: BookSettings, theme_key: str, rng: random.Random):
    theme = THEMES[theme_key]
    lang = s.language
    age = AGE_NUM[s.age_group]
    hero_pronoun = rng.choice(["she", "he"])
    if s.hero_name:
        hero_name, hero_latin = s.hero_name, s.hero_name
    elif lang == "fa":
        hero_name, hero_latin = rng.choice(NAMES["fa"][hero_pronoun])
    else:
        hero_name = hero_latin = rng.choice(NAMES["en"][hero_pronoun])
    hero = make_child(rng, "hero", "protagonist", hero_pronoun, hero_name, age)

    # companion: talking animal when fantasy allows it, otherwise a child friend
    if s.fantasy_level >= 1:
        species = rng.choice(theme["companions"])
        en_name, (fa_name, fa_latin), _, en_desc, fa_desc = SPECIES[species]
        comp_pronoun = rng.choice(["she", "he"])
        comp_name = fa_name if lang == "fa" else en_name
        comp = make_animal(rng, "comp", "companion", species, comp_name, comp_pronoun)
        comp_latin = fa_latin if lang == "fa" else en_name
        comp_desc = fa_desc if lang == "fa" else en_desc
        hand_en, hand_fa = "paw", "پنجه‌اش"
    else:
        comp_pronoun = "he" if hero_pronoun == "she" else "she"
        pool = NAMES[lang][comp_pronoun]
        pick = rng.choice([n for n in pool if (n[0] if isinstance(n, tuple) else n) != hero_name])
        comp_name, comp_latin = pick if isinstance(pick, tuple) else (pick, pick)
        comp = make_child(rng, "comp", "companion", comp_pronoun, comp_name, age)
        comp_desc = "دوستی از همسایگی" if lang == "fa" else "a friend from just down the lane"
        hand_en, hand_fa = "hand", "دستش"

    h = theme["helper"]
    helper_name = h[lang]
    if h["species"] == "owl":
        helper = make_animal(rng, "helper", "supporting", "owl", helper_name, h["pronoun"])
    else:
        helper = make_elder(rng, "helper", helper_name, h["pronoun"])
    helper_latin = h["en"]

    hp, cp = _pronouns(hero_pronoun), _pronouns(comp_pronoun)
    slots = {
        "hero": hero_name, "comp": comp_name, "helper": helper_name, "comp_desc": comp_desc,
        "home": theme["home"][lang],
        "kid": ("دختر" if hero_pronoun == "she" else "پسر") if lang == "fa" else ("girl" if hero_pronoun == "she" else "boy"),
        "h_s": hp["s"], "H_s": hp["S"], "h_o": hp["o"], "h_p": hp["p"], "H_p": hp["P"],
        "c_s": cp["s"], "C_s": cp["S"], "c_o": cp["o"], "c_p": cp["p"], "c_hand": hand_en, "c_hand_fa": hand_fa,
    }
    img_slots = dict(slots, hero=hero_latin, comp=comp_latin, helper=helper_latin)
    hero.relationships = {"comp": "best friend", "helper": "trusted grown-up"}
    comp.relationships = {"hero": "best friend"}
    helper.relationships = {"hero": "mentor"}
    # store latin names for image prompts
    for c, latin in ((hero, hero_latin), (comp, comp_latin), (helper, helper_latin)):
        c.relationships["_latin_name"] = latin
    return [hero, comp, helper], slots, img_slots


def _fmt(t: str, slots: dict) -> str:
    out = re.sub(r"\s+", " ", t.format_map(slots)).strip()
    # capitalise Latin sentence starts (slots such as names/pronouns may begin a sentence)
    out = re.sub(r"^([a-z])", lambda m: m.group(1).upper(), out)
    return re.sub(r"([.!?]['\u2019\"]?\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)


def select_beats(theme: dict, n: int) -> list[dict]:
    beats = theme["beats"]
    if n < MIN_PAGES:
        raise OfflineEngineError(f"The offline engine needs at least {MIN_PAGES} story pages.")
    if n > len(beats):
        raise OfflineEngineError(
            f"The offline engine supports up to {len(beats)} story pages for this theme. "
            "Connect an LLM provider (Ollama / OpenAI-compatible) for longer books.")
    order = {b["id"]: i for i, b in enumerate(beats)}
    chosen = sorted(beats, key=lambda b: b["prio"])[:n]
    return sorted(chosen, key=lambda b: order[b["id"]])


def include_detail(s: BookSettings) -> bool:
    if s.text_amount == "short":
        return False
    if s.age_group == "3-5":
        return s.text_amount == "long"
    return True


def simplify_for_young(text: str, lang: str) -> str:
    """Split long compound sentences into shorter ones for ages 3-5."""
    if lang == "fa":
        text = re.sub(r"؛\s*", ". ", text)
        text = re.sub(r"،\s+و\s+", ". ", text)
        return text
    text = re.sub(r";\s*", ". ", text)
    text = re.sub(r",\s+(and|but|so)\s+(?=[a-z])", lambda m: ". " + m.group(1).capitalize() + " ", text)
    text = re.sub(r",\s+where\s+", ". There, ", text)
    # capitalise after full stops
    return re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)


def beat_text(beat: dict, s: BookSettings, slots: dict, variant: int, rng: random.Random,
              force_detail: bool | None = None, force_humor: bool | None = None) -> str:
    lang = s.language
    block = beat[lang]
    if "ending" in block:
        base = block["ending"][s.ending]
    else:
        texts = block["text"]
        base = texts[variant % len(texts)]
    parts = [base]
    want_detail = include_detail(s) if force_detail is None else force_detail
    if want_detail and block.get("detail"):
        parts.append(block["detail"][variant % len(block["detail"])])
    want_humor = force_humor if force_humor is not None else (
        (s.humor_level >= 2 or (s.humor_level == 1 and rng.random() < 0.35)) and s.age_group != "9-12" or s.humor_level == 3)
    if block.get("humor") and want_humor:
        parts.append(block["humor"][0])
    out = _fmt(" ".join(parts), slots)
    if s.age_group == "3-5":
        out = simplify_for_young(out, lang)
    return out


def generate_plan(s: BookSettings) -> StoryPlan:
    seed = settings_seed(s)
    rng = random.Random(seed)
    theme_key = choose_theme(s, rng)
    theme = THEMES[theme_key]
    lang = s.language
    cast, slots, img_slots = build_cast(s, theme_key, rng)
    beats = select_beats(theme, s.page_count)

    pages: list[PageSpec] = []
    arc: dict[str, list[int]] = {}
    for i, beat in enumerate(beats, start=1):
        variant = rng.randrange(2)
        text = beat_text(beat, s, slots, variant, rng)
        scene = Scene(**beat["scene"])
        pages.append(PageSpec(number=i, beat=beat["id"], text=text,
                              image_description=_fmt(beat["img"], img_slots),
                              characters_present=list(scene.characters), scene=scene))
        arc.setdefault(beat["role"], []).append(i)

    title = s.title or _fmt(rng.choice(theme["titles"][lang]), slots)
    notes = []
    if s.narration == "first_person":
        notes.append("first_person narration is not supported by the offline engine; third person was used")
    if s.main_characters > 2:
        notes.append("the offline engine writes 2 main characters + 1 supporting character")
    plan = StoryPlan(
        title=title,
        summary=_fmt(theme["summary"][lang], slots),
        moral=s.moral or theme["moral"][lang],
        theme=theme_key,
        language=lang,
        age_group=s.age_group,
        characters=cast,
        locations=[LocationSpec(id=k, name=n, kind=k) for k, n in theme["locations"]],
        props=[PropSpec(id=k, name=n, description=d) for k, n, d in theme["props"]],
        arc=arc,
        pages=pages,
        back_cover_blurb=_fmt(theme["blurb"][lang], slots),
        engine="offline:" + theme_key + (" | " + "; ".join(notes) if notes else ""),
    )
    return plan


def _slots_from_plan(plan: StoryPlan, s: BookSettings):
    """Rebuild slot dictionaries from a stored plan (so edits to names propagate)."""
    by_id = {c.id: c for c in plan.characters}
    hero, comp, helper = by_id.get("hero"), by_id.get("comp"), by_id.get("helper")
    theme = THEMES[plan.theme]
    hp = _pronouns(hero.pronoun if hero else "they")
    cp = _pronouns(comp.pronoun if comp else "they")
    animal = comp is not None and comp.appearance.species not in ("human", "human_old")
    comp_desc = ""
    if comp is not None:
        if animal:
            sp = SPECIES.get(comp.appearance.species)
            comp_desc = (sp[4] if s.language == "fa" else sp[3]) if sp else ""
        else:
            comp_desc = "دوستی از همسایگی" if s.language == "fa" else "a friend from just down the lane"
    slots = {
        "hero": hero.name if hero else "", "comp": comp.name if comp else "", "helper": helper.name if helper else "",
        "comp_desc": comp_desc, "home": theme["home"][s.language],
        "kid": ("دختر" if hp["s"] == "she" else "پسر") if s.language == "fa" else ("girl" if hp["s"] == "she" else "boy"),
        "h_s": hp["s"], "H_s": hp["S"], "h_o": hp["o"], "h_p": hp["p"], "H_p": hp["P"],
        "c_s": cp["s"], "C_s": cp["S"], "c_o": cp["o"], "c_p": cp["p"],
        "c_hand": "paw" if animal else "hand", "c_hand_fa": "پنجه‌اش" if animal else "دستش",
    }
    latin = lambda c: (c.relationships.get("_latin_name") or c.name) if c else ""
    img_slots = dict(slots, hero=latin(hero), comp=latin(comp), helper=latin(helper))
    return slots, img_slots


def regenerate_page(plan: StoryPlan, s: BookSettings, page_number: int, variant: int, shorten: bool = False) -> PageSpec:
    """Produce an alternative text for one page while keeping the plan's facts."""
    if not plan.theme or plan.theme not in THEMES:
        raise OfflineEngineError("This page was not produced by the offline engine; use an LLM provider to rewrite it.")
    page = next(p for p in plan.pages if p.number == page_number)
    beat = next((b for b in THEMES[plan.theme]["beats"] if b["id"] == page.beat), None)
    if beat is None:
        raise OfflineEngineError("Custom page: the offline engine cannot rewrite it; edit the text manually or use an LLM provider.")
    slots, img_slots = _slots_from_plan(plan, s)
    rng = random.Random(variant * 7919 + page_number)
    if shorten:
        text = beat_text(beat, s, slots, variant, rng, force_detail=False, force_humor=False)
        return page.model_copy(update={"text": text, "image_description": _fmt(beat["img"], img_slots)})
    # enumerate every distinct wording (text variant x detail x humour) allowed for this audience
    block = beat[s.language]
    n_text = 1 if "ending" in block else len(block["text"])
    details = [True, False] if (block.get("detail") and s.text_amount != "short") else [include_detail(s)]
    humors = [False, True] if (block.get("humor") and s.humor_level >= 1) else [False]
    combos = []
    for v in range(n_text):
        for d in details:
            for h in humors:
                t = beat_text(beat, s, slots, v, rng, force_detail=d, force_humor=h)
                if t not in combos:
                    combos.append(t)
    alternatives = [t for t in combos if t.strip() != page.text.strip()]
    if not alternatives:
        raise OfflineEngineError("The offline engine has no other wording for this page; edit it manually or connect an LLM provider.")
    text = alternatives[variant % len(alternatives)]
    return page.model_copy(update={"text": text, "image_description": _fmt(beat["img"], img_slots)})
