"""Real HTTP mock services used by integration tests: an OpenAI-compatible API
(chat + images), an Ollama API and a ComfyUI API. Behaviour can be switched
per test (inject 429/500 errors, malformed JSON, ...)."""
from __future__ import annotations

import base64
import io
import json
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageDraw

STORY = {
    "title": "Mila Builds a Boat",
    "summary": "Mila the mouse wants to visit her grandmother across the pond. With help from her friend Tock the frog, she builds a little boat.",
    "moral": "Patience and teamwork turn big problems into small ones.",
    "back_cover_blurb": "Can a tiny mouse cross a big pond? Mila is about to find out.",
    "characters": [
        {"id": "hero", "name": "Mila", "latin_name": "Mila", "role": "protagonist", "pronoun": "she", "age": "7",
         "description": "a small grey mouse girl with a yellow raincoat and a red scarf", "species": "human", "skin": "#c98e63",
         "hair_color": "#2b1d14", "hair_style": "bun", "top": "hoodie", "top_color": "#f4d03f", "bottom": "trousers", "bottom_color": "#3d8bd9",
         "accessory": "scarf", "accessory_color": "#e94f4f", "distinctive": ["yellow hoodie", "red scarf"]},
        {"id": "comp", "name": "Tock", "latin_name": "Tock", "role": "companion", "pronoun": "he", "age": "young",
         "description": "a cheerful fox with a teal scarf", "species": "fox", "skin": "#e07b39", "accessory": "scarf", "accessory_color": "#2bb3b1",
         "distinctive": ["teal scarf"]},
    ],
    "locations": [{"id": "pond", "name": "the pond", "kind": "river", "description": "a wide calm pond"}],
    "pages": [],
}
_TEXTS = [
    ("setup", "Mila lived in a cosy burrow beside a wide, shining pond. Every morning she waved to the ducks and dreamed of the far shore.", "meadow"),
    ("setup", "Her best friend Tock the fox lived under the willow tree. Tock knew every stone and reed around the water.", "meadow"),
    ("inciting", "One day a letter arrived from Grandma: she was ill and wanted Mila to visit, but Grandma lived on the other side of the pond.", "meadow"),
    ("rising", "Mila tried to walk around the pond, but the path was flooded. 'We need a boat,' said Tock, scratching his ear.", "river"),
    ("rising", "They gathered sticks, a big leaf for a sail and a length of string. Building was slow, and the first raft fell apart.", "river"),
    ("rising", "Mila felt like giving up. Tock sat beside her. 'Let's try again, more carefully this time,' he said kindly.", "river"),
    ("climax", "The second boat floated! Halfway across, a gust of wind spun them round, and Mila held the leaf-sail with all her strength.", "river"),
    ("resolution", "At last the boat bumped onto the far shore. Grandma was waiting with warm soup and the biggest hug in the world.", "meadow"),
    ("ending", "That night Mila and Tock sailed home under the stars, already planning their next voyage.", "meadow"),
]


def story(n_pages: int):
    s = json.loads(json.dumps(STORY))
    for i in range(n_pages):
        role, text, loc = _TEXTS[i % len(_TEXTS)]
        if i == n_pages - 1:
            role, text, loc = _TEXTS[-1]
        elif i >= len(_TEXTS) - 1:
            text = text + f" (part {i + 1})"
        s["pages"].append({"number": i + 1, "role": role, "text": text,
                           "image_description": f"Mila and Tock at the {loc}, page {i + 1}", "characters_present": ["hero", "comp"],
                           "location": loc, "time": "day" if i < n_pages - 1 else "night", "mood": "happy",
                           "poses": {"hero": "wave", "comp": "stand"}, "expressions": {"hero": "happy"}, "shot": "medium", "props": ["stones"]})
    return s


class State:
    def __init__(self):
        self.reset()

    def reset(self):
        self.calls = []
        self.chat_fail_first = 0  # return malformed JSON for the first N plan calls
        self.image_errors = []  # queue of status codes to return before succeeding
        self.always_500 = False
        self.pages = 8


STATE = State()
app = FastAPI()


