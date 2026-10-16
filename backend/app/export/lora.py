"""Per-character LoRA: dataset export and prompt integration.

Honest scope: **this repository does not train LoRAs** - that needs a GPU and a
trainer (kohya ss / ai-toolkit, both open source). What it does:

1. builds a ready-to-train dataset for every character: the reference sheets and the
   page illustrations the character appears in, with kohya/ai-toolkit caption files
   that start with a trigger word and repeat the Character Bible description;
2. ships training configs (kohya TOML + ai-toolkit YAML) with conservative values;
3. once a LoRA is trained elsewhere, ``lora_token()`` injects ``<lora:name:weight>``
   and the trigger word into the prompts so every page uses it.
"""
from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image

TRIGGER_DEFAULT = "bkchar"
MAX_SIDE = 1024


def trigger_word(spec: dict, fallback_name: str = "") -> str:
    """Stable trigger word: the user's, else a sanitised version of the character name."""
    t = (spec or {}).get("lora_trigger") or ""
    if t.strip():
        return t.strip().split()[0]
    base = re.sub(r"[^a-z0-9]", "", (fallback_name or (spec or {}).get("name", "") or "").lower())
    return f"{TRIGGER_DEFAULT}{base[:12]}" if base else TRIGGER_DEFAULT


def lora_token(spec: dict) -> str:
    """``<lora:name:weight> trigger`` for A1111 / Forge / SD.Next / ComfyUI text encoders."""
    name = (spec or {}).get("lora_name", "").strip()
    if not name:
        return ""
    w = float((spec or {}).get("lora_weight", 0.8) or 0.8)
    trig = trigger_word(spec)
    return f"<lora:{Path(name).stem}:{w:g}> {trig}".strip()


def caption(spec: dict, style: str, extra: str = "") -> str:
    """Caption file content: trigger word first (kohya convention)."""
    trig = trigger_word(spec)
    bits = [f"{trig}, children's picture book character"]
    desc = (spec or {}).get("description", "").strip()
    if desc:
        bits.append(desc.rstrip("."))
    dist = ", ".join((spec or {}).get("distinctive", []) or [])
    if dist:
        bits.append(dist)
    pal = ", ".join((spec or {}).get("palette", [])[:3] or [])
    if pal:
        bits.append(f"colours {pal}")
    if extra:
        bits.append(extra)
    bits.append(f"{style} illustration")
    return ", ".join(b for b in bits if b)


def _png(im: Image.Image, max_side: int = MAX_SIDE) -> bytes:
    im = im.convert("RGB")
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_dataset(characters: list[dict], samples: dict[str, list[Path]], style: str, out: Path,
                  repeats: int = 10) -> dict:
    """characters: Character Bible entries (dicts). samples: {char_id: [image paths]}.

    Writes a ZIP with ``10_<trigger> <class>/`` folders, one caption file per image,
    plus ready-to-edit kohya and ai-toolkit configs and a README."""
    out.parent.mkdir(parents=True, exist_ok=True)
    report: dict = {"characters": [], "images": 0, "bytes": 0}
    tmp = out.with_suffix(".tmp.zip")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for spec in characters:
            cid = spec.get("id") or spec.get("name") or "char"
            trig = trigger_word(spec, spec.get("name", ""))
            paths = [p for p in samples.get(cid, []) if p and Path(p).is_file()]
            if not paths:
                continue
            folder = f"img/{10 if repeats < 10 else repeats}_{trig} {spec.get('species', 'character')}"
            n = 0
            for i, p in enumerate(paths):
                try:
                    with Image.open(p) as im:
                        data = _png(im)
                except Exception:
                    continue
                stem = f"{trig}_{i + 1:03d}"
                z.writestr(f"{folder}/{stem}.png", data)
                z.writestr(f"{folder}/{stem}.txt", caption(spec, style))
                n += 1
            if not n:
                continue
            report["images"] += n
            report["characters"].append({"id": cid, "name": spec.get("name", ""), "trigger": trig,
                                         "images": n, "lora_name": (spec.get("lora_name") or f"{trig}.safetensors")})
        z.writestr("kohya_config.toml", KOHYA_TOML.render(characters=report["characters"], trigger=trig_word(report)))
        z.writestr("ai_toolkit.yaml", AI_TOOLKIT_YAML.render(characters=report["characters"], trigger=trig_word(report)))
        z.writestr("README.md", README.render(characters=report["characters"], style=style))
        z.writestr("dataset.json", json.dumps(report, ensure_ascii=False, indent=1))
    tmp.replace(out)
    report["bytes"] = out.stat().st_size
    report["file"] = str(out)
    return report


