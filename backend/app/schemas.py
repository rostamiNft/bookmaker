"""Pydantic contracts: user input (BookSettings) and the structured story plan
shared by every stage (StoryPlan / CharacterSpec / PageSpec)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

AgeGroup = Literal["3-5", "6-8", "9-12"]
Language = Literal["en", "fa"]
RTL_LANGUAGES = {"fa", "ar", "he", "ur"}

ILLUSTRATION_STYLES = {
    "watercolor": "soft watercolor picture-book illustration, gentle washes, visible paper texture, warm light",
    "flat_vector": "clean flat vector children's illustration, simple shapes, bold friendly colors",
    "crayon": "hand-drawn crayon and colored-pencil children's illustration, textured strokes",
    "paper_cutout": "layered paper-cutout collage illustration, soft drop shadows, handmade feel",
    "gouache": "rich gouache storybook painting, matte colors, cozy atmosphere",
}


class BookSettings(BaseModel):
    title: str = Field("", max_length=200, description="Empty = let the engine suggest a title")
    topic: str = Field("", max_length=600)
    moral: str = Field("", max_length=300)
    age_group: AgeGroup = "6-8"
    language: Language = "en"
    page_count: int = Field(12, ge=4, le=40)
    complexity: Literal["simple", "medium", "rich"] = "medium"
    narration: Literal["third_person", "first_person"] = "third_person"
    illustration_style: Literal["watercolor", "flat_vector", "crayon", "paper_cutout", "gouache"] = "watercolor"
    aspect_ratio: Literal["4:3", "3:2", "1:1", "3:4", "16:9"] = "4:3"
    page_size: Literal["A4", "A5", "Letter", "Square8"] = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    main_characters: int = Field(2, ge=1, le=4)
    hero_name: str = Field("", max_length=40)
    text_amount: Literal["short", "medium", "long"] = "medium"
    fantasy_level: int = Field(2, ge=0, le=3)
    humor_level: int = Field(1, ge=0, le=3)
    ending: Literal["happy", "calm", "surprise", "open"] = "happy"
    cover_style: Literal["illustrated", "framed", "minimal"] = "illustrated"
    author: str = Field("", max_length=120)
    publisher: str = Field("", max_length=120)
    page_numbers: bool = True
    back_cover: bool = True
    text_in_image: bool = False
    output_profile: Literal["screen", "print"] = "screen"
    seed: Optional[int] = Field(None, ge=0, le=2**31 - 1)
    llm_provider_id: str = ""
    image_provider_id: str = ""
    vision_provider_id: str = ""
    max_image_attempts: int = Field(3, ge=1, le=6)
    max_story_attempts: int = Field(3, ge=1, le=5)
    budget_usd: float = Field(0.0, ge=0, le=1000, description="0 = no paid-API budget limit enforced beyond estimate confirmation")
    # workflow
    review_outline: bool = Field(False, description="Stop after the story plan so the text can be reviewed before illustration")
    reference_mode: Literal["multi_view", "single"] = "multi_view"  # character sheets: poses+expressions / turnaround prompts
    cast_reference: bool = True  # also send the cast line-up (relative sizes) to AI image models
    # layout & print
    layout_style: Literal["classic", "full_bleed"] = "classic"  # full_bleed: text typeset on a panel over the illustration
    bleed_mm: float = Field(0.0, ge=0, le=6)
    crop_marks: bool = False
    color_mode: Literal["rgb", "cmyk"] = "rgb"
    # narration
    narration_audio: bool = False
    tts_provider_id: str = ""
    narration_voice: str = Field("", max_length=80)
    narration_speed: float = Field(1.0, ge=0.5, le=2.0)
    # typography
    text_align: Literal["start", "justify"] = Field(
        "start", description="justify: stretch story lines to the text box (kashida for Persian/Arabic, word spaces otherwise)")
    hyphenation: bool = Field(False, description="Break long words at syllable boundaries (Latin scripts, needs pyphen)")
    shaping: Literal["auto", "harfbuzz", "basic"] = Field(
        "auto", description="auto: HarfBuzz when installed; harfbuzz: real OpenType shaping; basic: built-in presentation forms")
    kashida: bool = Field(True, description="Allow tatweel (kashida) stretching when justifying Arabic-script text")
    speech_bubbles: bool = Field(False, description="Draw the page text in vector speech bubbles over the illustration")
    # print standards
    print_standard: Literal["none", "pdfx4"] = Field(
        "none", description="pdfx4: add PDF/X-4 output intent, XMP and TrimBox (forces CMYK colour)")
    icc_profile: str = Field("", max_length=300, description="Path to a CMYK ICC profile; empty = bundled ISOcoated_v2_300 (CC0)")
    # per-character LoRA
    lora_training: bool = Field(False, description="Also export a LoRA training dataset (kohya/ai-toolkit format) per character")

    @field_validator("title", "topic", "moral", "hero_name", "author", "publisher")
    @classmethod
    def _strip(cls, v: str) -> str:
        # strip control characters
        return "".join(ch for ch in v if ch == "\n" or ch >= " ").strip()


class Appearance(BaseModel):
    species: str = "human"  # human | fox | owl | rabbit | hedgehog | puppy | squirrel | cat | bear
    skin: str = ""  # hex colour (skin or fur)
    hair_color: str = ""
    hair_style: str = ""  # short | curly | ponytail | braids | bun | spiky | none
    eye_color: str = "#3b2a20"
    face: str = ""
    body: str = ""
    height: float = 1.0  # relative height (story data)
    scale: float = Field(1.0, ge=0.5, le=1.8)  # user size override on top of the species proportions


class Outfit(BaseModel):
    top: str = ""
    top_color: str = ""
    bottom: str = ""
    bottom_color: str = ""
    accessory: str = ""  # scarf | hat | backpack | bow | glasses | none
    accessory_color: str = ""
    shoes_color: str = ""


class CharacterSpec(BaseModel):
    id: str
    name: str
    role: str = "supporting"  # protagonist | companion | supporting
    pronoun: Literal["she", "he", "they", "it"] = "they"
    age: str = ""
    description: str = ""  # English visual description used in image prompts
    personality: str = ""
    appearance: Appearance = Appearance()
    outfit: Outfit = Outfit()
    distinctive: list[str] = []
    expressions: list[str] = []
    movement: str = ""
    palette: list[str] = []
    relationships: dict[str, str] = {}
    # per-character LoRA (weights are trained elsewhere; these fields drive prompts + dataset export)
    lora_name: str = Field("", max_length=120, description="File name (without extension) of a trained LoRA for this character")
    lora_weight: float = Field(0.8, ge=0.0, le=1.5, description="Default LoRA strength used in prompts")
    lora_trigger: str = Field("", max_length=200, description="Trigger word that activates the LoRA")


class LocationSpec(BaseModel):
    id: str
    name: str
    kind: str = "meadow"  # meadow | forest | river | hill | village | garden | bedroom | yard | field
    description: str = ""
    palette: list[str] = []


class PropSpec(BaseModel):
    id: str
    name: str
    description: str = ""
    color: str = ""


class Scene(BaseModel):
    location: str = "meadow"  # location kind
    time: Literal["day", "sunset", "night"] = "day"
    characters: list[str] = []
    props: list[str] = []
    mood: str = "happy"
    poses: dict[str, str] = {}
    expressions: dict[str, str] = {}
    shot: Literal["wide", "medium", "close"] = "medium"
    weather: str = ""


class PageSpec(BaseModel):
    number: int
    beat: str = "story"  # setup | inciting | rising | climax | resolution | ending ...
    text: str
    image_description: str = ""
    characters_present: list[str] = []
    scene: Scene = Scene()


class StoryPlan(BaseModel):
    title: str
    subtitle: str = ""
    summary: str = ""
    moral: str = ""
    theme: str = ""
    language: str = "en"
    age_group: str = "6-8"
    characters: list[CharacterSpec]
    locations: list[LocationSpec] = []
    props: list[PropSpec] = []
    arc: dict[str, list[int]] = {}
    pages: list[PageSpec]
    back_cover_blurb: str = ""
    engine: str = ""  # which engine produced it
