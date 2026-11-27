"""Props: OpenMoji library (4000+ drawings) + AI se bane props (JSON shapes) + kuch hath se bane (samosa...).

find("pressure cooker") -> Prop ya None
"""
import json
import re
import urllib.request
import zipfile
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
OM_DIR = ASSETS / "openmoji"
OM_JSON = ASSETS / "openmoji.json"
AI_DIR = ROOT / "props_ai"            # AI ke banaye props (git mein commit hote hain, library badhti hai)
OM_VERSION = "17.0.0"
OM_ZIP = f"https://github.com/hfg-gmuend/openmoji/releases/download/{OM_VERSION}/openmoji-618x618-color.zip"
OM_META = f"https://raw.githubusercontent.com/hfg-gmuend/openmoji/{OM_VERSION}/data/openmoji.json"

BUILTIN = {"samosa", "laddoo", "jalebi"}          # engine.py khud banata hai
KEEP_GROUPS = {"food-drink", "animals-nature", "objects", "activities", "travel-places", "extras-openmoji",
               "extras-unicode", "smileys-emotion", "people-body", "symbols"}

# Hinglish / Indian naam -> library ke naam
ALIASES = {
    "chappal": "thong sandal", "slipper": "thong sandal", "chai": "teacup without handle", "tea": "hot beverage",
    "coffee": "hot beverage", "roti": "flatbread", "chapati": "flatbread", "paratha": "flatbread",
    "dosa": "flatbread", "rice": "cooked rice", "biryani": "curry rice", "curry": "curry rice",
    "dal": "curry rice", "mobile": "mobile phone", "phone": "mobile phone", "alarm": "alarm clock",
    "clock": "alarm clock", "watch": "watch", "bat": "cricket game", "cricket": "cricket game",
    "ball": "softball", "football": "soccer ball", "laptop": "laptop", "computer": "desktop computer",
    "tv": "television", "remote": "television", "book": "closed book", "books": "books", "notebook": "notebook",
    "pen": "pen", "pencil": "pencil", "exam": "page facing up", "paper": "page facing up",
    "report card": "page facing up", "money": "banknote with dollar sign", "rupee": "money bag", "note": "banknote with dollar sign",
    "gift": "wrapped gift", "cake": "shortcake", "birthday cake": "birthday cake", "icecream": "soft ice cream",
    "ice cream": "soft ice cream", "chili": "hot pepper", "mirchi": "hot pepper", "lemon": "lemon", "nimbu": "lemon",
    "mango": "mango", "aam": "mango", "apple": "red apple", "banana": "banana", "kela": "banana", "pizza": "pizza",
    "burger": "hamburger", "fries": "french fries", "noodles": "steaming bowl", "maggi": "steaming bowl",
    "momos": "dumpling", "momo": "dumpling", "water": "glass of milk", "milk": "glass of milk",
    "bottle": "bottle with popping cork", "cooker": "cooking", "pan": "cooking", "egg": "egg", "anda": "egg",
    "dog": "dog", "cat": "cat", "billi": "cat", "kutta": "dog", "mouse": "mouse", "chuha": "mouse",
    "cockroach": "cockroach", "mosquito": "mosquito", "machhar": "mosquito", "lizard": "lizard",
    "chipkali": "lizard", "spider": "spider", "snake": "snake", "bike": "motorcycle", "car": "automobile",
    "cycle": "bicycle", "umbrella": "umbrella", "bag": "backpack", "school bag": "backpack", "glasses": "glasses",
    "shoe": "running shoe", "joota": "running shoe", "broom": "broom", "jhadu": "broom", "bucket": "bucket",
    "key": "key", "chabi": "old key", "lock": "locked", "bell": "bell", "light": "light bulb", "bulb": "light bulb",
    "candle": "candle", "diya": "diya lamp", "cracker": "firecracker", "patakha": "firecracker", "kite": "kite",
    "patang": "kite", "balloon": "balloon", "medicine": "pill", "injection": "syringe", "toothbrush": "toothbrush",
    "soap": "soap", "pillow": "bed", "fan": "wind face", "camera": "camera", "guitar": "guitar", "drum": "drum",
    "trophy": "trophy", "medal": "1st place medal", "crown": "crown", "rose": "rose", "flower": "blossom",
    "heart": "red heart", "letter": "love letter", "popcorn": "popcorn", "cookie": "cookie", "biscuit": "cookie",
    "chocolate": "chocolate bar", "candy": "candy", "lollipop": "lollipop", "juice": "beverage box",
    "cold drink": "cup with straw", "coconut": "coconut", "watermelon": "watermelon", "tarbooz": "watermelon",
    "carrot": "carrot", "onion": "onion", "pyaz": "onion", "potato": "potato", "aloo": "potato",
    "tomato": "tomato", "garlic": "garlic", "bread": "bread", "sandwich": "sandwich", "hammer": "hammer",
    "scissors": "scissors", "tooth": "tooth", "bomb": "bomb", "ghost": "ghost", "bhoot": "ghost",
    "rocket": "rocket", "plane": "airplane", "train": "locomotive", "bus": "bus", "auto": "auto rickshaw",
    "rickshaw": "auto rickshaw", "tiffin": "bento box", "lunchbox": "bento box", "plate": "fork and knife with plate",
}

GENERIC = {"with", "the", "a", "an", "of", "and", "face", "button", "sign", "symbol"}