def png(w: int, h: int, seed: int = 0) -> bytes:
    img = Image.new("RGB", (w, h), (120 + seed % 100, 170, 220))
    d = ImageDraw.Draw(img)
    for i in range(8):
        x = (seed * 37 + i * 97) % w
        d.ellipse([x, h // 3 + i * 10, x + w // 6, h // 3 + i * 10 + h // 6], fill=((seed * 13 + i * 40) % 255, 120, 90))
    d.rectangle([0, int(h * 0.7), w, h], fill=(90, 160, 80))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _image_gate():
    if STATE.always_500:
        return JSONResponse({"error": "boom"}, status_code=500)
    if STATE.image_errors:
        code = STATE.image_errors.pop(0)
        return JSONResponse({"error": "busy"}, status_code=code, headers={"Retry-After": "0"} if code == 429 else {})
    return None


@app.get("/v1/models")
def models(request: Request):
    if request.headers.get("authorization") == "Bearer bad-key":
        return JSONResponse({"error": "invalid key"}, status_code=401)
    return {"data": [{"id": "mock-llm"}, {"id": "mock-image"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    STATE.calls.append(("chat", body.get("model")))
    if request.headers.get("authorization") == "Bearer bad-key":
        return JSONResponse({"error": "invalid key"}, status_code=401)
    content = body["messages"][-1]["content"]
    if isinstance(content, list):  # vision request
        return {"choices": [{"message": {"content": json.dumps({"relevance": 4, "characters_visible": ["Mila"], "has_text_or_letters": False,
                                                                  "anatomy_problems": False, "notes": "fine"})}}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 40}}
    if "Rewrite page" in content:
        out = {"text": "Mila smiled and tied the last knot. The little boat was ready at last.", "image_description": "Mila tying a knot on a small boat"}
        if "SHORTER" in content:
            out["text"] = "Mila tied the last knot."
    else:
        import re
        m = re.search(r"Exactly (\d+) story pages|exactly (\d+) pages", content)
        n = int(next(g for g in m.groups() if g)) if m else STATE.pages
        if STATE.chat_fail_first > 0:
            STATE.chat_fail_first -= 1
            return {"choices": [{"message": {"content": "Sure! Here is your story: {not valid json"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
        out = story(n)
    return {"model": body.get("model"), "choices": [{"message": {"content": json.dumps(out)}}],
            "usage": {"prompt_tokens": 1500, "completion_tokens": 2200}}


@app.post("/v1/images/generations")
async def images(request: Request):
    body = await request.json()
    STATE.calls.append(("image", body.get("size")))
    gate = _image_gate()
    if gate:
        return gate
    w, h = [int(x) for x in body["size"].split("x")]
    return {"data": [{"b64_json": base64.b64encode(png(w, h, len(STATE.calls))).decode()}]}


@app.post("/v1/images/edits")
async def edits(prompt: str = Form(...), size: str = Form(...), model: str = Form(""), n: str = Form("1"), request: Request = None):
    form = await request.form()
    refs = form.getlist("image[]")
    mask = form.get("mask")
    STATE.calls.append(("edit", size, len(refs)) if mask is None else ("edit_mask", size, len(refs)))
    gate = _image_gate()
    if gate:
        return gate
    w, h = [int(x) for x in size.split("x")]
    return {"data": [{"b64_json": base64.b64encode(png(w, h, len(STATE.calls))).decode()}]}


# ---- Ollama
@app.get("/api/tags")
def tags():
    return {"models": [{"name": "llama3.1:8b"}]}


@app.post("/api/generate")
async def ollama_generate(request: Request):
    body = await request.json()
    STATE.calls.append(("ollama", body.get("model")))
    return {"response": json.dumps(story(STATE.pages)) if body.get("format") == "json" else "Hello from Ollama",
            "prompt_eval_count": 900, "eval_count": 1800}


# ---- ComfyUI
_COMFY = {}


@app.get("/system_stats")
def stats():
    return {"system": {"comfyui_version": "mock"}, "devices": [{"name": "cpu", "vram_free": 0}]}


_UPLOADS = {}


@app.post("/upload/image")
async def upload(image: UploadFile = File(...), overwrite: str = Form("true")):
    data = await image.read()
    try:  # real ComfyUI stores any file; remember the size of real images (inpaint input)
        _UPLOADS[image.filename] = Image.open(io.BytesIO(data)).size
    except Exception:
        pass
    STATE.calls.append(("comfy_upload", image.filename))
    return {"name": image.filename}


@app.post("/prompt")
async def prompt(request: Request):
    body = await request.json()
    wf = body["prompt"]
    loads = [n["inputs"]["image"] for n in wf.values() if n.get("class_type") == "LoadImage"]
    if "width" in wf["5"]["inputs"]:
        w, h = wf["5"]["inputs"]["width"], wf["5"]["inputs"]["height"]
    elif loads and loads[0] in _UPLOADS:  # inpaint workflow: output has the input image size
        w, h = _UPLOADS[loads[0]]
        STATE.calls.append(("comfy_inpaint", len(loads)))
    else:
        return JSONResponse({"error": "bad workflow", "node_errors": {"5": "missing size"}}, status_code=400)
    pid = f"p{len(_COMFY) + 1}"
    _COMFY[pid] = {"w": w, "h": h, "t": time.time(), "text": wf["6"]["inputs"]["text"]}
    STATE.calls.append(("comfy_prompt", w, h, isinstance(w, int)))
    return {"prompt_id": pid, "number": 1, "node_errors": {}}


@app.get("/history/{pid}")
def history(pid: str):
    job = _COMFY.get(pid)
    if not job or time.time() - job["t"] < 0.2:
        return {}
    return {pid: {"status": {"status_str": "success"}, "outputs": {"9": {"images": [{"filename": f"{pid}.png", "subfolder": "", "type": "output"}]}}}}


@app.get("/view")
def view(filename: str, subfolder: str = "", type: str = "output"):
    job = _COMFY[filename.split(".")[0]]
    return Response(png(job["w"], job["h"], 5), media_type="image/png")


class Server:
    def __init__(self, port):
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.state = STATE
        cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def stop(self):
        self.server.should_exit = True


def start() -> Server:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = Server(port)
    srv.thread.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return srv
