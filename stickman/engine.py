"""Stickman silent-comedy engine v2: story JSON -> Shorts video (1080x1920).
Koi dialogue nahi — sirf expressions, expression-awaazein, cartoon SFX, music.
Characters (1-2), scenes, ~40 actions, props (OpenMoji 4000+ / AI / built-in).

    .venv/bin/python engine.py stories/aakhri_samosa.json out/video.mp4
    .venv/bin/python engine.py stories/aakhri_samosa.json --preview
"""
import argparse
import asyncio
import glob
import hashlib
import json
import math
import os
import random
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import props as PR

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
CACHE = ROOT / "cache"
W, H, FPS, S = 1080, 1920, 30, 2
GROUND = 1500
SR = 44100
DUR = 30.0

HEAD_R, NECK, TORSO, SHOULDER = 95, 18, 230, 200
UPPER, LOWER, THIGH, SHIN = 140, 135, 150, 150
LINE = 15
INK = (25, 25, 25)

TABLE_X = 560
HOME_A, HOME_B = 340, 830
PROP_ON_TABLE = (790.0, 1128.0)
GLASS_ON_TABLE = (960.0, 1125.0)


def _find_font():
    env = os.environ.get("STICKMAN_FONT")
    if env:
        return env, 0
    if Path("/System/Library/Fonts/Kohinoor.ttc").exists():
        return "/System/Library/Fonts/Kohinoor.ttc", 3
    for pat in ("/usr/share/fonts/**/NotoSansDevanagari-Bold.ttf", "/usr/share/fonts/**/NotoSansDevanagari*Bold*.ttf",
                "/usr/share/fonts/**/*Devanagari*Bold*.tt*", "/usr/share/fonts/**/*Devanagari*.tt*"):
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            return hits[0], 0
    raise SystemExit("Devanagari font nahi mila (Linux: sudo apt install fonts-noto-core)")


FONT, FONT_IDX = _find_font()
_FONTS = {}


def font(size):
    size = max(8, int(size))
    if size not in _FONTS:
        _FONTS[size] = ImageFont.truetype(FONT, size, index=FONT_IDX)
    return _FONTS[size]


# ═════════════════════════ keyframe tracks ═════════════════════════
def ease(u, kind):
    u = min(1.0, max(0.0, u))
    if kind == "lin":
        return u
    if kind == "in":
        return u * u
    if kind == "out":
        return 1 - (1 - u) ** 2
    if kind == "back":
        c = 2.2
        return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2
    if kind == "step":
        return 1.0 if u >= 1 else 0.0
    return u * u * (3 - 2 * u)


class Tracks:
    def __init__(self, defaults):
        self.k = {n: [(0.0, v, "io")] for n, v in defaults.items()}

    def get(self, name, t):
        keys = self.k[name]
        if t <= keys[0][0]:
            return keys[0][1]
        for (t0, v0, _), (t1, v1, e) in zip(keys, keys[1:]):
            if t < t1:
                if t1 == t0:
                    return v1
                return v0 + (v1 - v0) * ease((t - t0) / (t1 - t0), e)
        return keys[-1][1]

    def sample(self, t):
        return {n: self.get(n, t) for n in self.k}

    def kf(self, t0, t1, e="io", **kw):
        for n, v in kw.items():
            cur = self.get(n, t0)
            keys = self.k[n]
            keys[:] = [k for k in keys if k[0] <= t0]
            if keys[-1][0] < t0:
                keys.append((t0, cur, "io"))
            keys.append((t1, float(v), e))


CHAR_DEF = dict(
    x=-250.0, y=0.0, lean=0.0, tilt=0.0,
    ua_l=-12.0, la_l=-6.0, ua_r=12.0, la_r=6.0, th_l=-7.0, sh_l=-7.0, th_r=7.0, sh_r=7.0,
    walk=0.0, walk_hz=1.6, flail=0.0, laugh=0.0, thumb=0.0, shiver=0.0,
    eye=1.0, px=0.0, py=0.0, psize=1.0, brow=0.0, browy=0.0, smile=0.35, mopen=0.0, mround=0.0, look=0.0,
    red=0.0, steam=0.0, tears=0.0, sweat=0.0, sparkle=0.0, love=0.0, zzz=0.0, dizzy=0.0, cool=0.0,
    wind=0.0, fire=0.0, vis=0.0,
)
GLOBAL_DEF = dict(
    s_x=PROP_ON_TABLE[0], s_y=PROP_ON_TABLE[1], s_rot=0.0, s_hand=0.0, s_handb=0.0, s_mouth=0.0, s_mouthb=0.0,
    s_scale=1.0, s_hot=0.0, s_vis=1.0,
    g_hand=0.0, g_mouth=0.0, g_who=0.0, g_level=1.0,
    t_x=0.0, t_y=0.0, t_rot=0.0, t_vis=0.0, dust=0.0, dust_x=700.0,
    cz=1.2, cx=560.0, cy=1150.0, shake=0.0, ring=0.0,
)
DEF = {**dict(CHAR_DEF), **{"b_" + k: v for k, v in CHAR_DEF.items()}, **GLOBAL_DEF}

EXPR = {
    "neutral": dict(eye=1.0, px=0, py=0, psize=1.0, brow=0, browy=0, smile=0.35, mopen=0, mround=0),
    "happy": dict(eye=0.9, psize=1.0, brow=0.3, browy=0.2, smile=1.0, mopen=0.15, mround=0),
    "excited": dict(eye=1.35, psize=1.35, brow=0.6, browy=1.0, smile=1.0, mopen=0.65, mround=0),
    "sneaky": dict(eye=0.45, psize=1.0, brow=-0.7, browy=0, smile=0.7, mopen=0, mround=0),
    "pain": dict(eye=0.0, brow=0.9, browy=0.3, smile=-0.6, mopen=0.85, mround=0),
    "shock": dict(eye=1.45, psize=0.6, brow=1.0, browy=1.2, smile=-0.3, mopen=1.0, mround=0),
    "sad": dict(eye=0.75, py=0.6, psize=1.1, brow=1.0, browy=0.2, smile=-1.0, mopen=0.0, mround=0),
    "blow": dict(eye=0.6, brow=0.2, smile=-0.2, mopen=0.35, mround=1.0),
    "laugh": dict(eye=0.0, brow=0.4, browy=0.5, smile=1.0, mopen=0.95, mround=0),
    "fire": dict(eye=1.45, psize=0.55, brow=1.0, browy=1.3, smile=-0.2, mopen=1.0, mround=0),
    "chew": dict(eye=0.0, brow=0.3, smile=0.9, mopen=0.0, mround=0),
    "angry": dict(eye=0.7, psize=0.9, brow=-1.0, browy=0, smile=-0.8, mopen=0.3, mround=0),
    "scared": dict(eye=1.3, psize=0.5, brow=1.0, browy=0.8, smile=-0.7, mopen=0.4, mround=0),
    "sleep": dict(eye=0.0, brow=0.0, smile=0.1, mopen=0.0, mround=0),
    "love": dict(eye=1.1, psize=1.2, brow=0.5, browy=0.6, smile=1.0, mopen=0.2, mround=0),
    "cool": dict(eye=0.9, brow=-0.3, smile=0.8, mopen=0, mround=0),
}
STAND = dict(lean=0, tilt=0, ua_l=-12, la_l=-6, ua_r=12, la_r=6, th_l=-7, sh_l=-7, th_r=7, sh_r=7, y=0)
ARMS_DOWN = dict(ua_l=-12, la_l=-6, ua_r=12, la_r=6)


# ═════════════════════════ characters ═════════════════════════
CHAR_TYPES = {
    "man": dict(hair="short", hair_col=(40, 30, 25), outfit="shirt", scale=1.0, voice="m"),
    "boy": dict(hair="spiky", hair_col=(40, 30, 25), outfit="shirt", scale=0.8, voice="kid_m"),
    "girl": dict(hair="ponytail", hair_col=(40, 30, 25), outfit="dress", scale=0.8, voice="kid_f"),
    "woman": dict(hair="long", hair_col=(35, 25, 20), outfit="dress", scale=0.95, bindi=True, voice="f"),
    "mummy": dict(hair="bun", hair_col=(35, 25, 20), outfit="saree", scale=0.95, bindi=True, voice="f"),
    "papa": dict(hair="short", hair_col=(40, 30, 25), outfit="kurta", scale=1.0, mustache=True, voice="m"),
    "dadaji": dict(hair="bald", hair_col=(220, 220, 220), outfit="kurta", scale=0.95, glasses=True,
                   mustache=True, mustache_col=(230, 230, 230), voice="old_m"),
    "dadi": dict(hair="bun", hair_col=(225, 225, 225), outfit="saree", scale=0.9, glasses=True, bindi=True,
                 voice="old_f"),
    "teacher": dict(hair="short", hair_col=(40, 30, 25), outfit="shirt", scale=1.0, glasses=True, voice="m"),
    "student": dict(hair="spiky", hair_col=(40, 30, 25), outfit="shirt", scale=0.85, voice="kid_m"),
    "plain": dict(hair="none", outfit="none", scale=1.0, voice="m"),
}
VOICES = {"m": ("hi-IN-MadhurNeural", 0), "f": ("hi-IN-SwaraNeural", 0), "kid_m": ("hi-IN-MadhurNeural", 35),
          "kid_f": ("hi-IN-SwaraNeural", 30), "old_m": ("hi-IN-MadhurNeural", -15), "old_f": ("hi-IN-SwaraNeural", -10)}
COLORS = {"red": (230, 70, 70), "blue": (70, 130, 230), "green": (70, 180, 100), "yellow": (250, 200, 60),
          "orange": (250, 140, 50), "pink": (240, 120, 180), "purple": (150, 90, 210), "sky": (100, 190, 240),
          "white": (245, 245, 245), "black": (60, 60, 60), "brown": (150, 100, 60), "grey": (150, 150, 150)}


def parse_color(c, default):
    if isinstance(c, str):
        c = c.strip().lower()
        if c in COLORS:
            return COLORS[c]
        if len(c) == 7 and c.startswith("#"):
            try:
                return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
            except ValueError:
                pass
    return default


def make_style(spec, rnd):
    spec = spec if isinstance(spec, dict) else {}
    typ = str(spec.get("type", "plain")).lower()
    st = dict(CHAR_TYPES.get(typ, CHAR_TYPES["plain"]))
    st["type"] = typ
    st["outfit_col"] = parse_color(spec.get("outfit_color"), rnd.choice(list(COLORS.values())[:8]))
    for k in ("glasses", "mustache", "cap", "bindi"):
        if k in spec:
            st[k] = bool(spec[k])
    if spec.get("hair") in ("none", "short", "spiky", "long", "bun", "ponytail", "bald"):
        st["hair"] = spec["hair"]
    st["cap_col"] = parse_color(spec.get("cap_color"), (230, 60, 60))
    return st


# ═════════════════════════ kinematics ═════════════════════════
def vec(a, length):
    r = math.radians(a)
    return math.sin(r) * length, math.cos(r) * length


def char_params(p, who):
    pre = "" if who == "a" else "b_"
    c = {k: p[pre + k] for k in CHAR_DEF}
    c["_phase"] = p.get(pre + "_phase", 0.0)
    c["_t"] = p.get("_t", 0.0)
    return c


def skeleton(c, sc=1.0):
    th_l, sh_l, th_r, sh_r = c["th_l"], c["sh_l"], c["th_r"], c["sh_r"]
    ua_l, la_l, ua_r, la_r = c["ua_l"], c["la_l"], c["ua_r"], c["la_r"]
    ph, w, tt = c.get("_phase", 0.0), c["walk"], c.get("_t", 0.0)
    if w:
        s = math.sin(ph)
        th_l += w * 26 * s
        th_r -= w * 26 * s
        sh_l = th_l - w * 30 * max(0.0, -s)
        sh_r = th_r - w * 30 * max(0.0, s)
        ua_l -= w * 20 * s
        ua_r += w * 20 * s
    if c["flail"]:
        f = c["flail"] * 35
        ua_l += f * math.sin(tt * 22)
        ua_r += f * math.sin(tt * 22 + 2)
        la_l += f * math.sin(tt * 22 + 1)
        la_r += f * math.sin(tt * 22 + 3)
    thg, shn, up_, lo_ = THIGH * sc, SHIN * sc, UPPER * sc, LOWER * sc
    drop = max(vec(th_l, thg)[1] + vec(sh_l, shn)[1], vec(th_r, thg)[1] + vec(sh_r, shn)[1])
    y = c["y"]
    if c["laugh"]:
        y += c["laugh"] * 7 * math.sin(tt * 38)
    x = c["x"] + (c["shiver"] * 5 * math.sin(tt * 70) if c["shiver"] else 0)
    hip = (x, GROUND - drop + y)
    add = lambda a, b: (a[0] + b[0], a[1] + b[1])
    up = lambda ang, ln: (math.sin(math.radians(ang)) * ln, -math.cos(math.radians(ang)) * ln)
    neck = add(hip, up(c["lean"], TORSO * sc))
    shoulder = add(hip, up(c["lean"], SHOULDER * sc))
    head = add(neck, up(c["lean"] + c["tilt"], (NECK + HEAD_R) * sc))
    el_l = add(shoulder, vec(ua_l, up_)); hd_l = add(el_l, vec(la_l, lo_))
    el_r = add(shoulder, vec(ua_r, up_)); hd_r = add(el_r, vec(la_r, lo_))
    kn_l = add(hip, vec(th_l, thg)); ft_l = add(kn_l, vec(sh_l, shn))
    kn_r = add(hip, vec(th_r, thg)); ft_r = add(kn_r, vec(sh_r, shn))
    mouth = (head[0] + c["look"] * 14 * sc, head[1] + 42 * sc)
    return dict(hip=hip, neck=neck, shoulder=shoulder, head=head, el_l=el_l, hd_l=hd_l, el_r=el_r, hd_r=hd_r,
                kn_l=kn_l, ft_l=ft_l, kn_r=kn_r, ft_r=ft_r, mouth=mouth, sc=sc)


