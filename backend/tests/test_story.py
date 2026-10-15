import itertools

import pytest

from app.schemas import BookSettings, CharacterSpec, PageSpec, Scene, StoryPlan
from app.story.offline_engine import MAX_PAGES, MIN_PAGES, OfflineEngineError, generate_plan, regenerate_page
from app.story.validator import mentions, sentences, similarity, validate_plan


@pytest.mark.parametrize("lang,age,n,fantasy,ending", list(itertools.product(["en", "fa"], ["3-5", "6-8", "9-12"], [6, 12, 16, 18], [0, 2], ["happy", "open"])))
def test_offline_plans_are_valid(lang, age, n, fantasy, ending):
    s = BookSettings(topic="friendship", language=lang, age_group=age, page_count=n, fantasy_level=fantasy, ending=ending, seed=n + fantasy)
    plan = generate_plan(s)
    assert len(plan.pages) == n
    assert [p.number for p in plan.pages] == list(range(1, n + 1))
    rep = validate_plan(plan, s)
    assert rep["ok"], rep["issues"]
    assert plan.pages[-1].beat == "ending" and plan.pages[0].beat == "setup"
    ids = {c.id for c in plan.characters}
    assert all(set(p.characters_present) <= ids for p in plan.pages)


def test_fantasy_zero_never_uses_talking_animal_theme():
    for seed in range(20):
        plan = generate_plan(BookSettings(topic="a star in the sky", fantasy_level=0, page_count=10, seed=seed))
        assert plan.theme != "lost_star"
        comp = next(c for c in plan.characters if c.id == "comp")
        assert comp.appearance.species == "human"


def test_topic_keywords_select_theme():
    assert generate_plan(BookSettings(topic="being brave in the dark at bedtime", page_count=10, seed=1)).theme == "brave_night"
    assert generate_plan(BookSettings(topic="saving the river and nature", page_count=10, seed=1)).theme == "silent_stream"
    assert generate_plan(BookSettings(topic="ستاره در آسمان شب", language="fa", page_count=10, seed=1)).theme == "lost_star"


def test_persian_text_keeps_zwnj_and_persian_names():
    plan = generate_plan(BookSettings(topic="دوستی", language="fa", page_count=12, seed=3))
    joined = " ".join(p.text for p in plan.pages)
    assert "\u200c" in joined
    hero = plan.characters[0]
    assert any("\u0600" <= ch <= "\u06ff" for ch in hero.name)
    assert hero.relationships["_latin_name"].isascii()


def test_page_limits_are_reported_honestly():
    with pytest.raises(OfflineEngineError):
        generate_plan(BookSettings(page_count=MAX_PAGES + 1))
    with pytest.raises(OfflineEngineError):
        generate_plan(BookSettings(page_count=MIN_PAGES - 1))


def test_hero_name_override_and_determinism():
    s = BookSettings(topic="kite", hero_name="Zoë", page_count=10, seed=4)
    a, b = generate_plan(s), generate_plan(s)
    assert a.model_dump() == b.model_dump()
    assert a.characters[0].name == "Zoë" and "Zoë" in a.pages[0].text


def test_regenerate_page_gives_different_text_same_facts():
    s = BookSettings(topic="night", page_count=12, seed=8, text_amount="medium", humor_level=1)
    plan = generate_plan(s)
    for p in plan.pages:
        new = regenerate_page(plan, s, p.number, 1)
        assert new.text != p.text
        assert new.characters_present == p.characters_present
    short = regenerate_page(plan, s, 1, 0, shorten=True)
    assert len(short.text) <= len(plan.pages[0].text)


def _plan(texts, chars=("hero",), beats=None):
    pages = [PageSpec(number=i + 1, beat=(beats[i] if beats else "rising"), text=t, image_description="x", characters_present=list(chars),
                      scene=Scene(characters=list(chars))) for i, t in enumerate(texts)]
    arc = {}
    for p in pages:
        arc.setdefault(p.beat, []).append(p.number)
    return StoryPlan(title="T", characters=[CharacterSpec(id="hero", name="Ana", role="protagonist")], pages=pages, arc=arc, moral="Be kind.")


def test_validator_catches_structural_problems():
    s = BookSettings(page_count=6)
    beats = ["setup", "rising", "rising", "rising", "rising", "rising"]
    texts = ["Ana went out to play in the big green park today."] * 6
    rep = validate_plan(_plan(texts, beats=beats), s)
    codes = {i["code"] for i in rep["issues"]}
    assert {"duplicate_page", "no_climax", "no_ending"} <= codes
    assert not rep["ok"]


def test_validator_unknown_character_preachy_and_unfinished():
    s = BookSettings(page_count=6, ending="happy")
    beats = ["setup", "inciting", "rising", "climax", "resolution", "ending"]
    texts = [f"Ana had a different adventure number {i} with the kind old dog near the lake." for i in range(5)] + [
        "The moral of the story is that Ana should always share, and"]
    p = _plan(texts, beats=beats)
    p.pages[2].characters_present.append("ghost")
    rep = validate_plan(p, s)
    codes = {i["code"] for i in rep["issues"]}
    assert {"unknown_character", "preachy", "unfinished_ending"} <= codes


def test_text_helpers():
    assert mentions("پارسا آمد", "پارسا") and not mentions("پارسال آمد", "پارسا")
    assert mentions("Leo ran.", "Leo") and not mentions("Leon ran.", "Leo")
    assert sentences('He said, "Go!" Then left.') == ['He said, "Go!"', "Then left."]
    assert similarity("a b c d e", "a b c d e") == 1.0
