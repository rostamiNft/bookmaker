"""Character Bible construction for the offline engine (deterministic per seed)."""
from __future__ import annotations

import random

from ..schemas import Appearance, CharacterSpec, Outfit

NAMES = {
    "en": {"she": ["Luna", "Nora", "Zara", "Ruby", "Mia", "Ivy", "Leila", "Ada"],
           "he": ["Milo", "Theo", "Finn", "Omar", "Leo", "Sami", "Kai", "Arlo"]},
    "fa": {"she": [("سارا", "Sara"), ("نیلوفر", "Niloufar"), ("آوا", "Ava"), ("ترانه", "Taraneh"), ("مینا", "Mina"), ("یاسمن", "Yasaman")],
           "he": [("کیان", "Kian"), ("آرش", "Arash"), ("بردیا", "Bardia"), ("امید", "Omid"), ("پارسا", "Parsa"), ("سامان", "Saman")]},
}

SPECIES = {
    #            en name   fa name (latin)            fur        en desc                          fa desc
    "fox":      ("Pip",     ("فلفل", "Felfel"),       "#e07b39", "a little red fox",              "روباه کوچولوی نارنجی"),
    "rabbit":   ("Clover",  ("پشمک", "Pashmak"),      "#f1ece4", "a fluffy white rabbit",         "خرگوش سفید و پشمالو"),
    "hedgehog": ("Bramble", ("تیغی", "Tighi"),        "#8a6a4f", "a small, prickly hedgehog",     "جوجه‌تیغی کوچولو"),
    "squirrel": ("Nutmeg",  ("فندق", "Fandogh"),      "#b5652b", "a quick little squirrel",       "سنجاب کوچولوی چابک"),
    "puppy":    ("Biscuit", ("کلوچه", "Koloocheh"),   "#b8834f", "a playful brown puppy",         "توله‌سگ قهوه‌ای بازیگوش"),
    "cat":      ("Mittens", ("ملوس", "Malous"),       "#9aa0a8", "a gray cat with soft white paws", "گربه‌ی خاکستری با پنجه‌های سفید و نرم"),
    "owl":      ("Owl",     ("جغد", "Joghd"),         "#8b6b4a", "a wise old owl",                "جغد پیر و دانا"),
}

FUR_NAMES = {"fox": "orange-red", "rabbit": "snowy white", "hedgehog": "chestnut brown", "squirrel": "russet",
             "puppy": "caramel brown", "cat": "soft gray", "owl": "speckled brown"}

SKIN = [("#f6d5b8", "fair"), ("#eabf98", "light peach"), ("#c98e63", "warm tan"), ("#a86b45", "light brown"), ("#7a4a2e", "deep brown")]
HAIR = [("#2b1d14", "dark brown"), ("#5a3825", "chestnut brown"), ("#b5651d", "auburn"), ("#e2b65a", "golden blonde"), ("#151515", "black")]
CLOTH = [("#e94f4f", "red"), ("#f2a33a", "orange"), ("#f4d03f", "yellow"), ("#4caf7a", "green"), ("#3d8bd9", "blue"),
         ("#8e6cc9", "purple"), ("#ef7fb0", "pink"), ("#2bb3b1", "teal")]
HAIR_STYLES = {"she": ["ponytail", "braids", "bun", "curly", "bob"], "he": ["short", "curly", "spiky", "short"]}
TOPS = {"she": ["dress", "t-shirt", "hoodie", "overalls"], "he": ["t-shirt", "hoodie", "overalls", "sweater"]}
ACCESSORIES = ["scarf", "backpack", "hat", "bow", "glasses", "scarf"]


def _cname(hex_: str, table) -> str:
    for h, n in table:
        if h == hex_:
            return n
    return hex_