# ═════════════════════════ story -> keyframes ═════════════════════════
T = Tracks(DEF)
TEXTS, BUBBLES, VOICE, SFX, MUSIC_CUTS = [], [], [], [], []
STYLE = {}


def clean_voice(v):
    """Sirf chhoti expression-awaaz (max 3 shabd, 16 akshar) — dialogue nahi."""
    v = " ".join(str(v or "").split())
    return v if v and len(v) <= 16 and len(v.split()) <= 3 else ""


def clean_caption(c):
    return " ".join(str(c or "").split())[:22]


class Director:
    ACTIONS = {
        "walk_in": "character chal ke andar aata hai (shuru mein)",
        "enter": "doosra character (b) andar aata hai",
        "wave": "haath hila ke hello",
        "notice": "prop ko dekh ke aankhein chamakti hain, excited",
        "sneaky_look": "chupke se idhar-udhar dekhta hai",
        "tiptoe": "dabe paon table tak jaata hai",
        "touch_hot": "prop chhuta hai, garam hai, haath jalta hai",
        "blow": "prop pe phook maarta hai",
        "grab": "table se prop uthata hai",
        "use_item": "prop haath mein le ke dekhta/chalata hai, hansta hai (phone, kitaab...)",
        "slip_juggle": "prop haath se phisalta hai, juggle karta hai, gir jaata hai",
        "drop": "prop zameen pe gir jaata hai",
        "pick_up": "zameen se prop uthata hai",
        "eat": "prop kha jaata hai (sirf khane ki cheez)",
        "spicy_fire": "teekha laga, muh se aag, bhaagta hai, paani peeta hai",
        "drink_water": "glass se paani peeta hai",
        "sad": "rota hai, aansu",
        "shock": "zor se chaunk ke uchhalta hai",
        "angry": "gussa, laal chehra, pair patakta hai",
        "scared": "dar ke kaanpta hai, pasina",
        "think": "sochta hai, '?'",
        "idea": "achanak idea aata hai, bulb",
        "dance": "khushi mein naachta hai",
        "cheer": "jeet ki khushi mein uchhalta hai",
        "facepalm": "maatha pakad leta hai",
        "shrug": "kandhe uchkata hai, '?'",
        "love": "dil wali aankhein, pyaar",
        "cool": "kaala chashma laga ke style",
        "yawn_sleep": "ubaasi le ke so jaata hai, Zzz",
        "wake_up": "jhatke se jaagta hai (prop alarm clock ho toh ring)",
        "sneeze": "aa..aa..chhee! zor ki chheenk",
        "exercise": "jumping jacks, pasina",
        "slip_fall": "phisal ke peeth ke bal girta hai, chakkar",
        "hide": "neeche jhuk ke chhupta hai",
        "run_away": "darr ke bhaag jaata hai aur wapas aata hai",
        "laugh": "zor se hansta hai + thumbs up (aakhir mein)",
        "caption": "screen pe chhota Hindi text (text field)",
        "scold": "doosre ko daantta hai (ungli dikha ke), doosra darta hai",
        "throw_item": "doosre pe throw_item phenkta hai (jaise chappal), sar pe lagti hai",
        "chase": "doosre ke peeche bhaagta hai",
        "give": "prop doosre ko deta hai",
        "snatch": "doosre se prop chheen leta hai",
        "high_five": "dono high five",
        "point_laugh": "doosre pe ungli dikha ke hansta hai",
    }

    def __init__(self, story):
        self.story = story
        self.t = 0.0
        self.who = "a"
        self.has_b = isinstance(story.get("characters"), dict) and isinstance(story["characters"].get("b"), dict)
        self.b_in = False
        self.state = "table"
        self.holder = None
        self.glass_full = True
        info = STYLE["prop_info"]
        self.edible = bool(story.get("prop_edible", info[0] == "builtin"))
        self.hot = bool(story.get("prop_hot", False))

    # ───────── helpers ─────────
    @property
    def other(self):
        return "b" if self.who == "a" else "a"

    def key(self, k, who=None):
        who = who or self.who
        return ("b_" + k) if (who == "b" and k in CHAR_DEF) else k

    def kf(self, t0, t1, e="io", who=None, **kw):
        T.kf(t0, t1, e, **{self.key(k, who): v for k, v in kw.items()})

    def get(self, k, t=None, who=None):
        return T.get(self.key(k, who), self.t if t is None else t)

    def expr(self, t0, t1, name, e="io", who=None, **extra):
        self.kf(t0, t1, e, who=who, **{**EXPR[name], **extra})

    def skel(self, t, who=None):
        c = char_params(T.sample(t), who or self.who)
        c["walk"] = 0
        c["flail"] = 0
        return skeleton(c, STYLE["chars"][who or self.who]["scale"])

    def hand_at(self, t, side="r", who=None):
        return self.skel(t, who)["hd_" + side]

    def x(self, who=None):
        return self.get("x", who=who)

    def toward_other(self):
        if not self.b_in:
            return 1
        return 1 if self.x(self.other) > self.x() else -1

    def say(self, at, beat, default="", who=None):
        v = clean_voice(beat.get("voice")) if "voice" in beat else default
        if v:
            VOICE.append((at, v, who or self.who))

    def bubble(self, t0, t1, ch, who=None):
        BUBBLES.append((t0, t1, ch, who or self.who))

    def cam(self, t0, t1, z, who=None, cy=870, e="out"):
        T.kf(t0, t1, e, cz=z, cx=self.x(who), cy=cy)

    def cam_reset(self, t0, t1):
        T.kf(t0, t1, cz=1.2, cx=560, cy=1150)

    def hk(self, who=None):
        return "s_hand" if (who or self.who) == "a" else "s_handb"

    def mk(self, who=None):
        return "s_mouth" if (who or self.who) == "a" else "s_mouthb"

    def walk_to(self, x_to, tiptoe=False, who=None, start=None, run=False):
        a = self.t if start is None else start
        x0 = self.get("x", a, who)
        if abs(x_to - x0) < 25:
            return a
        speed = 150 if tiptoe else (600 if run else 260)
        d = max(0.5, abs(x_to - x0) / speed)
        if tiptoe:
            self.kf(a, a + 0.25, who=who, ua_l=-150, la_l=-60, ua_r=150, la_r=60)
        self.kf(a, a + 0.15, who=who, walk=1.3 if run else (0.8 if tiptoe else 1.0),
                walk_hz=3.4 if run else (1.0 if tiptoe else 1.7))
        self.kf(a, a + d, "lin" if (tiptoe or run) else "io", who=who, x=x_to)
        self.kf(a + d - 0.2, a + d, who=who, walk=0.0)
        if tiptoe:
            self.kf(a + d - 0.2, a + d + 0.1, who=who, **ARMS_DOWN)
        return a + d + 0.1

    def go(self, x_to, **kw):
        self.t = self.walk_to(x_to, **kw)

    def ensure_near_table(self):
        if self.b_in and abs(self.x(self.other) - TABLE_X) < 160:
            self.walk_to(HOME_A - 60 if self.other == "a" else HOME_B + 120, who=self.other)
        if abs(self.x() - TABLE_X) > 40:
            self.go(TABLE_X)

    def ensure_in_hand(self):
        if self.state == "hand" and self.holder == self.who:
            return True
        if self.state == "hand" and self.holder != self.who:
            self.snatch({})
        elif self.state == "table":
            self.grab({})
        elif self.state == "floor":
            self.pick_up({})
        return self.state == "hand" and self.holder == self.who

    def detach(self, at):
        hp = self.hand_at(at, who=self.holder) if self.holder else (T.get("s_x", at), T.get("s_y", at))
        T.kf(at - 0.01, at, "step", s_x=hp[0], s_y=hp[1])
        T.kf(at, at + 0.01, "step", s_hand=0.0, s_handb=0.0, s_mouth=0.0, s_mouthb=0.0)
        self.holder = None

    # ───────── actions ─────────
    def walk_in(self, b):
        self.kf(0.0, 0.01, "step", vis=1.0)
        self.go(HOME_A if self.who == "a" else HOME_B)
        self.say(self.t - 0.4, b)

    def enter(self, b):
        if self.b_in or not self.has_b:
            return
        a = self.t
        self.kf(a, a + 0.01, "step", who="b", vis=1.0, x=1350)
        self.b_in = True
        end = self.walk_to(HOME_B, who="b", start=a + 0.02)
        self.kf(a, a + 0.3, who="a", look=0.8, px=1.0)
        self.t = end
        self.say(self.t - 0.3, b, who="b")

    def wave(self, b):
        a = self.t
        self.expr(a, a + 0.2, "happy")
        for k in range(4):
            s = a + k * 0.22
            self.kf(s, s + 0.11, ua_r=150, la_r=200 if k % 2 else 150)
        self.say(a + 0.1, b, "हेलो!")
        self.kf(a + 0.9, a + 1.2, **ARMS_DOWN)
        self.t = a + 1.25

    def notice(self, b):
        a = self.t
        d = 1 if T.get("s_x", a) > self.x() else -1
        self.kf(a, a + 0.3, look=0.9 * d, px=1.0 * d, tilt=8 * d)
        self.expr(a + 0.4, a + 0.6, "excited", "back", px=1.0 * d, sparkle=1.0)
        self.kf(a + 0.4, a + 0.6, "back", y=-40)
        self.kf(a + 0.65, a + 0.9, "in", y=0)
        self.bubble(a + 0.4, a + 1.4, "!")
        self.say(a + 0.45, b, "ओहो!")
        SFX.append((a + 0.4, "sparkle", 0.5))
        self.cam(a + 0.35, a + 0.7, 1.8)
        self.cam_reset(a + 1.3, a + 1.6)
        self.kf(a + 1.3, a + 1.6, sparkle=0.0, tilt=0, look=0.3 * d)
        self.t = a + 1.7

    def sneaky_look(self, b):
        a = self.t
        self.expr(a, a + 0.25, "sneaky", px=-1.0)
        self.kf(a, a + 0.25, look=-0.9, tilt=-10)
        self.say(a + 0.3, b, "हम्म्म...")
        self.kf(a + 0.9, a + 1.15, px=1.0, look=0.9, tilt=10)
        self.kf(a + 1.6, a + 1.8, tilt=0, look=0.3, px=0.4)
        self.t = a + 1.9

    def tiptoe(self, b):
        self.say(self.t, b)
        if self.b_in and self.state != "table":
            target = self.x(self.other) - 220 * self.toward_other()
        else:
            target = TABLE_X
        self.go(target, tiptoe=True)

    def touch_hot(self, b):
        if self.state != "table":
            return self.shock(b)
        self.ensure_near_table()
        a = self.t
        self.kf(a, a + 0.5, ua_l=-12, la_l=-6, ua_r=62, la_r=75, lean=8)
        self.expr(a, a + 0.4, "happy", px=1.0)
        self.expr(a + 0.62, a + 0.7, "pain", "lin", red=0.25)
        self.kf(a + 0.62, a + 0.75, "out", y=-90, ua_r=150, la_r=170, lean=-6)
        T.kf(a + 0.62, a + 0.75, "out", shake=1.0, s_hot=1.0)
        self.kf(a + 0.75, a + 1.1, "in", y=0)
        T.kf(a + 1.0, a + 1.3, shake=0.0)
        SFX.extend([(a + 0.62, "sizzle", 0.55), (a + 1.1, "thud", 0.4)])
        self.say(a + 0.66, b, "आह्ह्ह!")
        self.bubble(a + 0.65, a + 1.4, "!")
        for k in range(6):
            s = a + 1.1 + k * 0.12
            self.kf(s, s + 0.06, "lin", ua_r=120 if k % 2 else 160, la_r=150 if k % 2 else 200)
        self.kf(a + 1.85, a + 2.05, ua_r=12, la_r=6, red=0.0, lean=0)
        self.expr(a + 1.85, a + 2.05, "neutral")
        self.hot = True
        self.t = a + 2.1

    def blow(self, b):
        if self.state == "gone":
            return
        if self.state == "table":
            self.ensure_near_table()
        a = self.t
        if self.state == "table":
            self.kf(a, a + 0.3, lean=18, tilt=12)
        elif self.holder == self.who:
            self.kf(a, a + 0.3, ua_r=-30, la_r=-140)
        self.expr(a, a + 0.2, "blow", px=1.0 if self.state == "table" else 0.0, py=0.4)
        self.kf(a + 0.2, a + 0.4, wind=1.0)
        self.kf(a + 1.0, a + 1.2, wind=0.0)
        T.kf(a + 1.0, a + 1.2, s_hot=0.0)
        self.say(a + 0.2, b, "फ़ूऊऊऊ")
        SFX.append((a + 0.2, "whoosh", 0.45))
        self.kf(a + 1.1, a + 1.4, lean=0, tilt=0)
        self.expr(a + 1.2, a + 1.4, "happy")
        self.hot = False
        self.t = a + 1.5

    def grab(self, b):
        if self.state != "table":
            return
        self.ensure_near_table()
        a = self.t
        self.kf(a, a + 0.35, lean=6, ua_r=62, la_r=75)
        T.kf(a + 0.35, a + 0.5, **{self.hk(): 1.0})
        SFX.append((a + 0.45, "pop", 0.35))
        self.expr(a + 0.35, a + 0.55, "happy", px=0.3)
        self.say(a + 0.4, b)
        self.kf(a + 0.6, a + 1.0, lean=0, ua_r=20, la_r=-40)
        self.state, self.holder = "hand", self.who
        self.t = a + 1.05

    def use_item(self, b):
        if not self.ensure_in_hand():
            return
        a = self.t
        self.kf(a, a + 0.35, ua_r=40, la_r=-130, tilt=6)
        self.expr(a + 0.2, a + 0.4, "happy", px=0.5, py=0.6)
        self.say(a + 0.6, b, "हे हे हे")
        self.kf(a + 0.6, a + 0.8, laugh=0.6)
        self.expr(a + 0.6, a + 0.8, "laugh")
        self.kf(a + 1.8, a + 2.0, laugh=0.0, tilt=0)
        self.expr(a + 1.8, a + 2.0, "happy")
        self.t = a + 2.05

    def slip_juggle(self, b):
        if not self.ensure_in_hand():
            return
        a = self.t
        self.kf(a, a + 0.6, ua_r=-10, la_r=-150)
        self.expr(a + 0.3, a + 0.6, "excited", px=0.0, py=0.6)
        sl = a + 0.9
        self.detach(sl)
        hp = (T.get("s_x", sl), T.get("s_y", sl))
        sc = STYLE["chars"][self.who]["scale"]
        T.kf(sl, sl + 0.45, "out", s_y=hp[1] - 300 * sc, s_x=hp[0] + 40, s_rot=260)
        SFX.append((sl, "boing", 0.55))
        self.expr(sl + 0.02, sl + 0.15, "shock", py=-1.0)
        cx0 = self.x()
        self.kf(sl + 0.02, sl + 0.2, "out", ua_l=-160, la_l=-170, ua_r=160, la_r=170)
        self.cam(sl + 0.02, sl + 0.2, 1.4, cy=900)
        self.say(sl + 0.15, b, "अरे अरे अरे!")
        self.bubble(sl + 0.05, sl + 1.2, "!")
        top = self.hand_at(sl + 0.2, "l")[1]
        tj = sl + 0.45
        for i in range(4):
            jx = cx0 - 60 * sc if i % 2 == 0 else cx0 + 60 * sc
            T.kf(tj, tj + 0.18, "in", s_y=top, s_x=jx, s_rot=260 + 180 * (i + 1))
            SFX.append((tj + 0.18, "pop", 0.4))
            T.kf(tj + 0.18, tj + 0.3, "out", s_y=top - 170, s_x=jx + (40 if i % 2 == 0 else -40))
            self.kf(tj, tj + 0.18, ua_l=-120 if i % 2 == 0 else -160, ua_r=160 if i % 2 == 0 else 120)
            self.kf(tj, tj + 0.12, px=-0.8 if i % 2 == 0 else 0.8)
            tj += 0.3
        self.state = "air"
        self.t = tj
        self.drop({"voice": ""})

    def drop(self, b):
        a = self.t
        if self.state == "hand":
            self.detach(a)
        elif self.state == "table":
            self.ensure_near_table(); a = self.t
            self.kf(a, a + 0.25, ua_r=62, la_r=75, lean=8)
            a += 0.25
            T.kf(a, a + 0.15, "out", s_y=1060, s_x=860)
            a += 0.15
        elif self.state != "air":
            return
        fx = T.get("s_x", a)
        fx = min(1000, max(120, fx + (40 if fx > self.x() else -40)))
        T.kf(a, a + 0.45, "in", s_x=fx, s_y=GROUND - 26, s_rot=T.get("s_rot", a) + 540)
        SFX.extend([(a, "whoosh", 0.35), (a + 0.45, "splat", 0.7)])
        f = a + 0.45
        T.kf(f, f + 0.01, "step", dust_x=fx)
        T.kf(f, f + 0.05, "lin", dust=1.0)
        T.kf(f + 0.6, f + 1.0, dust=0.0)
        self.expr(a, a + 0.2, "shock", py=1.0, px=0.6)
        self.kf(a, a + 0.25, lean=0, **ARMS_DOWN)
        self.cam_reset(a, a + 0.25)
        T.kf(f, f + 0.1, "out", shake=0.8)
        T.kf(f + 0.2, f + 0.4, shake=0.0)
        self.bubble(a + 0.05, f + 0.6, "!")
        self.say(a + 0.1, b)
        self.state = "floor"
        self.t = f + 0.6

    def pick_up(self, b):
        if self.state != "floor":
            return
        sx = T.get("s_x", self.t)
        self.go(sx - 110 if sx > 200 else sx + 110)
        a = self.t
        self.expr(a, a + 0.2, "excited", py=1.0)
        self.kf(a, a + 0.4, lean=45, th_l=-80, sh_l=10, th_r=-30, sh_r=40, ua_r=60, la_r=50)
        T.kf(a + 0.4, a + 0.5, **{self.hk(): 1.0})
        SFX.append((a + 0.45, "pop", 0.35))
        self.kf(a + 0.6, a + 1.0, **STAND)
        self.kf(a + 0.6, a + 1.0, ua_r=20, la_r=-40)
        self.expr(a + 0.7, a + 0.9, "sneaky", px=0.6, smile=1.0)
        self.say(a + 0.75, b, "हे हे हे")
        self.state, self.holder = "hand", self.who
        self.t = a + 1.6

    def eat(self, b):
        if not self.edible:
            return self.use_item(b)
        if not self.ensure_in_hand():
            return
        a = self.t
        self.kf(a, a + 0.35, ua_r=-30, la_r=-140)
        T.kf(a + 0.35, a + 0.6, **{self.mk(): 1.0})
        self.kf(a + 0.35, a + 0.55, mopen=1.0, eye=1.2)
        self.kf(a + 0.65, a + 0.7, "lin", mopen=0.0)
        T.kf(a + 0.65, a + 0.7, "lin", s_scale=0.55)
        SFX.append((a + 0.67, "crunch", 0.6))
        self.kf(a + 1.05, a + 1.15, mopen=1.0)
        self.kf(a + 1.2, a + 1.25, "lin", mopen=0.0)
        T.kf(a + 1.2, a + 1.25, "lin", s_scale=0.0)
        SFX.append((a + 1.22, "crunch", 0.6))
        T.kf(a + 1.25, a + 1.45, **{self.mk(): 0.0})
        self.kf(a + 1.25, a + 1.45, ua_r=12, la_r=6)
        self.expr(a + 1.25, a + 1.45, "chew")
        self.say(a + 1.45, b, "म्म्म्म!")
        for k in range(5):
            s = a + 1.45 + k * 0.18
            self.kf(s, s + 0.09, mopen=0.25)
            self.kf(s + 0.09, s + 0.18, mopen=0.0)
        self.expr(a + 2.4, a + 2.6, "happy")
        self.state, self.holder = "gone", None
        self.t = a + 2.6

    def spicy_fire(self, b):
        a = self.t
        self.expr(a, a + 0.15, "neutral", eye=1.2, px=0.0)
        self.kf(a, a + 0.8, "in", red=1.0, sweat=1.0)
        self.bubble(a + 0.15, a + 0.75, "?")
        MUSIC_CUTS.append((a, a + 0.85))
        self.cam(a + 0.5, a + 0.8, 2.0, cy=860, e="io")
        f = a + 0.85
        self.expr(f, f + 0.1, "fire", "lin")
        self.kf(f, f + 0.1, "lin", fire=1.0, steam=1.0)
        T.kf(f, f + 0.1, "lin", shake=1.0)
        self.cam_reset(f, f + 0.1)
        self.say(f + 0.05, b, "आआआआआ!")
        SFX.extend([(f, "fire", 0.7), (f + 0.75, "boing", 0.3)])
        self.kf(f + 0.45, f + 0.65, walk=1.4, walk_hz=3.6, flail=1.0, ua_l=-150, ua_r=150, la_l=-150, la_r=150)
        for k, xx in enumerate((220, 640, 260, 560)):
            self.kf(f + 0.55 + k * 0.5, f + 1.05 + k * 0.5, x=xx)
        self.kf(f + 1.15, f + 1.25, fire=0.3)
        self.kf(f + 1.75, f + 1.85, fire=1.0)
        self.kf(f + 2.45, f + 2.75, walk=0.0, flail=0.0, fire=0.0, **ARMS_DOWN)
        T.kf(f + 2.45, f + 2.75, shake=0.3)
        self.t = f + 2.8
        if self.glass_full:
            self.drink_water({"voice": "आहा!"})
        else:
            self.kf(self.t, self.t + 1.0, red=0.0, steam=0.0, sweat=0.0)
            T.kf(self.t, self.t + 1.0, shake=0.0)
            self.t += 1.0

    def drink_water(self, b):
        if not self.glass_full:
            return
        self.ensure_near_table()
        a = self.t
        T.kf(a, a + 0.01, "step", g_who=0.0 if self.who == "a" else 1.0)
        self.kf(a, a + 0.35, ua_l=-12, la_l=-6, ua_r=70, la_r=82, lean=10)
        T.kf(a + 0.35, a + 0.45, g_hand=1.0)
        SFX.append((a + 0.4, "pop", 0.3))
        self.kf(a + 0.45, a + 0.85, lean=-8, tilt=-15, ua_r=100, la_r=-120)
        T.kf(a + 0.45, a + 0.85, g_mouth=1.0)
        self.expr(a + 0.5, a + 0.7, "chew", eye=0.0, smile=0.0)
        self.kf(a + 0.85, a + 1.0, mopen=0.4)
        for k in range(3):
            SFX.append((a + 0.95 + k * 0.42, "gulp", 0.6))
        T.kf(a + 0.9, a + 2.2, "lin", g_level=0.0, shake=0.0)
        self.kf(a + 0.9, a + 2.2, "lin", red=0.0, steam=0.0, sweat=0.0)
        T.kf(a + 2.2, a + 2.5, g_mouth=0.0)
        self.kf(a + 2.2, a + 2.5, lean=0, tilt=0, ua_r=20, la_r=30, mopen=0.0)
        self.expr(a + 2.3, a + 2.5, "happy")
        self.say(a + 2.3, b, "आहा!")
        self.glass_full = False
        self.t = a + 2.6

    def sad(self, b):
        a = self.t
        self.expr(a, a + 0.4, "sad", px=0.4, tears=1.0)
        self.kf(a, a + 0.6, tilt=-6, lean=-4, ua_l=-4, ua_r=4)
        self.cam(a, a + 0.8, 1.9, cy=860, e="io")
        SFX.append((a + 0.05, "trombone", 0.55))
        MUSIC_CUTS.append((a - 0.2, a + 2.1))
        self.say(a + 0.2, b, "ओह नो...")
        self.cam_reset(a + 1.9, a + 2.2)
        self.kf(a + 1.9, a + 2.2, tears=0.0, tilt=0, lean=0)
        self.expr(a + 1.9, a + 2.2, "neutral")
        self.t = a + 2.3

    def shock(self, b):
        a = self.t
        self.expr(a, a + 0.12, "shock", "back", py=-0.5)
        self.kf(a, a + 0.15, "out", y=-110, ua_l=-150, la_l=-170, ua_r=150, la_r=170)
        T.kf(a, a + 0.15, "out", shake=0.8)
        self.kf(a + 0.15, a + 0.45, "in", y=0)
        T.kf(a + 0.4, a + 0.6, shake=0.0)
        self.cam(a, a + 0.25, 1.6, cy=900)
        SFX.extend([(a, "boing", 0.5), (a + 0.45, "thud", 0.4)])
        self.bubble(a + 0.02, a + 1.1, "!")
        self.say(a + 0.05, b, "अरे!")
        self.cam_reset(a + 1.0, a + 1.3)
        self.kf(a + 1.0, a + 1.3, **ARMS_DOWN)
        self.expr(a + 1.0, a + 1.3, "neutral")
        self.t = a + 1.35

    def angry(self, b):
        a = self.t
        self.expr(a, a + 0.25, "angry", red=0.9, steam=1.0)
        self.kf(a, a + 0.25, ua_l=-40, la_l=40, ua_r=40, la_r=-40)
        self.cam(a, a + 0.25, 1.6, cy=900)
        for k in range(4):
            s = a + 0.3 + k * 0.25
            self.kf(s, s + 0.1, "out", y=-35)
            self.kf(s + 0.1, s + 0.22, "in", y=0)
            SFX.append((s + 0.22, "thud", 0.45))
        T.kf(a + 0.3, a + 0.4, shake=0.6)
        self.say(a + 0.3, b, "उफ्फ!")
        T.kf(a + 1.4, a + 1.7, shake=0.0)
        self.cam_reset(a + 1.4, a + 1.7)
        self.kf(a + 1.4, a + 1.7, red=0.0, steam=0.0, **ARMS_DOWN)
        self.expr(a + 1.4, a + 1.7, "neutral")
        self.t = a + 1.75

    def scared(self, b):
        a = self.t
        self.expr(a, a + 0.2, "scared", sweat=1.0)
        self.kf(a, a + 0.25, shiver=1.0, ua_l=-40, la_l=-150, ua_r=40, la_r=150, lean=-6)
        SFX.append((a, "chatter", 0.4))
        self.say(a + 0.2, b, "ब्ब्ब्ब...")
        self.cam(a, a + 0.3, 1.6, cy=900)
        self.cam_reset(a + 1.5, a + 1.8)
        self.kf(a + 1.5, a + 1.8, shiver=0.0, sweat=0.0, lean=0, **ARMS_DOWN)
        self.expr(a + 1.5, a + 1.8, "neutral")
        self.t = a + 1.85

    def think(self, b):
        a = self.t
        self.expr(a, a + 0.3, "neutral", eye=0.8, px=0.7, py=-0.9, brow=0.5, smile=0.0)
        if self.holder != self.who:
            self.kf(a, a + 0.35, ua_r=20, la_r=-150, tilt=-8)
        self.bubble(a + 0.3, a + 1.5, "?")
        self.say(a + 0.35, b, "हम्म्म...")
        self.kf(a + 1.5, a + 1.8, tilt=0)
        if self.holder != self.who:
            self.kf(a + 1.5, a + 1.8, ua_r=12, la_r=6)
        self.expr(a + 1.5, a + 1.8, "neutral")
        self.t = a + 1.85

    def idea(self, b):
        a = self.t
        self.expr(a, a + 0.15, "excited", "back", px=0, py=-1)
        self.kf(a, a + 0.2, "out", y=-30, ua_l=-160, la_l=-180)
        self.kf(a + 0.2, a + 0.4, "in", y=0)
        self.bubble(a, a + 1.3, "💡")
        SFX.append((a, "ding", 0.6))
        self.say(a + 0.1, b, "आहा!")
        self.kf(a + 1.2, a + 1.5, ua_l=-12, la_l=-6)
        self.expr(a + 1.2, a + 1.5, "sneaky", smile=1.0)
        self.t = a + 1.55

    def dance(self, b):
        a = self.t
        self.expr(a, a + 0.2, "laugh")
        self.say(a + 0.1, b, "वाह!")
        holding = self.holder == self.who
        for k in range(6):
            s = a + k * 0.3
            sgn = 1 if k % 2 else -1
            arm = dict(ua_l=-150, la_l=-170, ua_r=40, la_r=80) if k % 2 else dict(ua_l=-40, la_l=-80, ua_r=150, la_r=170)
            if holding:
                arm.pop("ua_r"); arm.pop("la_r")
            self.kf(s, s + 0.15, lean=12 * sgn, th_l=-20 * sgn - 7, th_r=20 * sgn + 7, **arm)
            SFX.append((s, "pop", 0.25))
        self.kf(a + 1.8, a + 2.1, lean=0, th_l=-7, th_r=7, ua_l=-12, la_l=-6)
        if not holding:
            self.kf(a + 1.8, a + 2.1, ua_r=12, la_r=6)
        self.expr(a + 1.8, a + 2.0, "happy")
        self.t = a + 2.15

    def cheer(self, b):
        a = self.t
        self.expr(a, a + 0.15, "excited")
        for k in range(2):
            s = a + k * 0.55
            self.kf(s, s + 0.2, "out", y=-120, ua_l=-160, la_l=-175, ua_r=160, la_r=175, th_l=-30, th_r=30)
            self.kf(s + 0.2, s + 0.45, "in", y=0, th_l=-7, th_r=7)
            SFX.append((s, "boing", 0.3))
        SFX.append((a + 0.1, "sparkle", 0.5))
        self.say(a + 0.1, b, "येएए!")
        self.kf(a + 1.2, a + 1.5, **ARMS_DOWN)
        self.expr(a + 1.2, a + 1.5, "happy")
        self.t = a + 1.55

    def facepalm(self, b):
        a = self.t
        self.expr(a, a + 0.2, "neutral", eye=0.0, smile=-0.6, brow=0.8)
        self.kf(a, a + 0.2, "out", ua_l=30, la_l=-160, tilt=10)
        SFX.append((a + 0.2, "thud", 0.5))
        self.say(a + 0.25, b, "उफ्फ...")
        self.kf(a + 1.3, a + 1.6, ua_l=-12, la_l=-6, tilt=0)
        self.expr(a + 1.3, a + 1.6, "neutral")
        self.t = a + 1.7

    def shrug(self, b):
        a = self.t
        holding = self.holder == self.who
        self.expr(a, a + 0.2, "neutral", eye=0.9, brow=0.9, browy=0.6, smile=-0.3)
        self.kf(a, a + 0.25, ua_l=-60, la_l=-130, tilt=-10)
        if not holding:
            self.kf(a, a + 0.25, ua_r=60, la_r=130)
        self.bubble(a + 0.2, a + 1.2, "?")
        self.say(a + 0.2, b)
        self.kf(a + 1.2, a + 1.5, ua_l=-12, la_l=-6, tilt=0)
        if not holding:
            self.kf(a + 1.2, a + 1.5, ua_r=12, la_r=6)
        self.t = a + 1.6

    def love(self, b):
        a = self.t
        self.expr(a, a + 0.3, "love", love=1.0)
        self.kf(a, a + 0.4, tilt=10, lean=-4, ua_l=-20, la_l=-150)
        SFX.append((a, "sparkle", 0.4))
        self.say(a + 0.3, b, "आहा...")
        self.kf(a + 1.8, a + 2.1, love=0.0, tilt=0, lean=0, ua_l=-12, la_l=-6)
        self.expr(a + 1.8, a + 2.1, "happy")
        self.t = a + 2.15

    def cool(self, b):
        a = self.t
        self.kf(a, a + 0.3, "out", cool=1.0)
        self.expr(a, a + 0.3, "cool")
        self.kf(a + 0.3, a + 0.5, lean=-6, ua_l=-50, la_l=40)
        SFX.append((a + 0.3, "ding", 0.4))
        self.say(a + 0.4, b, "हम्म!")
        self.cam(a + 0.3, a + 0.6, 1.5, cy=930)
        self.cam_reset(a + 1.6, a + 1.9)
        self.kf(a + 1.6, a + 1.9, lean=0, ua_l=-12, la_l=-6)
        self.t = a + 1.95

    def yawn_sleep(self, b):
        a = self.t
        self.expr(a, a + 0.4, "neutral", eye=0.2, mopen=1.0, mround=0)
        self.kf(a, a + 0.5, ua_l=-150, la_l=-200, ua_r=150, la_r=200, lean=-4)
        self.say(a + 0.2, b, "हाआआ...")
        self.kf(a + 1.0, a + 1.4, lean=4, tilt=18, **ARMS_DOWN)
        self.expr(a + 1.0, a + 1.4, "sleep", zzz=1.0)
        SFX.append((a + 1.5, "snore", 0.5))
        self.cam(a + 1.2, a + 1.6, 1.5, cy=930, e="io")
        self.t = a + 3.0

    def wake_up(self, b):
        a = self.t
        if "alarm" in STYLE.get("prop_name", "") and self.state == "table":
            T.kf(a, a + 0.05, ring=1.0)
            SFX.append((a, "ring", 0.6))
            T.kf(a + 1.3, a + 1.4, ring=0.0)
            a += 0.5
        self.kf(a, a + 0.01, zzz=0.0)
        self.expr(a, a + 0.12, "shock", "back")
        self.kf(a, a + 0.15, "out", y=-120, lean=0, tilt=0, ua_l=-150, la_l=-170, ua_r=150, la_r=170)
        self.kf(a + 0.15, a + 0.45, "in", y=0)
        T.kf(a, a + 0.15, shake=0.8)
        T.kf(a + 0.4, a + 0.6, shake=0.0)
        self.cam_reset(a, a + 0.3)
        SFX.append((a, "boing", 0.5))
        self.bubble(a, a + 1.0, "!")
        self.say(a + 0.05, b, "हैं?!")
        self.kf(a + 0.9, a + 1.2, **ARMS_DOWN)
        self.expr(a + 0.9, a + 1.2, "neutral", eye=1.2)
        self.t = a + 1.25

    def sneeze(self, b):
        a = self.t
        for k in range(2):
            s = a + k * 0.5
            self.expr(s, s + 0.3, "neutral", eye=0.3 + 0.2 * k, mopen=0.5, brow=0.8, smile=-0.2)
            self.kf(s, s + 0.3, lean=-8 - 4 * k, tilt=-10 - 5 * k)
        self.say(a, b, "आ... आ... छी!")
        s = a + 1.1
        self.expr(s, s + 0.08, "pain", "lin")
        self.kf(s, s + 0.1, "out", lean=18, tilt=16, wind=1.0)
        T.kf(s, s + 0.1, "out", shake=1.0)
        SFX.append((s, "whoosh", 0.6))
        T.kf(s + 0.3, s + 0.5, shake=0.0)
        self.kf(s + 0.5, s + 0.8, lean=0, tilt=0, wind=0.0)
        self.expr(s + 0.6, s + 0.8, "neutral")
        self.t = s + 0.85

    def exercise(self, b):
        a = self.t
        self.expr(a, a + 0.2, "neutral", eye=0.8, smile=0.0, brow=-0.3)
        for k in range(5):
            s = a + k * 0.36
            self.kf(s, s + 0.18, ua_l=-165, la_l=-175, ua_r=165, la_r=175, th_l=-28, sh_l=-28, th_r=28, sh_r=28)
            self.kf(s + 0.18, s + 0.36, ua_l=-15, la_l=-8, ua_r=15, la_r=8, th_l=-7, sh_l=-7, th_r=7, sh_r=7)
            SFX.append((s + 0.18, "step", 0.3))
        self.kf(a + 0.8, a + 1.8, sweat=1.0, red=0.4)
        self.say(a + 0.4, b, "हुँह! हुँह!")
        self.expr(a + 1.9, a + 2.1, "pain", "io", eye=0.0)
        self.kf(a + 1.9, a + 2.2, lean=20)
        self.kf(a + 2.6, a + 2.9, lean=0, sweat=0.0, red=0.0)
        self.expr(a + 2.6, a + 2.9, "neutral")
        self.t = a + 2.95

    def slip_fall(self, b):
        a = self.t
        self.expr(a, a + 0.1, "shock")
        self.kf(a, a + 0.15, "out", th_r=60, sh_r=70, ua_l=-150, ua_r=150, lean=-20)
        SFX.append((a, "boing", 0.5))
        self.kf(a + 0.15, a + 0.45, "in", lean=-80, th_l=60, sh_l=80, th_r=80, sh_r=95, y=60)
        T.kf(a + 0.45, a + 0.55, "out", shake=1.0)
        SFX.append((a + 0.45, "splat", 0.6))
        self.say(a + 0.2, b, "आआ!")
        self.expr(a + 0.5, a + 0.7, "pain", dizzy=1.0)
        T.kf(a + 0.7, a + 0.9, shake=0.0)
        self.cam(a + 0.5, a + 0.8, 1.5, cy=1200, e="io")
        e = a + 2.0
        self.kf(e, e + 0.5, dizzy=0.0, **STAND)
        self.expr(e, e + 0.5, "neutral", eye=0.8)
        self.cam_reset(e, e + 0.5)
        self.t = e + 0.55

    def hide(self, b):
        a = self.t
        self.expr(a, a + 0.2, "scared", px=1.0)
        self.kf(a, a + 0.35, lean=30, th_l=-95, sh_l=20, th_r=-60, sh_r=40, ua_l=-120, la_l=-160, ua_r=120, la_r=160)
        self.say(a + 0.2, b, "श्श्श...")
        self.kf(a + 0.6, a + 0.8, px=-1.0)
        self.kf(a + 1.1, a + 1.3, px=1.0)
        self.kf(a + 1.7, a + 2.0, **STAND)
        self.expr(a + 1.7, a + 2.0, "neutral")
        self.t = a + 2.05

    def run_away(self, b):
        a = self.t
        self.expr(a, a + 0.15, "scared")
        self.kf(a, a + 0.15, flail=1.0, ua_l=-150, ua_r=150)
        self.say(a + 0.05, b, "बचाओ!")
        d = -1 if self.x() < 540 else 1
        end = self.walk_to(-260 if d < 0 else 1340, start=a + 0.1, run=True)
        self.kf(end - 0.1, end, flail=0.0, **ARMS_DOWN)
        SFX.append((a, "whoosh", 0.4))
        self.t = end + 0.4
        self.go(HOME_A if self.who == "a" else HOME_B, run=True)

    def laugh(self, b):
        a = self.t
        self.expr(a, a + 0.25, "laugh")
        self.kf(a, a + 0.3, laugh=1.0, lean=-6, ua_l=-150, la_l=-175, thumb=1.0)
        self.say(a + 0.3, b, "हा हा हा हा!")
        SFX.append((a + 0.15, "sparkle", 0.4))
        if self.b_in:
            o = self.other
            self.expr(a + 0.4, a + 0.6, "laugh", who=o)
            self.kf(a + 0.4, a + 0.7, laugh=1.0, who=o)
            T.kf(a + 0.8, a + 1.2, cz=1.25, cx=(self.x() + self.x(o)) / 2, cy=1100)
        else:
            T.kf(a + 0.8, a + 1.2, cz=1.4, cx=self.x(), cy=1080)
        self.t = a + 2.4

    def caption(self, b):
        txt = clean_caption(b.get("text"))
        if txt:
            TEXTS.append((self.t, self.t + 2.3, txt, 1700, 104, (255, 255, 255)))
            SFX.append((self.t, "ding", 0.6))
        self.t += 0.3

    # ───────── do characters ─────────
    def need_b(self):
        if not self.has_b:
            return False
        if not self.b_in:
            w = self.who
            self.enter({})
            self.who = w
        return True

    def face_each_other(self, t0, t1):
        d = 1 if self.x("b") > self.x("a") else -1
        self.kf(t0, t1, who="a", px=d, look=0.6 * d)
        self.kf(t0, t1, who="b", px=-d, look=-0.6 * d)

    def scold(self, b):
        if not self.need_b():
            return self.angry(b)
        o = self.other
        d = self.toward_other()
        if abs(self.x() - self.x(o)) > 380:
            self.go(self.x(o) - 300 * d)
        a = self.t
        self.face_each_other(a, a + 0.2)
        self.expr(a, a + 0.25, "angry", red=0.8, steam=1.0, px=d)
        arm = dict(ua_r=95, la_r=95) if d > 0 else dict(ua_l=-95, la_l=-95)
        self.kf(a, a + 0.25, lean=8 * d, **arm)
        for k in range(3):
            s = a + 0.3 + k * 0.3
            self.kf(s, s + 0.15, **({"la_r": 70} if d > 0 else {"la_l": -70}))
            self.kf(s + 0.15, s + 0.3, **({"la_r": 95} if d > 0 else {"la_l": -95}))
        self.say(a + 0.3, b, "ऐ!")
        self.expr(a + 0.3, a + 0.5, "scared", who=o, sweat=1.0, px=-d)
        self.kf(a + 0.3, a + 0.5, who=o, shiver=1.0, lean=-10 * d, ua_l=-40, la_l=-150, ua_r=40, la_r=150)
        T.kf(a + 0.3, a + 0.6, cz=1.3, cx=(self.x() + self.x(o)) / 2, cy=1080)
        e = a + 1.8
        self.kf(e, e + 0.3, red=0.0, steam=0.0, lean=0, **ARMS_DOWN)
        self.expr(e, e + 0.3, "neutral")
        self.kf(e, e + 0.3, who=o, shiver=0.0, sweat=0.0, lean=0, **ARMS_DOWN)
        self.expr(e, e + 0.3, "sad", who=o, tears=0)
        self.cam_reset(e, e + 0.3)
        self.t = e + 0.35

    def throw_item(self, b):
        if not self.need_b():
            return self.angry(b)
        a, o = self.t, self.other
        d = self.toward_other()
        self.expr(a, a + 0.2, "angry", red=0.7, px=d)
        if d > 0:
            self.kf(a, a + 0.35, ua_r=-160, la_r=-200, lean=-10)
        else:
            self.kf(a, a + 0.35, ua_l=160, la_l=200, lean=10)
        self.say(a + 0.1, b, "ये ले!")
        r = a + 0.5
        hand = self.hand_at(r, "r" if d > 0 else "l")
        if d > 0:
            self.kf(r - 0.1, r + 0.05, "out", ua_r=80, la_r=80, lean=10)
        else:
            self.kf(r - 0.1, r + 0.05, "out", ua_l=-80, la_l=-80, lean=-10)
        target = self.skel(r + 0.5, o)["head"]
        T.kf(r - 0.01, r, "step", t_x=hand[0], t_y=hand[1], t_vis=1.0, t_rot=0)
        T.kf(r, r + 0.5, "lin", t_x=target[0], t_rot=720 * d)
        T.kf(r, r + 0.25, "out", t_y=min(hand[1], target[1]) - 200)
        T.kf(r + 0.25, r + 0.5, "in", t_y=target[1])
        SFX.append((r, "whoosh", 0.5))
        h = r + 0.5
        SFX.extend([(h, "thud", 0.7), (h, "boing", 0.4)])
        self.expr(h, h + 0.08, "pain", "lin", who=o, dizzy=1.0)
        self.kf(h, h + 0.12, "out", who=o, tilt=-20 * d, lean=-12 * d)
        T.kf(h, h + 0.1, "out", shake=1.0)
        T.kf(h + 0.3, h + 0.5, shake=0.0)
        self.bubble(h, h + 1.0, "!", who=o)
        self.say(h + 0.05, {}, "आउच!", who=o)
        T.kf(h, h + 0.5, "in", t_x=target[0] - 120 * d, t_y=GROUND - 30, t_rot=900 * d)
        T.kf(h + 1.2, h + 1.21, "step", t_vis=0.0)
        self.cam(h, h + 0.2, 1.5, who=o, cy=900)
        self.kf(a + 0.9, a + 1.2, red=0.0, lean=0, **ARMS_DOWN)
        self.expr(a + 1.0, a + 1.2, "sneaky", smile=1.0)
        e = h + 1.4
        self.kf(e, e + 0.3, who=o, dizzy=0.0, tilt=0, lean=0)
        self.expr(e, e + 0.3, "sad", who=o, tears=0)
        self.cam_reset(e, e + 0.3)
        self.t = e + 0.35

    def chase(self, b):
        if not self.need_b():
            return self.run_away(b)
        a, o = self.t, self.other
        self.expr(a, a + 0.2, "angry", red=0.6)
        self.expr(a, a + 0.2, "scared", who=o)
        self.say(a + 0.1, b, "रुक!")
        self.say(a + 0.4, {}, "बचाओ!", who=o)
        self.kf(a, a + 0.2, who=o, flail=1.0)
        e1 = self.walk_to(-260, who=o, start=a + 0.2, run=True)
        e2 = self.walk_to(-200, start=a + 0.45, run=True)
        e = max(e1, e2) + 0.5
        self.kf(e, e + 0.01, "step", who=o, x=-260)
        self.kf(e, e + 0.01, "step", x=-460)
        home_o = HOME_B if o == "b" else HOME_A
        home_s = home_o - 260 if o == "b" else home_o + 260
        e3 = self.walk_to(home_o, who=o, start=e + 0.05, run=True)
        e4 = self.walk_to(home_s, start=e + 0.3, run=True)
        self.kf(e3 - 0.1, e3, who=o, flail=0.0, **ARMS_DOWN)
        self.expr(e4, e4 + 0.2, "sneaky", smile=1.0)
        self.expr(e3, e3 + 0.2, "sad", who=o, tears=0)
        self.t = max(e3, e4) + 0.2

    def give(self, b):
        if not self.need_b() or not self.ensure_in_hand():
            return
        o = self.other
        d = self.toward_other()
        if abs(self.x() - self.x(o)) > 300:
            self.go(self.x(o) - 240 * d)
        a = self.t
        self.face_each_other(a, a + 0.2)
        self.expr(a, a + 0.2, "happy")
        self.kf(a, a + 0.35, ua_r=70 if d > 0 else -30, la_r=80 if d > 0 else -60)
        self.kf(a + 0.15, a + 0.45, who=o, ua_r=70 if d < 0 else -30, la_r=80 if d < 0 else -60)
        T.kf(a + 0.45, a + 0.7, **{self.hk(o): 1.0})
        T.kf(a + 0.7, a + 0.71, "step", **{self.hk(): 0.0})
        SFX.append((a + 0.6, "pop", 0.4))
        self.expr(a + 0.6, a + 0.8, "excited", who=o, sparkle=1.0)
        self.say(a + 0.65, {}, "वाह!", who=o)
        self.say(a + 0.1, b)
        self.kf(a + 0.9, a + 1.2, **ARMS_DOWN)
        self.kf(a + 0.9, a + 1.2, who=o, ua_r=20, la_r=-40, sparkle=0.0)
        self.holder = o
        self.t = a + 1.3

    def snatch(self, b):
        if not self.need_b():
            return
        o = self.other
        if self.state != "hand" or self.holder != o:
            if self.state == "table":
                return self.grab(b)
            return
        d = self.toward_other()
        self.go(self.x(o) - 170 * d, run=True)
        a = self.t
        self.expr(a, a + 0.15, "sneaky", smile=1.0, px=d)
        self.kf(a, a + 0.2, ua_r=80 if d > 0 else -40, la_r=90 if d > 0 else -80)
        T.kf(a + 0.2, a + 0.21, "step", **{self.hk(): 0.0})
        T.kf(a + 0.2, a + 0.4, **{self.hk(): 1.0})
        T.kf(a + 0.4, a + 0.41, "step", **{self.hk(o): 0.0})
        SFX.append((a + 0.3, "whoosh", 0.4))
        self.say(a + 0.3, b, "हे हे!")
        self.expr(a + 0.4, a + 0.55, "shock", who=o)
        self.bubble(a + 0.4, a + 1.2, "!", who=o)
        self.kf(a + 0.5, a + 0.8, ua_r=20, la_r=-40)
        self.holder = self.who
        self.t = self.walk_to(self.x(o) - 380 * d, start=a + 0.6, run=True)
        self.expr(self.t, self.t + 0.3, "angry", who=o, red=0.7)
        self.kf(self.t + 0.6, self.t + 0.9, who=o, red=0.0)

    def high_five(self, b):
        if not self.need_b():
            return self.cheer(b)
        o = self.other
        mid = (self.x() + self.x(o)) / 2
        d = self.toward_other()
        e1 = self.walk_to(mid - 120 * d, start=self.t)
        e2 = self.walk_to(mid + 120 * d, who=o, start=self.t)
        a = max(e1, e2)
        self.expr(a, a + 0.2, "excited")
        self.expr(a, a + 0.2, "excited", who=o)
        self.kf(a, a + 0.25, ua_r=130 if d > 0 else -60, la_r=160 if d > 0 else -150)
        self.kf(a, a + 0.25, who=o, ua_l=-130 if d > 0 else 60, la_l=-160 if d > 0 else 150)
        SFX.extend([(a + 0.25, "clap", 0.7), (a + 0.25, "sparkle", 0.4)])
        self.say(a + 0.25, b, "येएए!")
        self.kf(a + 0.9, a + 1.2, **ARMS_DOWN)
        self.kf(a + 0.9, a + 1.2, who=o, **ARMS_DOWN)
        self.expr(a + 0.9, a + 1.1, "happy")
        self.expr(a + 0.9, a + 1.1, "happy", who=o)
        self.t = a + 1.25

    def point_laugh(self, b):
        if not self.need_b():
            return self.laugh(b)
        a, o = self.t, self.other
        d = self.toward_other()
        self.face_each_other(a, a + 0.2)
        self.expr(a, a + 0.2, "laugh")
        self.kf(a, a + 0.25, laugh=1.0, **(dict(ua_r=95, la_r=95) if d > 0 else dict(ua_l=-95, la_l=-95)))
        self.say(a + 0.2, b, "हा हा हा!")
        self.expr(a + 0.3, a + 0.5, "angry", who=o, red=0.6)
        T.kf(a + 0.3, a + 0.6, cz=1.25, cx=(self.x() + self.x(o)) / 2, cy=1100)
        self.t = a + 2.2

    # ───────── build ─────────
    def build(self, max_len=44.0):
        beats = [b for b in self.story.get("beats", []) if isinstance(b, dict) and b.get("action") in self.ACTIONS]
        if not beats or beats[0].get("action") != "walk_in":
            beats.insert(0, {"action": "walk_in"})
        if beats[-1].get("action") not in ("laugh", "point_laugh"):
            beats.append({"action": "laugh"})
        hook = clean_caption(self.story.get("hook")) or "देखो क्या हुआ"
        TEXTS.append((0.25, 999, hook, 230, 118 if len(hook) < 14 else 96, STYLE["accent"]))
        SFX.append((0.25, "pop", 0.6))
        for b in beats[:-1]:
            if self.t > max_len - 5:
                break
            act = b["action"]
            self.who = "b" if (b.get("who") == "b" and self.has_b) else "a"
            if act == "enter":
                self.who = "b"
            elif self.who == "b" and not self.b_in:
                self.enter({})
                self.who = "b"
            getattr(self, act)(b)
            self.t += 0.15
        last = beats[-1]
        self.who = "b" if (last.get("who") == "b" and self.b_in) else "a"
        getattr(self, last["action"])(last)
        end1 = clean_caption(self.story.get("end_text")) or "तुम भी ऐसा करते हो?"
        end2 = clean_caption(self.story.get("end_sub")) or "कमेंट करो!"
        TEXTS.append((self.t - 1.6, 999, end1, 1620, 92, (255, 255, 255)))
        TEXTS.append((self.t - 0.9, 999, end2, 1760, 80, STYLE["accent"]))
        SFX.extend([(self.t - 1.6, "pop", 0.5), (self.t - 0.9, "pop", 0.4)])
        return self.t + 1.0


