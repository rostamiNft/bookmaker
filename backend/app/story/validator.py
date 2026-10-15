"""Independent story QA. It never trusts the generator: every plan (offline or
LLM) is checked for structure, coherence, duplication, age fit and consistency.
Each issue carries severity, page and the stage that should repair it."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from ..schemas import BookSettings, StoryPlan

# words-per-page bounds by age group and text amount: (min, max)
WORD_LIMITS = {
    "3-5": {"short": (4, 35), "medium": (6, 50), "long": (8, 75)},
    "6-8": {"short": (8, 45), "medium": (12, 80), "long": (15, 110)},
    "9-12": {"short": (10, 70), "medium": (15, 120), "long": (20, 170)},
}
# Persian words are on average shorter / more numerous per idea
FA_FACTOR = 1.25
MAX_AVG_SENTENCE_WORDS = {"3-5": 18, "6-8": 22, "9-12": 30}
MAX_AVG_WORD_LEN_EN = {"3-5": 5.2, "6-8": 5.8, "9-12": 6.6}

PREACHY = [
    "the moral of the story", "moral of this story", "always remember that", "you should always", "the lesson is",
    "نتیجه‌ی اخلاقی", "نتیجه اخلاقی", "درس این داستان", "همیشه یادت باشد", "باید همیشه",
]
UNFINISHED_ENDINGS = ("...", "…", ",", "،", ":", " and", " but", " و")
NONSENSE = ["lorem ipsum", "todo", "placeholder", "[insert", "{", "}"]

TERMINAL = (".", "!", "?", "!'", ".'", "?'", "\u201d", "\"", "»", "؟", "!»", ".»", "؟»", "'")


@dataclass
class Issue:
    code: str
    severity: str  # error | warning
    message: str
    page: int | None = None
    stage: str = "story"

    def as_dict(self):
        return asdict(self)


def words(text: str) -> list[str]:
    return re.findall(r"[\w\u0600-\u06FF\u200c'-]+", text)


def sentences(text: str) -> list[str]:
    # sentence end = terminal punctuation, optionally followed by a closing quote (» ” ' ")
    parts = re.split(r"(?<=[.!?؟…])\s+|(?<=[.!?؟…][»”'\"])\s+", text.strip())
    return [p for p in parts if p.strip()]


def mentions(text: str, name: str) -> bool:
    """Whole-word match (works for Persian too: «پارسا» must not match «پارسال»)."""
    return re.search(r"(?<![\w\u200c])" + re.escape(name) + r"(?![\w\u200c])", text) is not None


def _shingles(text: str, n: int = 3) -> set:
    w = [x.lower() for x in words(text)]
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def similarity(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def validate_plan(plan: StoryPlan, s: BookSettings) -> dict:
    issues: list[Issue] = []
    pages = plan.pages
    lang = plan.language or s.language
    char_ids = {c.id for c in plan.characters}

    # --- structure
    if not plan.title.strip():
        issues.append(Issue("missing_title", "error", "The book has no title.", stage="story"))
    if len(pages) != s.page_count:
        issues.append(Issue("page_count", "error", f"Plan has {len(pages)} pages but {s.page_count} were requested."))
    nums = [p.number for p in pages]
    if nums != list(range(1, len(pages) + 1)):
        issues.append(Issue("page_order", "error", f"Page numbers are not sequential: {nums}"))
    if not plan.characters:
        issues.append(Issue("no_characters", "error", "The character bible is empty.", stage="characters"))
    if not any(c.role == "protagonist" for c in plan.characters):
        issues.append(Issue("no_protagonist", "error", "No protagonist defined.", stage="characters"))

    # --- arc: beginning / middle / end
    roles = [p.beat for p in pages]
    n = len(pages)
    if n >= 4:
        arc = plan.arc or {}
        climax_pages = arc.get("climax", [])
        if not climax_pages:
            issues.append(Issue("no_climax", "error", "The story has no identifiable climax."))
        elif min(climax_pages) < n * 0.4:
            issues.append(Issue("early_climax", "warning", f"Climax appears early (page {min(climax_pages)} of {n})."))
        if not arc.get("setup") or min(arc.get("setup", [n])) > 2:
            issues.append(Issue("no_setup", "error", "The story does not open with a setup."))
        if not arc.get("ending") or max(arc.get("ending", [0])) != n:
            issues.append(Issue("no_ending", "error", "The last page is not an ending."))
        if not arc.get("resolution") and not arc.get("ending"):
            issues.append(Issue("no_resolution", "error", "The conflict is never resolved."))

    lim = WORD_LIMITS[s.age_group][s.text_amount]
    factor = FA_FACTOR if lang == "fa" else 1.0
    lo, hi = int(lim[0] * factor), int(lim[1] * factor)
    all_w = 0
    for p in pages:
        t = p.text.strip()
        w = words(t)
        all_w += len(w)
        if len(w) < lo:
            issues.append(Issue("too_short", "error" if len(w) < 3 else "warning", f"Only {len(w)} words (min {lo}).", p.number))
        if len(w) > hi:
            issues.append(Issue("too_long", "warning", f"{len(w)} words (max {hi} for age {s.age_group}).", p.number))
        low = t.lower()
        for bad in NONSENSE:
            if bad in low:
                issues.append(Issue("nonsense", "error", f"Template/placeholder text found: '{bad}'.", p.number))
        if re.search(r"\b(\w+)\s+\1\b", low) and lang == "en":
            m = re.search(r"\b(\w+)\s+\1\b", low)
            if m.group(1) not in {"down", "up", "very", "on", "rustle", "higher", "step", "one"}:
                issues.append(Issue("repeated_word", "warning", f"Repeated word '{m.group(1)}'.", p.number))
        sents = sentences(t)
        if sents:
            avg = len(w) / len(sents)
            if avg > MAX_AVG_SENTENCE_WORDS[s.age_group] * factor:
                issues.append(Issue("long_sentences", "warning", f"Average sentence length {avg:.0f} words is high for age {s.age_group}.", p.number))
        if lang == "en" and w:
            avg_len = sum(len(x) for x in w) / len(w)
            if avg_len > MAX_AVG_WORD_LEN_EN[s.age_group]:
                issues.append(Issue("hard_vocabulary", "warning", f"Average word length {avg_len:.1f} may be hard for age {s.age_group}.", p.number))
        for ph in PREACHY:
            if ph in low:
                issues.append(Issue("preachy", "warning", f"Moral is stated too directly ('{ph}').", p.number))
        # characters
        for cid in p.characters_present:
            if cid not in char_ids:
                issues.append(Issue("unknown_character", "error", f"Character id '{cid}' is not in the character bible.", p.number, "story"))
        if not p.image_description.strip():
            issues.append(Issue("no_image_description", "error", "Missing illustration brief.", p.number))
        # text <-> image coherence: named characters in the text should be present in the scene
        for c in plan.characters:
            if c.name and mentions(t, c.name) and c.id not in p.characters_present and c.role != "supporting":
                issues.append(Issue("text_image_mismatch", "warning", f"'{c.name}' is mentioned but not in the illustration.", p.number))

    # --- duplicates / near duplicates
    for i in range(n):
        for j in range(i + 1, n):
            sim = similarity(pages[i].text, pages[j].text)
            if pages[i].text.strip() == pages[j].text.strip():
                issues.append(Issue("duplicate_page", "error", f"Same text as page {pages[i].number}.", pages[j].number))
            elif sim > 0.45:
                issues.append(Issue("near_duplicate", "warning", f"Very similar to page {pages[i].number} ({sim:.0%}).", pages[j].number))
    seen = {}
    for p in pages:
        for snt in sentences(p.text):
            key = snt.strip().lower()
            if len(words(key)) >= 5:
                if key in seen and seen[key] != p.number:
                    issues.append(Issue("repeated_sentence", "warning", f"Sentence repeated from page {seen[key]}.", p.number))
                seen.setdefault(key, p.number)

    # --- protagonist continuity
    hero = next((c for c in plan.characters if c.role == "protagonist"), None)
    if hero and n:
        present = sum(1 for p in pages if hero.id in p.characters_present)
        if present / n < 0.6:
            issues.append(Issue("protagonist_absent", "warning", f"Protagonist appears in only {present}/{n} pages."))
        mentioned = sum(1 for p in pages if mentions(p.text, hero.name))
        if mentioned / n < 0.4:
            issues.append(Issue("protagonist_unnamed", "warning", f"Protagonist is named in only {mentioned}/{n} pages."))

    # --- ending must be complete
    if pages:
        last = pages[-1].text.strip()
        if s.ending != "open" and last.endswith(UNFINISHED_ENDINGS):
            issues.append(Issue("unfinished_ending", "error", "The final page ends mid-sentence.", pages[-1].number))
        if not last.endswith(TERMINAL) and not last.endswith(UNFINISHED_ENDINGS):
            issues.append(Issue("unterminated", "warning", "The final sentence has no closing punctuation.", pages[-1].number))
    # moral stated verbatim
    if plan.moral:
        verbatim = sum(1 for p in pages if plan.moral.strip().lower().rstrip(".") in p.text.lower())
        if verbatim > 1:
            issues.append(Issue("moral_repeated", "warning", "The moral sentence is repeated verbatim; it should emerge from the story."))

    errors = [i for i in issues if i.severity == "error"]
    return {
        "ok": not errors,
        "errors": len(errors),
        "warnings": len(issues) - len(errors),
        "word_count": all_w,
        "issues": [i.as_dict() for i in issues],
    }
