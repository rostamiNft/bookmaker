import json

import pytest

from app.schemas import BookSettings
from app.story.llm_engine import extract_json, generate_plan_llm, normalise, regenerate_page_llm
from tests.mock_servers import story


def test_extract_json_handles_fences_and_noise():
    assert extract_json('```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    assert extract_json('Sure! {"x": 1} trailing') == {"x": 1}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_normalise_sanitises_unknown_values():
    raw = story(8)
    raw["characters"][0]["species"] = "dragon"
    raw["characters"][0]["skin"] = "not-a-colour"
    raw["pages"][0]["characters_present"].append("nobody")
    raw["pages"][0]["location"] = "moon base"
    plan = normalise(raw, BookSettings(page_count=8))
    assert plan.characters[0].appearance.species == "human"
    assert plan.characters[0].appearance.skin.startswith("#")
    assert "nobody" not in plan.pages[0].characters_present
    assert plan.pages[0].scene.location == "meadow"
    assert plan.arc["ending"] == [8]


def test_generate_plan_repairs_bad_json_and_validates():
    calls = []

    def fake(prompt, system, json_mode):
        calls.append(prompt)
        if len(calls) == 1:
            return "here you go: {broken"
        return json.dumps(story(8))

    plan, meta = generate_plan_llm(fake, BookSettings(page_count=8), max_attempts=3)
    assert len(plan.pages) == 8 and plan.title == "Mila Builds a Boat"
    assert meta["attempts"][0]["error"].startswith("unparseable")
    assert meta["final"]["ok"]
    assert "not valid JSON" in calls[1]


def test_generate_plan_requests_targeted_revision_for_wrong_page_count():
    calls = []

    def fake(prompt, system, json_mode):
        calls.append(prompt)
        return json.dumps(story(6 if len(calls) == 1 else 8))

    plan, meta = generate_plan_llm(fake, BookSettings(page_count=8), max_attempts=3)
    assert len(plan.pages) == 8
    assert "page_count" in calls[1] and "Fix ONLY" in calls[1]


def test_regenerate_page_llm():
    plan = normalise(story(8), BookSettings(page_count=8))
    new = regenerate_page_llm(lambda p, s, j: json.dumps({"text": "New words.", "image_description": "new"}), plan, BookSettings(page_count=8), 3)
    assert new.text == "New words." and new.number == 3