PALETTES = [
    dict(wall=((255, 244, 222), (247, 222, 182)), floor=(205, 160, 110), table=(150, 95, 50)),
    dict(wall=((226, 242, 255), (196, 222, 245)), floor=(176, 140, 110), table=(120, 85, 60)),
    dict(wall=((235, 250, 230), (205, 232, 200)), floor=(196, 150, 105), table=(140, 90, 55)),
    dict(wall=((255, 232, 238), (245, 205, 215)), floor=(190, 150, 120), table=(130, 85, 70)),
    dict(wall=((244, 236, 255), (220, 205, 245)), floor=(180, 145, 115), table=(115, 80, 65)),
]
SCENES = ["room", "kitchen", "classroom", "park", "office", "bedroom_night"]


def setup(story):
    global T, DUR
    T = Tracks(DEF)
    for lst in (TEXTS, BUBBLES, VOICE, SFX, MUSIC_CUTS):
        lst.clear()
    rnd = random.Random(str(story.get("title", "")) + str(story.get("hook", "")))
    STYLE.clear()
    STYLE.update(rnd.choice(PALETTES))
    STYLE["seed"] = rnd.randint(0, 10 ** 6)
    STYLE["scene"] = story.get("scene") if story.get("scene") in SCENES else rnd.choice(SCENES[:5])
    STYLE["accent"] = rnd.choice([(255, 214, 10), (255, 120, 40), (90, 230, 120), (80, 200, 255)])
    chars = story.get("characters") if isinstance(story.get("characters"), dict) else {}
    STYLE["chars"] = {"a": make_style(chars.get("a", {"type": "plain"}), rnd),
                      "b": make_style(chars.get("b", {"type": "plain"}), rnd)}
    pname = str(story.get("prop", "samosa"))
    STYLE["prop_name"] = pname.lower()
    STYLE["prop_info"] = PR.find(pname) or ("builtin", "samosa")
    STYLE["throw_info"] = PR.find(story.get("throw_item") or "chappal") or PR.find("chappal")
    d = Director(story)
    STYLE["plate"] = d.edible
    T.k["s_hot"] = [(0.0, 1.0 if d.hot else 0.0, "io")]
    DUR = min(46.0, d.build())
    TEXTS[:] = [(a, min(b, DUR - 0.05), *rest) for a, b, *rest in TEXTS]
    return DUR