def make_child(rng: random.Random, cid: str, role: str, pronoun: str, name: str, age: str) -> CharacterSpec:
    skin = rng.choice(SKIN)
    hair = rng.choice(HAIR)
    style = rng.choice(HAIR_STYLES[pronoun])
    top_c, bottom_c, acc_c = rng.sample(CLOTH, 3)
    top = rng.choice(TOPS[pronoun])
    acc = rng.choice([a for a in ACCESSORIES if not (a == "bow" and pronoun == "he")])
    shoes = rng.choice([("#5a3825", "brown"), ("#e94f4f", "red"), ("#3d8bd9", "blue"), ("#f4f4f4", "white")])
    kid = "girl" if pronoun == "she" else "boy"
    bottom = "" if top in ("dress", "overalls") else rng.choice(["shorts", "trousers", "skirt" if pronoun == "she" else "trousers"])
    outfit_txt = f"a {top_c[1]} {top}" + (f" and {bottom_c[1]} {bottom}" if bottom else "")
    desc = (f"a {age}-year-old {kid} with {skin[1]} skin, {hair[1]} {style} hair, round friendly face, big dark eyes, "
            f"wearing {outfit_txt}, a {acc_c[1]} {acc} and {shoes[1]} shoes")
    return CharacterSpec(
        id=cid, name=name, role=role, pronoun=pronoun, age=age, description=desc,
        appearance=Appearance(species="human", skin=skin[0], hair_color=hair[0], hair_style=style, face="round, rosy cheeks", body="small child", height=1.0),
        outfit=Outfit(top=top, top_color=top_c[0], bottom=bottom, bottom_color=bottom_c[0], accessory=acc, accessory_color=acc_c[0], shoes_color=shoes[0]),
        distinctive=[f"{acc_c[1]} {acc}", f"{hair[1]} {style} hair", f"{top_c[1]} {top}"],
        expressions=["happy", "curious", "worried", "determined", "surprised"],
        movement="quick, bouncy steps",
        palette=[skin[0], hair[0], top_c[0], acc_c[0]] + ([bottom_c[0]] if bottom else []),
    )


def make_animal(rng: random.Random, cid: str, role: str, species: str, name: str, pronoun: str) -> CharacterSpec:
    _, _, fur, en_desc, _ = SPECIES[species]
    acc_c = rng.choice(CLOTH)
    acc = "scarf"
    if species == "owl":
        acc, acc_c = "glasses", ("#444444", "dark round")
    fur_name = FUR_NAMES.get(species, "brown")
    desc = (f"{en_desc} with {fur_name} {'feathers' if species == 'owl' else 'fur'}, big shiny dark eyes, "
            f"{'wearing small ' + acc_c[1] + ' glasses' if acc == 'glasses' else 'wearing a small ' + acc_c[1] + ' scarf'}")
    return CharacterSpec(
        id=cid, name=name, role=role, pronoun=pronoun, age="young" if role == "companion" else "old",
        description=desc,
        appearance=Appearance(species=species, skin=fur, eye_color="#2a1c12", body=f"small {species}", height=0.7 if species != "owl" else 0.6),
        outfit=Outfit(accessory=acc, accessory_color=acc_c[0]),
        distinctive=[f"{acc_c[1]} {acc}", f"{species} shape"],
        expressions=["happy", "curious", "surprised", "kind"],
        movement={"rabbit": "hops", "fox": "trots lightly", "hedgehog": "waddles", "squirrel": "scampers", "puppy": "bounds", "cat": "pads softly", "owl": "glides"}.get(species, "walks"),
        palette=[fur, acc_c[0]],
    )


def make_elder(rng: random.Random, cid: str, name: str, pronoun: str) -> CharacterSpec:
    skin = rng.choice(SKIN)
    top_c = rng.choice(CLOTH)
    desc = (f"a kind elderly {'woman' if pronoun == 'she' else 'man'} with {skin[1]} skin, white hair"
            f"{'' if pronoun == 'she' else ' and a white beard'}, round glasses and a {top_c[1]} cardigan")
    return CharacterSpec(
        id=cid, name=name, role="supporting", pronoun=pronoun, age="old", description=desc,
        appearance=Appearance(species="human_old", skin=skin[0], hair_color="#e8e8e8", hair_style="bun" if pronoun == "she" else "beard", height=1.25),
        outfit=Outfit(top="cardigan", top_color=top_c[0], bottom="trousers", bottom_color="#6b5b4b", accessory="glasses", accessory_color="#444444", shoes_color="#5a3825"),
        distinctive=["white hair", "round glasses", f"{top_c[1]} cardigan"], expressions=["kind", "happy"], movement="slow and gentle",
        palette=[skin[0], "#e8e8e8", top_c[0]],
    )