def trig_word(report: dict) -> str:
    return report["characters"][0]["trigger"] if report.get("characters") else TRIGGER_DEFAULT


class _Tpl:
    def __init__(self, text: str):
        self.text = text

    def render(self, **kw) -> str:
        out = self.text
        for k, v in kw.items():
            out = out.replace("{{" + k + "}}", json.dumps(v) if isinstance(v, (list, dict)) else str(v))
        return out


KOHYA_TOML = _Tpl("""# kohya_ss / sd-scripts LoRA training config (edit and run on a machine with a GPU).
# Bookmaker only prepares the dataset and this config: training itself needs
#   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
#   git clone https://github.com/kohya-ss/sd-scripts && pip install -r requirements.txt
[model]
pretrained_model_name_or_path = "runwayml/stable-diffusion-v1-5"   # or any SDXL checkpoint
v2 = false
v_parameterization = false
save_precision = "fp16"

[dataset]
resolution = 768
batch_size = 2
enable_bucket = true
min_bucket_reso = 512
max_bucket_reso = 1024

[[datasets.subsets]]
image_dir = "img"
num_repeats = 10
caption_extension = ".txt"
shuffle_caption = false
keep_tokens = 1          # keep the trigger word at the front of every caption

[training]
output_dir = "output"
output_name = "{{trigger}}"
max_train_steps = 1200
learning_rate = 1e-4
lr_scheduler = "cosine"
optimizer_type = "AdamW8bit"
network_module = "networks.lora"
network_dim = 16
network_alpha = 8
mixed_precision = "fp16"
gradient_checkpointing = true
clip_skip = 2
seed = 42

[additional]
# The trigger words used in the captions:
#   {{characters}}
""")

AI_TOOLKIT_YAML = _Tpl("""# ostris/ai-toolkit config (alternative trainer, Apache-2.0)
job: extension
config:
  name: "{{trigger}}_lora"
  process:
    - type: sd_trainer
      training_folder: output
      device: cuda:0
      trigger_word: "{{trigger}}"
      network:
        type: lora
        linear: 16
        linear_alpha: 8
      save:
        dtype: float16
        save_every: 250
        max_step_saves_to_keep: 4
      datasets:
        - folder_path: img
          caption_ext: txt
          resolution: [512, 768, 1024]
          cache_latents_to_disk: true
      train:
        batch_size: 2
        steps: 1200
        gradient_accumulation: 1
        lr: 1e-4
        optimizer: adamw8bit
        timestep_type: weighted
        dtype: bf16
model:
  name_or_path: runwayml/stable-diffusion-v1-5
  is_v2: false
  quantize: true
sample:
  prompts:
    - "{{trigger}}, children's book illustration, standing in a meadow"
  every: 250
  width: 768
  height: 768
""")

README = _Tpl("""# LoRA training dataset (generated by Bookmaker)

Style: {{style}}

What is in this ZIP

* ``img/<repeats>_<trigger> <class>/`` - training images (reference sheets and page
  illustrations the character appears in), one ``.txt`` caption per image. The caption
  always starts with the **trigger word**, then repeats the Character Bible description,
  so the LoRA learns the character's look, not the background.
* ``kohya_config.toml`` - config for kohya ss / sd-scripts (MIT licensed).
* ``ai_toolkit.yaml`` - config for ostris ai-toolkit (Apache-2.0).
* ``dataset.json`` - what was exported, for your own records.

Characters

{{characters}}

How to train (on a machine with an NVIDIA/AMD GPU)

1. ``pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121``
2. kohya:  ``git clone https://github.com/kohya-ss/sd-scripts`` and
   ``python sdxl_train_network.py --config_file kohya_config.toml``
   (or ``train_network.py`` for SD 1.5)
   ai-toolkit: follow its README and point it at ``ai_toolkit.yaml``.
3. Copy the resulting ``.safetensors`` into your Stable Diffusion ``models/Lora`` folder.
4. In Bookmaker, open the character and set **LoRA name** (e.g. ``sara_lora``) and the
   **trigger word**. Every new prompt then starts with ``<lora:sara_lora:0.8> <trigger>``.

Licence reminder: check the licence of the base checkpoint you fine-tune and of the
images you train on before publishing or selling a model.
""")