# ═════════════════════════ drawing ═════════════════════════
class Painter:
    def __init__(self, img):
        self.img = img
        self.d = ImageDraw.Draw(img)

    def line(self, pts, fill=INK, width=LINE):
        self.d.line([(x * S, y * S) for x, y in pts], fill=fill, width=max(1, int(width * S)), joint="curve")
        for x, y in (pts[0], pts[-1]):
            r = width * S / 2
            self.d.ellipse([x * S - r, y * S - r, x * S + r, y * S + r], fill=fill)

    def circle(self, c, r, fill=None, outline=None, width=0):
        x, y = c
        self.d.ellipse([(x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S], fill=fill, outline=outline,
                       width=int(width * S))

    def ellipse(self, c, rx, ry, fill=None, outline=None, width=0):
        x, y = c
        self.d.ellipse([(x - rx) * S, (y - ry) * S, (x + rx) * S, (y + ry) * S], fill=fill, outline=outline,
                       width=int(width * S))

    def poly(self, pts, fill=None, outline=None, width=0):
        self.d.polygon([(x * S, y * S) for x, y in pts], fill=fill, outline=outline, width=int(width * S))

    def paste(self, im, c, rot=0.0):
        if rot:
            im = im.rotate(-rot, resample=Image.BICUBIC, expand=True)
        self.img.paste(im, (int(c[0] * S - im.width / 2), int(c[1] * S - im.height / 2)), im)


def lerp(a, b, u):
    return a + (b - a) * u


def lerp2(a, b, u):
    return (lerp(a[0], b[0], u), lerp(a[1], b[1], u))


def quad(p0, p1, p2, n=14):
    return [((1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * p1[0] + u * u * p2[0],
             (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * p1[1] + u * u * p2[1]) for u in np.linspace(0, 1, n)]


def shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


# ───────── scenes ─────────
def _wall(d, top, bot, until):
    top, bot = np.array(top), np.array(bot)
    for yy in range(0, until * S, 4):
        c = tuple(int(v) for v in top + (bot - top) * (yy / (until * S)))
        d.rectangle([0, yy, W * S, yy + 4], fill=c)


def _rect(d, x0, y0, x1, y1, fill, outline=None, w=6, r=0):
    if r:
        d.rounded_rectangle([x0 * S, y0 * S, x1 * S, y1 * S], r * S, fill=fill, outline=outline, width=w)
    else:
        d.rectangle([x0 * S, y0 * S, x1 * S, y1 * S], fill=fill, outline=outline, width=w)


def _ell(d, x0, y0, x1, y1, fill, outline=None, w=0):
    d.ellipse([x0 * S, y0 * S, x1 * S, y1 * S], fill=fill, outline=outline, width=w)


def _window(d, wx, wy, ww, wh, night=False):
    _rect(d, wx, wy, wx + ww, wy + wh, (30, 40, 90) if night else (160, 215, 250), (120, 80, 45), 14, 8)
    if night:
        _ell(d, wx + 180, wy + 50, wx + 250, wy + 120, (245, 240, 200))
        for k in range(8):
            x, y = wx + 20 + (k * 53) % (ww - 40), wy + 30 + (k * 37) % (wh - 60)
            _ell(d, x, y, x + 6, y + 6, (255, 255, 230))
    else:
        _ell(d, wx + 190, wy + 50, wx + 260, wy + 120, (255, 225, 90))
        for cx_, cy_ in ((wx + 80, wy + 230), (wx + 120, wy + 215), (wx + 160, wy + 232)):
            _ell(d, cx_ - 45, cy_ - 30, cx_ + 45, cy_ + 30, (255, 255, 255))
    d.line([(wx + ww / 2) * S, wy * S, (wx + ww / 2) * S, (wy + wh) * S], fill=(120, 80, 45), width=10 * S)
    d.line([wx * S, (wy + wh / 2) * S, (wx + ww) * S, (wy + wh / 2) * S], fill=(120, 80, 45), width=10 * S)


def _table(d, col, top=1170):
    _rect(d, 660, top, 1040, top + 30, col, (90, 55, 25), 8, 8)
    for lx in (690, 990):
        _rect(d, lx, top + 30, lx + 26, GROUND, shade(col, 0.85), (90, 55, 25), 6)


def make_background():
    img = Image.new("RGB", (W * S, H * S))
    d = ImageDraw.Draw(img)
    sc = STYLE["scene"]
    fl, tb = STYLE["floor"], STYLE["table"]
    if sc == "park":
        _wall(d, (150, 210, 255), (215, 240, 255), GROUND)
        _ell(d, 760, 380, 900, 520, (255, 225, 90))
        for cx_, cy_ in ((250, 520), (320, 500), (600, 650), (660, 630)):
            _ell(d, cx_ - 70, cy_ - 40, cx_ + 70, cy_ + 40, (255, 255, 255))
        _rect(d, 0, 1250, W, GROUND, (120, 190, 110), None, 0)
        _rect(d, 120, 900, 175, 1300, (130, 85, 50), None, 0)
        for cx_, cy_, r in ((150, 860, 150), (60, 960, 100), (250, 960, 110)):
            _ell(d, cx_ - r, cy_ - r, cx_ + r, cy_ + r, (70, 160, 80))
        d.rectangle([0, GROUND * S, W * S, H * S], fill=(100, 170, 90))
        _rect(d, 660, 1170, 1040, 1200, (170, 110, 60), (90, 55, 25), 8, 6)
        _rect(d, 660, 1080, 1040, 1105, (170, 110, 60), (90, 55, 25), 6, 6)
        for lx in (690, 990):
            _rect(d, lx, 1200, lx + 22, GROUND, (110, 110, 110), None, 0)
    else:
        night = sc == "bedroom_night"
        wall = ((60, 70, 120), (40, 50, 95)) if night else STYLE["wall"]
        _wall(d, wall[0], wall[1], GROUND)
        flc = (90, 80, 100) if night else fl
        d.rectangle([0, GROUND * S, W * S, H * S], fill=flc)
        for yy in range(GROUND * S + 60, H * S, 120):
            d.line([0, yy, W * S, yy], fill=shade(flc, 0.92), width=4)
        d.rectangle([0, (GROUND - 18) * S, W * S, GROUND * S], fill=shade(flc, 0.8))
        if sc == "kitchen":
            for yy in range(1000, GROUND - 18, 60):
                d.line([0, yy * S, W * S, yy * S], fill=shade(wall[1], 0.93), width=3)
            for xx in range(0, W, 60):
                d.line([xx * S, 1000 * S, xx * S, (GROUND - 18) * S], fill=shade(wall[1], 0.93), width=3)
            _rect(d, 60, 560, 300, 820, (200, 160, 110), (120, 80, 45), 8, 6)
            d.line([180 * S, 560 * S, 180 * S, 820 * S], fill=(120, 80, 45), width=8)
            _rect(d, 700, 520, 1000, 760, (200, 160, 110), (120, 80, 45), 8, 6)
            d.line([850 * S, 520 * S, 850 * S, 760 * S], fill=(120, 80, 45), width=8)
            _rect(d, 640, 1150, 1080, 1200, (200, 200, 205), (110, 110, 120), 8, 6)
            _rect(d, 660, 1200, 1060, GROUND, (170, 130, 90), (110, 80, 50), 6, 0)
            d.line([860 * S, 1200 * S, 860 * S, GROUND * S], fill=(110, 80, 50), width=6)
        elif sc == "classroom":
            _rect(d, 120, 480, 960, 860, (40, 80, 60), (130, 90, 50), 18, 6)
            rnd = random.Random(STYLE["seed"])
            for k in range(3):
                y = 560 + k * 90
                x0 = 200 + rnd.randint(0, 80)
                d.line([x0 * S, y * S, (x0 + rnd.randint(200, 500)) * S, (y + rnd.randint(-8, 8)) * S],
                       fill=(230, 230, 220), width=6 * S)
            d.text((800 * S, 770 * S), "2+2=?", font=font(70 * S), fill=(235, 235, 225), anchor="mm")
            _table(d, (190, 140, 80))
        elif sc == "office":
            _window(d, 90, 520, 300, 360)
            _rect(d, 720, 560, 980, 760, (250, 250, 250), (150, 150, 150), 8, 4)
            pts = [(750, 730), (800, 690), (850, 710), (900, 640), (950, 600)]
            d.line([(x * S, y * S) for x, y in pts], fill=(220, 70, 70), width=8 * S)
            _table(d, (120, 120, 130))
            _rect(d, 900, 1040, 1020, 1130, (40, 40, 50), (20, 20, 25), 6, 6)
            _rect(d, 950, 1130, 970, 1170, (60, 60, 70), None, 0)
        elif night:
            _window(d, 90, 520, 300, 360, night=True)
            _rect(d, 60, 1250, 400, 1420, (200, 120, 140), (110, 60, 80), 8, 10)
            _rect(d, 60, 1420, 400, GROUND, (140, 90, 60), None, 0)
            _ell(d, 80, 1210, 200, 1270, (250, 250, 250), (180, 180, 180), 6)
            _table(d, (110, 80, 60))
            _rect(d, 960, 1050, 1000, 1170, (230, 200, 120), None, 0)
            d.polygon([(925 * S, 1060 * S), (1035 * S, 1060 * S), (1010 * S, 990 * S), (950 * S, 990 * S)],
                      fill=(255, 230, 150))
        else:
            _window(d, 90, 520, 300, 360)
            _rect(d, 700, 560, 940, 740, (255, 250, 240), (150, 100, 60), 12)
            d.polygon([(730 * S, 710 * S), (800 * S, 620 * S), (860 * S, 690 * S), (890 * S, 650 * S),
                       (915 * S, 710 * S)], fill=(120, 180, 120))
            _table(d, tb)
    if STYLE.get("plate"):
        _ell(d, 705, 1150, 875, 1182, (250, 250, 250), (170, 170, 170), 6)
    return img


# ───────── characters ─────────
def draw_hair_back(P, st, hx, hy, k):
    col = st.get("hair_col", INK)
    if st["hair"] == "long":
        P.poly([(hx - 98 * k, hy - 20 * k), (hx + 98 * k, hy - 20 * k), (hx + 92 * k, hy + 120 * k),
                (hx - 92 * k, hy + 120 * k)], fill=col)
    elif st["hair"] == "ponytail":
        P.ellipse((hx + 105 * k, hy + 10 * k), 26 * k, 60 * k, fill=col, outline=INK, width=4)


def draw_hair_front(P, st, hx, hy, k):
    col, r = st.get("hair_col", INK), HEAD_R * k
    hair = st["hair"]
    if hair in ("short", "long", "bun", "ponytail", "spiky"):
        pts = [(hx + math.cos(a) * r * 1.04, hy + math.sin(a) * r * 1.04)
               for a in np.linspace(math.pi * 1.05, math.pi * 1.95, 18)]
        pts += [(hx + r * 0.7, hy - r * 0.45), (hx, hy - r * 0.62), (hx - r * 0.7, hy - r * 0.45)]
        P.poly(pts, fill=col)
        if hair == "spiky":
            for j in range(5):
                a = math.pi * (1.2 + j * 0.15)
                bx, by = hx + math.cos(a) * r, hy + math.sin(a) * r
                tx, ty = hx + math.cos(a) * r * 1.35, hy + math.sin(a) * r * 1.35
                P.poly([(bx - 14 * k, by), (tx, ty), (bx + 14 * k, by)], fill=col)
        if hair == "bun":
            P.circle((hx, hy - r * 1.12), 32 * k, fill=col, outline=INK, width=4)
    elif hair == "bald":
        for s in (-1, 1):
            P.ellipse((hx + s * r * 0.95, hy - r * 0.1), 18 * k, 32 * k, fill=col, outline=INK, width=3)
    if st.get("cap"):
        cc = st.get("cap_col", (230, 60, 60))
        pts = [(hx + math.cos(a) * r * 1.06, hy + math.sin(a) * r * 1.06) for a in np.linspace(math.pi, math.pi * 2, 18)]
        P.poly(pts + [(hx + r * 1.06, hy - r * 0.25), (hx - r * 1.06, hy - r * 0.25)], fill=cc, outline=INK, width=4)
        P.poly([(hx + r * 0.2, hy - r * 0.32), (hx + r * 1.55, hy - r * 0.32), (hx + r * 1.5, hy - r * 0.18),
                (hx + r * 0.2, hy - r * 0.18)], fill=shade(cc, 0.8), outline=INK, width=4)


def draw_outfit(P, st, sk, k):
    o = st.get("outfit", "none")
    if o == "none":
        return
    col = st["outfit_col"]
    hip = sk["hip"]
    nx, ny = sk["neck"]
    if o in ("shirt", "kurta"):
        bottom = hip if o == "shirt" else lerp2(hip, lerp2(sk["kn_l"], sk["kn_r"], 0.5), 0.55)
        P.poly([(nx - 46 * k, ny + 6 * k), (nx + 46 * k, ny + 6 * k), (bottom[0] + 42 * k, bottom[1] + 8 * k),
                (bottom[0] - 42 * k, bottom[1] + 8 * k)], fill=col, outline=INK, width=5)
        if o == "kurta":
            P.line([(nx, ny + 10 * k), (nx, ny + 70 * k)], fill=shade(col, 0.6), width=5)
    elif o in ("dress", "saree"):
        knee = lerp2(sk["kn_l"], sk["kn_r"], 0.5)
        bottom = lerp2(knee, lerp2(sk["ft_l"], sk["ft_r"], 0.5), 0.15 if o == "dress" else 0.75)
        P.poly([(nx - 36 * k, ny + 6 * k), (nx + 36 * k, ny + 6 * k), (bottom[0] + 95 * k, bottom[1]),
                (bottom[0] - 95 * k, bottom[1])], fill=col, outline=INK, width=5)
        if o == "saree":
            P.poly([(nx + 36 * k, ny + 6 * k), (nx + 10 * k, ny + 6 * k), (bottom[0] - 50 * k, bottom[1] - 120 * k),
                    (bottom[0] - 20 * k, bottom[1] - 140 * k)], fill=shade(col, 0.75))
            P.line([(bottom[0] - 95 * k, bottom[1] - 6), (bottom[0] + 95 * k, bottom[1] - 6)],
                   fill=(250, 210, 80), width=8)


def draw_heart(P, c, r, col):
    x, y = c
    P.circle((x - r * 0.5, y - r * 0.2), r * 0.55, fill=col)
    P.circle((x + r * 0.5, y - r * 0.2), r * 0.55, fill=col)
    P.poly([(x - r * 1.02, y - r * 0.05), (x + r * 1.02, y - r * 0.05), (x, y + r * 1.0)], fill=col)


def draw_star(P, c, r, col=(255, 210, 40)):
    pts = []
    for j in range(10):
        a = math.pi / 2 + j * math.pi / 5
        rr = r if j % 2 == 0 else r * 0.45
        pts.append((c[0] + math.cos(a) * rr, c[1] - math.sin(a) * rr))
    P.poly(pts, fill=col, outline=(180, 120, 0), width=2)


def draw_face(P, c, st, hx, hy, k, t):
    red = c["red"]
    P.circle((hx, hy), HEAD_R * k, fill=(255, int(lerp(255, 95, red)), int(lerp(255, 75, red))), outline=INK,
             width=LINE * 0.75)
    draw_hair_front(P, st, hx, hy, k)
    lk = c["look"] * 16 * k
    ex_off, ey = 34 * k, hy - 12 * k
    if c["cool"] < 0.95:
        for side in (-1, 1):
            ex = hx + side * ex_off + lk
            eo = c["eye"]
            if c["love"] > 0.3:
                draw_heart(P, (ex, ey), 22 * k * (0.9 + 0.1 * math.sin(t * 12)), (230, 40, 70))
            elif eo < 0.15:
                if c["smile"] > 0.3 or c["zzz"] > 0.3:
                    bend = 1 if c["smile"] > 0.3 else -0.3
                    P.line(quad((ex - 18 * k, ey + 6 * k), (ex, ey - 16 * k * bend), (ex + 18 * k, ey + 6 * k)),
                           width=7 * k)
                else:
                    P.line([(ex - 16 * side * k, ey - 12 * k), (ex + 14 * side * k, ey), (ex - 16 * side * k, ey + 12 * k)],
                           width=7 * k)
            else:
                rx, ry = 21 * k * min(1.25, 0.85 + 0.25 * eo), 25 * k * eo
                P.ellipse((ex, ey), rx, ry, fill=(255, 255, 255), outline=INK, width=5 * k)
                pr = min(11 * k * c["psize"], rx - 4, ry - 2)
                if pr > 2:
                    pxx = ex + c["px"] * (rx - pr - 3)
                    pyy = ey + c["py"] * max(0.0, ry - pr - 3)
                    P.circle((pxx, pyy), pr, fill=(20, 20, 20))
                    P.circle((pxx - pr * 0.35, pyy - pr * 0.35), pr * 0.3, fill=(255, 255, 255))
            by = ey - 40 * k - c["browy"] * 12 * k - (eo - 1) * 8 * k
            b = c["brow"]
            P.line([(ex - side * 18 * k, by - b * 11 * k), (ex + side * 20 * k, by + b * 4 * k)], width=7 * k)
            if c["sparkle"] > 0.05:
                s_ = (10 + 8 * math.sin(t * 18 + side)) * k
                cx_, cy_ = ex + side * 12 * k, ey - 14 * k
                for a in (0, 90):
                    dx, dy = vec(a, s_ * c["sparkle"])
                    P.line([(cx_ - dx, cy_ - dy), (cx_ + dx, cy_ + dy)], fill=(255, 255, 255), width=4)
            if st.get("glasses"):
                P.circle((ex, ey), 30 * k, outline=(40, 40, 40), width=5 * k)
        if st.get("glasses"):
            P.line([(hx - 6 * k + lk, ey), (hx + 6 * k + lk, ey)], fill=(40, 40, 40), width=5 * k)
    if c["cool"] > 0.05:
        dy = (1 - c["cool"]) * -160 * k
        for s in (-1, 1):
            P.poly([(hx + s * 8 * k + lk, ey - 18 * k + dy), (hx + s * 62 * k + lk, ey - 22 * k + dy),
                    (hx + s * 56 * k + lk, ey + 16 * k + dy), (hx + s * 14 * k + lk, ey + 16 * k + dy)], fill=(15, 15, 20))
        P.line([(hx - 10 * k + lk, ey - 14 * k + dy), (hx + 10 * k + lk, ey - 14 * k + dy)], width=5 * k)
    if st.get("bindi"):
        P.circle((hx + lk, hy - 52 * k), 6 * k, fill=(220, 30, 40))
    mx, my = hx + lk * 0.9, hy + 42 * k
    if st.get("mustache"):
        mc = st.get("mustache_col", (40, 30, 25))
        P.poly([(mx - 36 * k, my - 6 * k), (mx, my - 20 * k), (mx + 36 * k, my - 6 * k), (mx + 30 * k, my - 14 * k),
                (mx, my - 26 * k), (mx - 30 * k, my - 14 * k)], fill=mc)
    sm, mo = c["smile"], c["mopen"]
    if c["mround"] > 0.5:
        r = (8 + 14 * mo) * k
        P.ellipse((mx, my), r * 0.9, r, fill=(110, 20, 25), outline=INK, width=5 * k)
    elif mo < 0.06:
        P.line(quad((mx - 28 * k, my - sm * 10 * k), (mx, my + sm * 16 * k), (mx + 28 * k, my - sm * 10 * k)), width=7 * k)
    else:
        w_ = (28 + 6 * mo) * k
        l, r_ = (mx - w_, my - sm * 10 * k), (mx + w_, my - sm * 10 * k)
        upper = quad(l, (mx, my - sm * 2 * k - mo * 14 * k), r_)
        lower = quad(r_, (mx, my + sm * 16 * k + mo * 52 * k), l)
        P.poly(upper + lower, fill=(110, 20, 25), outline=INK, width=5 * k)
        if mo > 0.4:
            P.ellipse((mx, my + sm * 6 * k + mo * 26 * k), w_ * 0.45, (7 + 5 * mo) * k, fill=(235, 110, 120))
    if c["tears"] > 0.05:
        for side in (-1, 1):
            for j in range(3):
                ph = (t * 1.6 + j / 3) % 1
                a_ = c["tears"] * (1 - ph)
                P.ellipse((hx + side * (ex_off + 6 * k) + lk, ey + 22 * k + ph * 140 * k), 7 * a_ * k + 1,
                          10 * a_ * k + 1, fill=(90, 170, 255))
    if c["sweat"] > 0.05:
        j = (t * 1.3) % 1
        P.ellipse((hx + HEAD_R * 0.85 * k, hy - 30 * k + j * 60 * k), 9 * c["sweat"] * k, 14 * c["sweat"] * k,
                  fill=(120, 190, 255), outline=(60, 120, 200), width=2)
    if c["steam"] > 0.05:
        for side in (-1, 1):
            for j in range(3):
                ph = (t * 2.2 + j / 3) % 1
                P.circle((hx + side * (HEAD_R * k + 10 + ph * 60), hy - 10 - ph * 90), (14 + ph * 26) * c["steam"] * k,
                         fill=(250, 250, 250), outline=(200, 200, 200), width=3)
    if c["love"] > 0.05:
        for j in range(3):
            ph = (t * 0.8 + j / 3) % 1
            draw_heart(P, (hx - 80 * k + j * 80 * k, hy - 120 * k - ph * 150), 16 * k * c["love"] * (1 - ph * 0.5),
                       (240, 60, 100))
    if c["zzz"] > 0.05:
        for j in range(3):
            ph = (t * 0.7 + j / 3) % 1
            P.d.text(((hx + 90 * k + ph * 80) * S, (hy - 90 * k - ph * 140) * S), "Z",
                     font=font((30 + 30 * ph) * S * k), fill=(90, 90, 200), anchor="mm",
                     stroke_width=3 * S, stroke_fill=(255, 255, 255))
    if c["dizzy"] > 0.05:
        for j in range(4):
            a_ = t * 6 + j * math.pi / 2
            draw_star(P, (hx + math.cos(a_) * 90 * k, hy - 110 * k + math.sin(a_) * 22 * k), 14 * k * c["dizzy"])


def draw_fire(P, m, f, t):
    ln = 330 * f
    for j, col in enumerate(((255, 90, 20), (255, 170, 30), (255, 240, 120))):
        fl = 1 - j * 0.25
        pts = []
        for i in range(10):
            u = i / 9
            pts.append((m[0] + 10 + ln * fl * u, m[1] - (16 + 60 * u) * fl + math.sin(t * 40 + i * 2 + j) * 14))
        for i in range(9, -1, -1):
            u = i / 9
            pts.append((m[0] + 10 + ln * fl * u, m[1] + (16 + 60 * u) * fl + math.cos(t * 37 + i * 2 + j) * 14))
        P.poly(pts, fill=col)


def draw_character(P, c, st, t, held=None):
    k = st["scale"]
    sk = skeleton(c, k)
    hx, hy = sk["head"]
    lw = LINE * (0.85 + 0.15 * k)
    draw_hair_back(P, st, hx, hy, k)
    P.line([sk["hip"], sk["kn_l"], sk["ft_l"]], width=lw)
    P.line([sk["hip"], sk["kn_r"], sk["ft_r"]], width=lw)
    P.line([sk["hip"], sk["neck"]], width=lw)
    draw_outfit(P, st, sk, k)
    P.line([sk["shoulder"], sk["el_l"], sk["hd_l"]], width=lw)
    draw_face(P, c, st, hx, hy, k, t)
    if held:
        held(P, sk)
    P.line([sk["shoulder"], sk["el_r"], sk["hd_r"]], width=lw)
    for h in ("hd_l", "hd_r"):
        P.circle(sk[h], 14 * k, fill=INK)
    if c["thumb"] > 0.5:
        x_, y_ = sk["hd_l"]
        P.line([(x_, y_), (x_ + 4, y_ - 34 * k)], width=12 * k)
    if c["wind"] > 0.05:
        m = sk["mouth"]
        for j in range(3):
            ph = (t * 3 + j / 3) % 1
            x0 = m[0] + 40 + ph * 120
            y0 = m[1] - 20 + j * 22
            P.line([(x0, y0), (x0 + 50 * c["wind"], y0 + 6)], fill=(120, 170, 220), width=6)
    if c["fire"] > 0.05:
        draw_fire(P, sk["mouth"], c["fire"], t)
    return sk


# ───────── props ─────────
def _rot_pts(c, rot, scale, pts):
    r = math.radians(rot)
    cs, sn = math.cos(r), math.sin(r)
    return [(c[0] + (x * cs - y * sn) * scale, c[1] + (x * sn + y * cs) * scale) for x, y in pts]


def _ring(cx, cy, rx, ry, n=16):
    return [(cx + math.cos(2 * math.pi * j / n) * rx, cy + math.sin(2 * math.pi * j / n) * ry) for j in range(n)]


def draw_builtin(P, kind, c, rot, scale):
    ink = (40, 30, 25)
    poly = lambda pts, fill, w=5: P.poly(_rot_pts(c, rot, scale, pts), fill=fill, outline=ink, width=w)
    dot = lambda x, y, r, fill: P.circle(_rot_pts(c, rot, scale, [(x, y)])[0], r * scale, fill=fill)
    if kind == "laddoo":
        poly(_ring(0, 0, 36, 36), (255, 170, 30))
        for x, y in ((-12, -10), (10, -14), (14, 8), (-8, 14), (0, 0)):
            dot(x, y, 3, (220, 120, 10))
    elif kind == "jalebi":
        for j in range(3):
            P.line(_rot_pts(c, rot, scale, _ring(0, 0, 38 - j * 12, 38 - j * 12, 14) + [(38 - j * 12, 0)]),
                   fill=(255, 150, 20), width=9 * scale)
    else:
        poly([(0, -48), (42, 24), (-42, 24)], (235, 165, 60), 6)
        for x, y in ((-10, 0), (12, 6), (0, 14)):
            dot(x, y, 3, (170, 100, 35))


def draw_prop(P, info, c, rot, scale, hot, t, size=105):
    if scale < 0.03 or not info:
        return
    if info[0] == "builtin":
        draw_builtin(P, info[1], c, rot, scale)
    else:
        im = PR.image(info[0], str(info[1]), int(size * S))
        if scale < 0.98:
            im = im.resize((max(2, int(im.width * scale)), max(2, int(im.height * scale))), Image.LANCZOS)
        P.paste(im, c, rot)
    if hot > 0.05:
        for j in range(3):
            ph = (t * 0.9 + j / 3) % 1
            x0 = c[0] - 26 + j * 26
            pts_ = [(x0 + 10 * math.sin(ph * 6 + i), c[1] - 60 - i * 14 - ph * 30) for i in range(5)]
            P.line(pts_, fill=(235, 235, 235), width=5 * hot * (1 - ph) + 1)


def draw_glass(P, c, tilt, level):
    a = math.radians(tilt)
    def rot(dx, dy):
        return (c[0] + dx * math.cos(a) - dy * math.sin(a), c[1] + dx * math.sin(a) + dy * math.cos(a))
    if level > 0.02:
        top = 45 - 85 * level
        P.poly([rot(-20 - 6 * (1 - (top + 45) / 90), top), rot(20 + 6 * (1 - (top + 45) / 90), top),
                rot(20, 45), rot(-20, 45)], fill=(110, 185, 255))
    P.poly([rot(-26, -45), rot(26, -45), rot(20, 45), rot(-20, 45)], outline=(80, 120, 160), width=5)


def draw_bubble(P, pos, t, t0, ch):
    sc = ease(min(1.0, (t - t0) / 0.15), "back")
    x, y = pos
    if ch == "💡":
        P.circle((x, y), 44 * sc, fill=(255, 235, 90), outline=(200, 150, 0), width=5)
        P.poly([(x - 20 * sc, y + 40 * sc), (x + 20 * sc, y + 40 * sc), (x + 16 * sc, y + 66 * sc),
                (x - 16 * sc, y + 66 * sc)], fill=(150, 150, 160))
        for a in range(0, 360, 45):
            dx, dy = vec(a, 70 * sc)
            P.line([(x + dx * 0.8, y + dy * 0.8), (x + dx, y + dy)], fill=(255, 200, 40), width=5)
        return
    P.d.text((x * S, y * S), ch, font=font(130 * sc * S), fill=(230, 40, 40), anchor="mm", stroke_width=6 * S,
             stroke_fill=(255, 255, 255))


def draw_frame(bg, p, t):
    img = bg.copy()
    P = Painter(img)
    ca, cb = char_params(p, "a"), char_params(p, "b")
    sa = skeleton(ca, STYLE["chars"]["a"]["scale"])
    sb = skeleton(cb, STYLE["chars"]["b"]["scale"])
    sp = (p["s_x"], p["s_y"])
    sp = lerp2(sp, sa["hd_r"], p["s_hand"])
    sp = lerp2(sp, sb["hd_r"], p["s_handb"])
    sp = lerp2(sp, (sa["mouth"][0] + 10, sa["mouth"][1] + 5), p["s_mouth"])
    sp = lerp2(sp, (sb["mouth"][0] + 10, sb["mouth"][1] + 5), p["s_mouthb"])
    gs = sa if p["g_who"] < 0.5 else sb
    gp = lerp2(GLASS_ON_TABLE, (gs["hd_r"][0] + 8, gs["hd_r"][1] - 20), p["g_hand"])
    gp = lerp2(gp, (gs["mouth"][0] + 50, gs["mouth"][1] - 15), p["g_mouth"])
    held_by = "a" if p["s_hand"] + p["s_mouth"] > 0.5 else ("b" if p["s_handb"] + p["s_mouthb"] > 0.5 else None)
    ring = p["ring"]
    rot = p["s_rot"] + (math.sin(t * 60) * 18 * ring if ring > 0.05 else 0)

    def held(Pn, sk):
        draw_prop(Pn, STYLE["prop_info"], sp, rot, p["s_scale"], p["s_hot"], t)

    if held_by is None and p["s_vis"] > 0.5:
        draw_prop(P, STYLE["prop_info"], sp, rot, p["s_scale"], p["s_hot"], t)
        if ring > 0.05:
            for j in (-1, 1):
                P.line([(sp[0] + j * 60, sp[1] - 40), (sp[0] + j * 80, sp[1] - 60)], fill=(230, 60, 60), width=6)
    glass_late = p["g_mouth"] >= 0.5
    if not glass_late:
        draw_glass(P, gp, -70 * p["g_mouth"], p["g_level"])
    if p["dust"] > 0.02:
        for j in range(7):
            a = math.radians(-80 + j * 27)
            rr = 40 + 90 * (1 - p["dust"])
            P.circle((p["dust_x"] + math.sin(a) * rr * 1.6, GROUND - 20 - abs(math.cos(a)) * rr * 0.6), 26 * p["dust"],
                     fill=(200, 185, 160))
    for who, c in (("b", cb), ("a", ca)):
        if c["vis"] > 0.5:
            draw_character(P, c, STYLE["chars"][who], t, held if held_by == who else None)
    if glass_late:
        draw_glass(P, gp, -70 * p["g_mouth"], p["g_level"])
    if p["t_vis"] > 0.5:
        draw_prop(P, STYLE["throw_info"], (p["t_x"], p["t_y"]), p["t_rot"], 1.0, 0, t, size=90)
    for t0, t1, ch, who in BUBBLES:
        if t0 <= t < t1:
            s = sa if who == "a" else sb
            k = STYLE["chars"][who]["scale"]
            draw_bubble(P, (s["head"][0] + 110 * k, s["head"][1] - 120 * k), t, t0, ch)
    z = max(1.0, p["cz"])
    if p["shake"] > 0.01:
        z = max(z, 1.05)
    cw, ch_ = W * S / z, H * S / z
    cx, cy = p["cx"] * S, p["cy"] * S
    if p["shake"] > 0.01:
        cx += random.uniform(-1, 1) * 14 * S * p["shake"]
        cy += random.uniform(-1, 1) * 14 * S * p["shake"]
    cx = min(max(cx, cw / 2), W * S - cw / 2)
    cy = min(max(cy, ch_ / 2), H * S - ch_ / 2)
    out = img.resize((W, H), Image.LANCZOS, box=(cx - cw / 2, cy - ch_ / 2, cx + cw / 2, cy + ch_ / 2))
    d = ImageDraw.Draw(out)
    for t0, t1, txt, y, size, fill in TEXTS:
        if t0 <= t < t1:
            sc = ease((t - t0) / 0.25, "back")
            d.text((W / 2, y), txt, font=font(size * sc), fill=fill, anchor="mm",
                   stroke_width=max(1, int(9 * sc)), stroke_fill=(20, 20, 20))
    return out


# ═════════════════════════ audio ═════════════════════════
def env(n, attack=0.005, decay=6.0):
    tt = np.arange(n) / SR
    return np.minimum(1, tt / attack) * np.exp(-decay * tt)


def tone(freqs, dur, harm=(1.0,), decay=6.0):
    n = int(dur * SR)
    f = np.asarray(freqs, dtype=float)
    f = np.full(n, float(f)) if f.ndim == 0 else f[:n]
    ph = 2 * np.pi * np.cumsum(f) / SR
    return sum(a * np.sin(ph * (j + 1)) for j, a in enumerate(harm)) * env(len(f), decay=decay)


def lowpass(x, a):
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += a * (x[i] - acc)
        y[i] = acc
    return y


_SFX = {}


def sfx(name):
    if name in _SFX:
        return _SFX[name]
    rng = np.random.default_rng(sum(map(ord, name)))
    noise = lambda d: rng.uniform(-1, 1, int(d * SR))
    if name == "pop":
        s = tone(np.linspace(500, 1400, int(0.08 * SR)), 0.08, decay=40)
    elif name == "boing":
        tt = np.arange(int(0.6 * SR)) / SR
        s = tone(180 + 260 * tt / 0.6 + 60 * np.sin(2 * np.pi * 9 * tt), 0.6, harm=(1, 0.3), decay=4)
    elif name == "whoosh":
        x = lowpass(noise(0.5), 0.15)
        s = x * np.sin(np.linspace(0, np.pi, len(x))) * 2.5
    elif name == "splat":
        s = tone(np.linspace(140, 50, int(0.3 * SR)), 0.3, decay=12)
        b = lowpass(noise(0.25), 0.4) * env(int(0.25 * SR), decay=14) * 2
        s[: len(b)] += b
    elif name == "thud":
        s = tone(np.linspace(110, 45, int(0.25 * SR)), 0.25, decay=14)
    elif name == "sizzle":
        x = noise(0.7)
        x = x - lowpass(x, 0.05)
        s = x * env(len(x), decay=3) * (0.6 + 0.4 * np.sin(np.arange(len(x)) / SR * 2 * np.pi * 30))
    elif name == "sparkle":
        s = np.zeros(int(0.9 * SR))
        for j, f in enumerate((1568, 2093, 2637, 3136)):
            tt = tone(f, 0.5, harm=(1, 0.2), decay=8)
            i = int(j * 0.07 * SR)
            s[i:i + len(tt)] += tt[: len(s) - i] * 0.6
    elif name == "ding":
        s = tone(1760, 1.0, harm=(1, 0.4, 0.15), decay=4)
    elif name == "trombone":
        parts = []
        for f, d in ((311, 0.35), (293, 0.35), (277, 0.35), (262, 1.1)):
            tt = np.arange(int(d * SR)) / SR
            vib = 1 + (0.03 * np.sin(2 * np.pi * 6 * tt) if d > 1 else 0)
            parts.append(tone(f * vib, d, harm=(1, 0.6, 0.45, 0.3, 0.2), decay=1.2 if d > 1 else 2))
        s = np.concatenate(parts) * 0.6
    elif name == "crunch":
        s = np.zeros(int(0.25 * SR))
        for j in range(5):
            b = rng.uniform(-1, 1, int(0.02 * SR)) * env(int(0.02 * SR), decay=120)
            i = int(j * 0.04 * SR + rng.uniform(0, 0.01) * SR)
            s[i:i + len(b)] += b
    elif name == "gulp":
        s = tone(np.linspace(320, 110, int(0.18 * SR)), 0.18, harm=(1, 0.3), decay=10)
    elif name == "fire":
        x = lowpass(noise(1.6), 0.06) * 3
        s = x * np.minimum(1, np.arange(len(x)) / (0.05 * SR)) * np.exp(-1.2 * np.arange(len(x)) / SR)
    elif name == "step":
        s = tone(np.linspace(160, 70, int(0.06 * SR)), 0.06, decay=50)
    elif name == "tiptoe":
        s = tone(np.linspace(900, 600, int(0.04 * SR)), 0.04, decay=80)
    elif name == "clap":
        s = lowpass(noise(0.12), 0.6) * env(int(0.12 * SR), decay=40) * 2
    elif name == "ring":
        tt = np.arange(int(1.2 * SR)) / SR
        s = np.sin(2 * np.pi * 1800 * tt) * (np.sin(2 * np.pi * 18 * tt) > 0) * 0.6
    elif name == "snore":
        tt = np.arange(int(1.4 * SR)) / SR
        s = lowpass(noise(1.4), 0.05) * 4 * np.sin(np.pi * tt / 1.4) * (0.6 + 0.4 * np.sin(2 * np.pi * 40 * tt))
    elif name == "chatter":
        s = np.zeros(int(1.0 * SR))
        for j in range(14):
            b = tone(np.linspace(1200, 900, int(0.02 * SR)), 0.02, decay=100)
            i = int(j * 0.07 * SR)
            s[i:i + len(b)] += b
    else:
        s = np.zeros(10)
    _SFX[name] = s
    return s


def pluck(f, dur, decay, rng):
    n = int(dur * SR)
    period = max(2, int(SR / f))
    buf = rng.uniform(-1, 1, period)
    out = np.empty(n)
    for i in range(n):
        j = i % period
        out[i] = buf[j]
        buf[j] = decay * 0.5 * (buf[j] + buf[(j + 1) % period])
    return out


def music(total, seed):
    """Har story ki alag dhun (pentatonic, seed se)."""
    rnd = random.Random(seed)
    step = 60 / rnd.choice([112, 120, 128, 136, 144]) / 2
    root = rnd.choice([196.0, 220.0, 246.9, 261.6, 293.7])
    scale = [0, 2, 4, 7, 9, 12, 14, 16]
    mel = [None if rnd.random() < 0.3 else rnd.choice(scale) for _ in range(32)]
    bass = [rnd.choice([0, 5, 7, -5]) for _ in range(4)]
    out = np.zeros(int(total * SR) + SR)
    cache = {}
    rng = np.random.default_rng(seed % 1000)

    def note(f, d, dec):
        key = (round(f, 1), d, dec)
        if key not in cache:
            cache[key] = pluck(f, d, dec, rng)
        return cache[key]
    i, tt = 0, 0.0
    while tt < total:
        n = mel[i % len(mel)]
        if n is not None:
            s = note(root * 2 ** (n / 12), 0.35, 0.993)
            j = int(tt * SR); out[j:j + len(s)] += s * 0.5
        if i % 4 == 0:
            s = note(root / 2 * 2 ** (bass[(i // 8) % 4] / 12), 0.5, 0.995)
            j = int(tt * SR); out[j:j + len(s)] += s * 0.7
        tt += step; i += 1
    return out[: int(total * SR)]


def tts_wav(text, who):
    import edge_tts
    CACHE.mkdir(exist_ok=True)
    voice, pitch = VOICES.get(STYLE["chars"][who].get("voice", "m"), VOICES["m"])
    p = f"{pitch + 20:+d}Hz"
    mp3 = CACHE / f"{hashlib.md5((text + voice + p).encode()).hexdigest()[:12]}.mp3"
    wav = mp3.with_suffix(".wav")
    if not wav.exists():
        async def go():
            await edge_tts.Communicate(text, voice, rate="+5%", pitch=p).save(str(mp3))
        asyncio.run(go())
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", str(SR), str(wav)],
                       check=True)
    with wave.open(str(wav)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def build_audio(step_events, path):
    n = int(DUR * SR)
    mix = np.zeros(n + SR * 2)

    def put(sig, t, gain):
        i = int(t * SR)
        if 0 <= i < n:
            sig = sig[: len(mix) - i]
            mix[i:i + len(sig)] += sig * gain

    vol = np.ones(n)
    ramp = int(0.3 * SR)
    for a, b in MUSIC_CUTS:
        ia, ib = max(0, int(a * SR)), min(n, int(b * SR))
        vol[ia:ib] = 0
        vol[ib:ib + ramp] = np.linspace(0, 1, len(vol[ib:ib + ramp]))
    vol[-int(0.8 * SR):] *= np.linspace(1, 0, int(0.8 * SR))
    mix[:n] += music(DUR, STYLE["seed"]) * 0.16 * vol
    for t, name, g in SFX:
        put(sfx(name), t, g)
    for t, kind in step_events:
        put(sfx(kind), t, 0.3)
    for t, text, who in VOICE:
        try:
            put(tts_wav(text, who), t, 1.0)
        except Exception as e:  # noqa: BLE001
            print(f"   ⚠️  voice '{text}' nahi bani: {e}")
    mix = mix[:n]
    mix = mix / max(1e-6, np.max(np.abs(mix))) * 0.9
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())


# ═════════════════════════ main ═════════════════════════
def frames_params():
    out, steps = [], []
    ph = {"": 0.0, "b_": 0.0}
    last = {"": 0, "b_": 0}
    for f in range(int(DUR * FPS)):
        t = f / FPS
        p = T.sample(t)
        for pre in ("", "b_"):
            if p[pre + "walk"] > 0.05 and p[pre + "vis"] > 0.5:
                ph[pre] += 2 * math.pi * p[pre + "walk_hz"] / FPS
                k = int(ph[pre] // math.pi)
                if k != last[pre]:
                    steps.append((t, "tiptoe" if p[pre + "walk_hz"] < 1.3 else "step"))
                    last[pre] = k
            p[pre + "_phase"] = ph[pre]
        p["_t"] = t
        out.append(p)
    return out, steps


def render(story, mp4, preview_dir=None, preview_times=None):
    setup(story)
    random.seed(7)
    bg = make_background()
    params, steps = frames_params()
    if preview_dir:
        Path(preview_dir).mkdir(parents=True, exist_ok=True)
        for old in Path(preview_dir).glob("p_*.png"):
            old.unlink()
        times = preview_times or [f / FPS for f in range(0, len(params), int(FPS * 1.5))]
        for t in times:
            f = min(len(params) - 1, int(t * FPS))
            draw_frame(bg, params[f], f / FPS).save(Path(preview_dir) / f"p_{f / FPS:05.1f}.png")
        return DUR
    mp4 = Path(mp4)
    mp4.parent.mkdir(parents=True, exist_ok=True)
    wav = mp4.with_suffix(".wav")
    print(f"🔊 audio... ({DUR:.1f}s)")
    build_audio(steps, wav)
    print("🎞️  frames...")
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(wav),
                           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(mp4)],
                          stdin=subprocess.PIPE)
    for i, p in enumerate(params):
        ff.stdin.write(draw_frame(bg, p, i / FPS).tobytes())
        if i % 150 == 0:
            print(f"   {i / FPS:4.1f}s / {DUR:.0f}s", flush=True)
    ff.stdin.close()
    if ff.wait() != 0:
        raise RuntimeError("ffmpeg fail")
    wav.unlink(missing_ok=True)
    print("✅", mp4)
    return DUR


def main():
    ap = argparse.ArgumentParser(description="story.json -> stickman mp4")
    ap.add_argument("story")
    ap.add_argument("out", nargs="?", default=str(OUT / "video.mp4"))
    ap.add_argument("--preview", action="store_true", help="sirf PNG frames (out/preview/)")
    args = ap.parse_args()
    story = json.loads(Path(args.story).read_text())
    render(story, args.out, OUT / "preview" if args.preview else None)


if __name__ == "__main__":
    main()