def ensure_assets():
    """OpenMoji download (ek baar; GitHub Actions mein cache hota hai)."""
    ASSETS.mkdir(exist_ok=True)
    if not OM_JSON.exists():
        print("⬇️  OpenMoji metadata...")
        urllib.request.urlretrieve(OM_META, OM_JSON)
    if not OM_DIR.exists() or len(list(OM_DIR.glob("*.png"))) < 1000:
        print("⬇️  OpenMoji drawings (~44MB)...")
        z = ASSETS / "om.zip"
        urllib.request.urlretrieve(OM_ZIP, z)
        OM_DIR.mkdir(exist_ok=True)
        with zipfile.ZipFile(z) as f:
            f.extractall(OM_DIR)
        z.unlink()


def words(s):
    return [w for w in re.findall(r"[a-z0-9]+", str(s).lower()) if w not in GENERIC]


def slug(s):
    return "-".join(words(s))[:40] or "prop"


@lru_cache(maxsize=1)
def catalog():
    """[(annotation, set(words), hexcode)] — skin-tone variants aur flags hata ke."""
    ensure_assets()
    out = []
    for e in json.loads(OM_JSON.read_text()):
        if e.get("skintone") or e.get("group") not in KEEP_GROUPS or e.get("subgroups") == "flag":
            continue
        if not (OM_DIR / f"{e['hexcode']}.png").exists():
            continue
        ann = e["annotation"].lower()
        tags = " ".join([e.get("tags", ""), e.get("openmoji_tags", "")])
        out.append((ann, set(words(ann)), set(words(tags)), e["hexcode"]))
    return out


def ai_props():
    AI_DIR.mkdir(exist_ok=True)
    return {p.stem: p for p in AI_DIR.glob("*.json")}


def find(name):
    """Naam -> ('builtin', name) / ('openmoji', hexcode) / ('ai', path) / None"""
    q = " ".join(str(name or "").lower().split())
    if not q:
        return None
    if q in BUILTIN:
        return ("builtin", q)
    q = ALIASES.get(q, q)
    ai = ai_props()
    if slug(q) in ai:
        return ("ai", ai[slug(q)])
    cat = catalog()
    for ann, _, _, hx in cat:
        if ann == q:
            return ("openmoji", hx)
    qw = set(words(q))
    if not qw:
        return None
    best, score = None, 0.0
    for ann, aw, tw, hx in cat:
        s = 2.0 * len(qw & aw) + 0.6 * len(qw & tw)
        if s == 0:
            continue
        s -= 0.15 * len(aw - qw)                 # chhota/exact naam behtar
        if qw <= aw:
            s += 1.5
        if s > score:
            best, score = hx, s
    # kam se kam ek main shabd naam mein mile
    if best and score >= 1.6:
        return ("openmoji", best)
    return None


@lru_cache(maxsize=64)
def image(kind, ref, px):
    """RGBA image, side = px (trimmed)."""
    if kind == "openmoji":
        im = Image.open(OM_DIR / f"{ref}.png").convert("RGBA")
    elif kind == "ai":
        im = render_shapes(json.loads(Path(ref).read_text()))
    else:
        raise ValueError(kind)
    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
    im.thumbnail((px, px), Image.LANCZOS)
    return im


def render_shapes(spec, size=600):
    """AI prop: {"shapes":[{"type":"polygon","points":[[x,y],...],"fill":"#hex"}, {"type":"circle","cx","cy","r",...},
    {"type":"rect","x","y","w","h"}, {"type":"line","points":[...],"width":4}]}  coords 0..100."""
    k = size / 100
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    def col(c, default):
        c = str(c or default).strip()
        return c if re.fullmatch(r"#[0-9a-fA-F]{6}", c) else default
    for s in spec.get("shapes", [])[:60]:
        try:
            fill = col(s.get("fill"), "#cccccc")
            out = col(s.get("outline"), "#222222")
            w = int(max(2, min(12, float(s.get("width", 4)))) * k / 2)
            t = s.get("type")
            if t == "polygon":
                pts = [(float(x) * k, float(y) * k) for x, y in s["points"][:40]]
                if len(pts) >= 3:
                    d.polygon(pts, fill=fill, outline=out, width=w)
            elif t in ("circle", "ellipse"):
                cx, cy = float(s["cx"]) * k, float(s["cy"]) * k
                rx = float(s.get("rx", s.get("r", 10))) * k
                ry = float(s.get("ry", s.get("r", 10))) * k
                d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=fill, outline=out, width=w)
            elif t == "rect":
                x, y = float(s["x"]) * k, float(s["y"]) * k
                d.rounded_rectangle([x, y, x + float(s["w"]) * k, y + float(s["h"]) * k], radius=int(3 * k),
                                    fill=fill, outline=out, width=w)
            elif t == "line":
                pts = [(float(x) * k, float(y) * k) for x, y in s["points"][:40]]
                if len(pts) >= 2:
                    d.line(pts, fill=col(s.get("color", s.get("fill")), "#222222"), width=w, joint="curve")
        except (KeyError, ValueError, TypeError):
            continue
    return im


def save_ai_prop(name, spec):
    AI_DIR.mkdir(exist_ok=True)
    path = AI_DIR / f"{slug(name)}.json"
    path.write_text(json.dumps({"name": name, "shapes": spec.get("shapes", [])}, ensure_ascii=False, indent=1))
    image.cache_clear()
    return path


def sample_names(n=150):
    """Grok ko dikhane ke liye kuch naam."""
    names = sorted(set(list(ALIASES.keys()) + list(BUILTIN)))
    return names[:n]


if __name__ == "__main__":
    import sys
    for q in sys.argv[1:] or ["samosa", "chappal", "pressure cooker", "alarm clock", "cockroach", "laptop",
                              "mango", "cricket bat", "report card", "golgappa", "umbrella", "rubber duck"]:
        r = find(q)
        ann = next((a for a, _, _, h in catalog() if r and h == r[1]), "") if r and r[0] == "openmoji" else ""
        print(f"{q:18} -> {r} {ann}")
