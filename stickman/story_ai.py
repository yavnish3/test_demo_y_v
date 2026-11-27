"""AI (Groq ya xAI Grok) se nayi stickman kahani + missing props ki drawing.

    GROQ_API_KEY=... python story_ai.py           # ek kahani print
Env: GROQ_API_KEY ya XAI_API_KEY (ek zaroori), AI_MODEL / GROK_MODEL (optional, warna khud chunta hai)
Key "gsk_..." -> Groq (api.groq.com), "xai-..." -> xAI Grok (api.x.ai)
"""
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

import engine
import props as PR

XAI_API, GROQ_API = "https://api.x.ai/v1", "https://api.groq.com/openai/v1"
PREFERRED = {
    XAI_API: ["grok-4-fast-non-reasoning", "grok-4-fast", "grok-4", "grok-3-mini", "grok-3"],
    GROQ_API: ["openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct-0905", "moonshotai/kimi-k2-instruct",
               "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "llama-3.1-8b-instant"],
}
API = GROQ_API


def get_key():
    """(key, base_url) — env se. Prefix se provider pehchano."""
    for name in ("GROQ_API_KEY", "XAI_API_KEY", "GROK_API_KEY"):
        k = os.environ.get(name, "").strip()
        if k:
            if k.startswith("xai-"):
                return k, XAI_API
            if k.startswith("gsk_"):
                return k, GROQ_API
            return k, XAI_API if name != "GROQ_API_KEY" else GROQ_API
    return "", GROQ_API

THEMES = [   # real life — jo har Indian ke saath hota hai
    "WiFi bilkul slow", "phone 1% battery", "Monday subah office", "salary ka din vs month end",
    "rishtedaar marks poochte hain", "light chali gayi garmi mein", "online order galat aaya", "OTP late aaya",
    "traffic jam mein late", "sardi mein thande paani se nahana", "raat ko machhar", "group project mein akela kaam",
    "exam ki raat padhai", "mummy ki chappal", "alarm aur 5 minute aur", "gym ka pehla din", "diet ka pehla din",
    "shaadi ka khana", "dost ka lunchbox", "teacher ka surprise test", "bhai-behen ka remote jhagda",
    "doodh ubal gaya", "garam chai jal gayi", "report card ka din", "cockroach ka dar", "online class mein so jaana",
    "selfie ka chakkar", "auto wala extra paise", "bill split karna", "WhatsApp group mein good morning",
    "naya phone gir gaya", "earphone ulajh gaye", "fridge mein khana chori", "neend nahi aati raat ko",
    "boss ka call chhutti pe", "cricket mein galat shot", "barish mein chhata ud gaya", "garmi mein AC",
]

FACT_TOPICS = ["jaanwar", "insaan ka sharir", "antariksh / chand / suraj", "khana", "bharat", "itihaas",
               "samundar", "science", "paudhe", "pakshi", "keede-makode", "duniya ke record"]


def _req(path, body=None, key=None, timeout=90):
    global API
    k, base = get_key()
    if key and key == k:
        API = base
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "stickman-shorts/1.0"},
                                 method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def pick_model(key):
    env = (os.environ.get("AI_MODEL") or os.environ.get("GROK_MODEL") or "").strip()
    _, base = get_key()
    pref = PREFERRED[base]
    if env:
        return env
    try:
        ids = [m["id"] for m in _req("/models", key=key).get("data", [])]
    except Exception:  # noqa: BLE001
        return pref[0]
    for p in pref:
        if p in ids:
            return p
    bad = ("image", "vision", "whisper", "tts", "guard", "embed", "audio")
    chat = [i for i in ids if not any(b in i for b in bad)]
    return chat[0] if chat else pref[0]


def parse_json(text):
    text = str(text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def chat(messages, key, model, temperature=1.0, tries=3):
    last, json_mode = None, True
    for k in range(tries + 1):
        body = {"model": model, "messages": messages, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            res = _req("/chat/completions", body, key=key)
            return parse_json(res["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as e:
            last = e
            msg = e.read()[:300]
            if e.code == 400 and json_mode:          # kuch models json mode nahi lete
                json_mode = False
                continue
            if e.code in (400, 401, 403, 404):
                raise RuntimeError(f"AI API error {e.code}: {msg!r}") from e
            time.sleep(5 * (k + 1))                  # 429 rate limit / 5xx
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as e:
            last = e
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"AI API fail: {last}")


def system_prompt():
    acts = "\n".join(f'- "{k}": {v}' for k, v in engine.Director.ACTIONS.items())
    types = ", ".join(engine.CHAR_TYPES)
    scenes = ", ".join(engine.SCENES)
    props = ", ".join(PR.sample_names(160))
    return f"""Tum ek viral Hindi YouTube Shorts comedy writer ho. Stickman cartoon, Mr.-Bean jaisi SILENT comedy:
koi dialogue NAHI — sirf chehre ke expressions, body action aur chhoti expression-awaazein (jaise "ओहो!", "अरे!",
"हा हा हा", "उफ्फ", "आह्ह!", "हम्म्म...", "बचाओ!", "ऐ!"). Kahani 25-40 second, relatable desi situation,
REAL LIFE se judi (jo har Indian ke saath hota hai — ghar, school, office, rishtedaar, phone, traffic),
shuru mein hook, beech mein twist, end mein punchline. Har baar BILKUL NAYI kahani.

Sirf ye JSON do:
{{
 "title": "YouTube title, Hindi + 1-2 emoji, max 70 akshar, end mein #shorts",
 "description": "2 line Hindi + 5-8 hashtags (#shorts #comedy #stickman #funny #hindi ...)",
 "hook": "screen ke upar text, Hindi, max 18 akshar",
 "scene": "ek: {scenes}",
 "prop": "kahani ki main cheez, ENGLISH noun (1-3 shabd), jaise samosa, alarm clock, pizza, mobile phone, cockroach",
 "prop_edible": true/false,
 "prop_hot": true/false,
 "throw_item": "(optional) phenkne wali cheez English mein, jaise chappal, pillow, tomato",
 "characters": {{"a": {{"type": "...", "outfit_color": "red/blue/green/yellow/orange/pink/purple/sky", "glasses": false, "cap": false}},
                "b": {{...}} ya null (b sirf tab jab kahani mein doosra character chahiye)}},
 "beats": [{{"action": "...", "who": "a" ya "b", "voice": "chhoti expression awaaz (optional, max 3 shabd)", "text": "sirf caption ke liye"}}],
 "end_text": "end question Hindi max 22 akshar (comment karwane wala)",
 "end_sub": "max 14 akshar, jaise 'कमेंट करो!'"
}}

Character types: {types}
Actions (sirf yahi use karo, 8-16 beats):
{acts}

Rules:
- Pehla beat "walk_in". Aakhri beat "laugh" ya "point_laugh".
- "eat" sirf tab jab prop_edible true. Do-character actions (scold, throw_item, chase, give, snatch, high_five,
  point_laugh) sirf tab jab characters.b ho.
- voice mein KOI vakya nahi, sirf expression-awaaz. caption max 1-2 baar.
- Prop library mein ye jaise naam hain (koi bhi English noun chal jayega): {props}
"""


def fact_prompt():
    types = ", ".join(engine.CHAR_TYPES)
    scenes = ", ".join(engine.SCENES)
    return f"""Tum ek viral Hindi YouTube Shorts creator ho. Format: "क्या आप जानते हो?" FUN FACT video, stickman cartoon.
Stickman screen pe fact cards dikhata hai aur har fact pe funny reaction deta hai. Koi dialogue NAHI, sirf chhoti
expression-awaazein ("वाह!", "क्या?!", "अरे बाप रे!").

SABSE ZAROORI: facts 100% SACH aur jaane-maane hon (science/encyclopedia mein verified). Shaq ho toh mat likho.
Koi medical/health salah nahi, koi vivaadit baat nahi. Facts aise hon ki log hairan ho ke comment/share karein.

Sirf ye JSON do:
{{
 "title": "Hindi title + 1-2 emoji, max 70 akshar, end mein #shorts (jaise 'ऑक्टोपस के 3 दिल? 🤯 #shorts')",
 "description": "2 line Hindi + hashtags (#shorts #facts #amazingfacts #hindi #stickman)",
 "hook": "upar ka text, max 18 akshar, jaise 'क्या आप जानते हो?'",
 "scene": "ek: {scenes}",
 "prop": "pehle fact se judi cheez, English noun",
 "prop_edible": false,
 "characters": {{"a": {{"type": "ek: {types}", "outfit_color": "red/blue/green/yellow/orange/pink/purple/sky", "glasses": true/false}}, "b": null}},
 "beats": [
   {{"action": "walk_in"}},
   {{"action": "think"}},
   {{"action": "fact", "text": "fact Hindi mein, saral, max 100 akshar", "icon": "fact se judi cheez English noun (jaise octopus, moon, honey, camel)", "react": "shock/excited/idea"}},
   ... 3 facts total, beech mein ek-do reaction actions (shock, facepalm, dance, cool, love, scared) ...
   {{"action": "laugh"}}
 ],
 "end_text": "max 22 akshar, jaise 'आपको पता था?'",
 "end_sub": "max 14 akshar, jaise 'कमेंट करो!'"
}}
"""


def make_story(key, model, recent_titles=(), seed=None, mode="comedy"):
    rnd = random.Random(seed)
    recent = "\n".join(f"- {t}" for t in list(recent_titles)[-30:]) or "- (koi nahi)"
    if mode == "fact":
        topic = rnd.choice(FACT_TOPICS)
        user = (f"Aaj ka topic: {topic}. 3 hairan karne wale SACCHE facts.\n"
                f"Ye videos pehle ban chuki hain, inke facts REPEAT mat karna:\n{recent}\n"
                f"Random number: {rnd.randint(1, 10 ** 6)}")
        sysmsg = fact_prompt()
    else:
        theme = rnd.choice(THEMES)
        user = (f"Aaj ki real-life situation (jo har Indian ke saath hoti hai): {theme}.\n"
                f"Kahani aisi ho ki dekhne wala bole 'ye toh mere saath bhi hota hai!'.\n"
                f"Ye title pehle ban chuke hain, inse ALAG kahani do:\n{recent}\n"
                f"Random number: {rnd.randint(1, 10 ** 6)}")
        sysmsg = system_prompt()
    story = chat([{"role": "system", "content": sysmsg}, {"role": "user", "content": user}], key, model,
                 temperature=0.7 if mode == "fact" else 1.0)
    if not isinstance(story, dict) or not isinstance(story.get("beats"), list) or len(story["beats"]) < 4:
        raise RuntimeError("Grok ne sahi kahani nahi di")
    if not isinstance((story.get("characters") or {}).get("b"), dict):
        story.setdefault("characters", {})["b"] = None
    return story


PROP_PROMPT = """Ek simple cartoon icon banao JSON shapes se, 100x100 canvas (0..100 coords), kaali outline wala flat
cartoon style, saaf pehchaan mein aaye. Sirf JSON:
{"shapes":[{"type":"polygon","points":[[x,y],...],"fill":"#rrggbb"},
           {"type":"circle","cx":50,"cy":50,"r":20,"fill":"#rrggbb"},
           {"type":"ellipse","cx":50,"cy":50,"rx":30,"ry":10,"fill":"#rrggbb"},
           {"type":"rect","x":10,"y":10,"w":30,"h":20,"fill":"#rrggbb"},
           {"type":"line","points":[[x,y],[x,y]],"color":"#222222","width":4}]}
8-30 shapes, peeche wali shapes pehle. Cheez: """


def ensure_prop(name, key, model):
    """Library mein na mile toh Grok se drawing banwao aur props_ai/ mein save. Return: True agar ab mil gaya."""
    if not name or PR.find(name):
        return True
    try:
        spec = chat([{"role": "user", "content": PROP_PROMPT + name}], key, model, temperature=0.4)
        if isinstance(spec, dict) and len(spec.get("shapes", [])) >= 3:
            PR.save_ai_prop(name, spec)
            print(f"🎨 Naya prop AI se bana: {name}")
            return True
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  Prop '{name}' nahi bana: {e}")
    return False


FACTS = [   # backup ke liye — sab jaane-maane, verified facts
    ("ऑक्टोपस के 3 दिल होते हैं और उसका खून नीला होता है!", "octopus"),
    ("केला botanically एक बेरी है, लेकिन स्ट्रॉबेरी बेरी नहीं है!", "banana"),
    ("ऊँट के कूबड़ में पानी नहीं, चर्बी जमा होती है!", "camel"),
    ("शार्क डायनासोर से भी पहले से धरती पर हैं!", "shark"),
    ("गर्मियों में एफिल टावर लगभग 15 सेंटीमीटर ऊँचा हो जाता है!", "thermometer"),
    ("बिल्लियाँ दिन में 12 से 16 घंटे सोती हैं!", "cat"),
    ("हाथी कूद नहीं सकता!", "elephant"),
    ("चाँद पर हवा नहीं है, इसलिए वहाँ पैरों के निशान लाखों साल रह सकते हैं!", "moon"),
    ("1995 में आलू अंतरिक्ष में उगाई जाने वाली पहली सब्ज़ी बना!", "potato"),
    ("डॉल्फ़िन सोते समय अपना आधा दिमाग जगाए रखती हैं!", "dolphin"),
    ("गोल्डफ़िश की याददाश्त 3 सेकंड नहीं, कई महीनों की होती है!", "tropical fish"),
    ("शतरंज की शुरुआत भारत में 'चतुरंग' नाम के खेल से हुई थी!", "chess pawn"),
    ("शहद कभी खराब नहीं होता — मिस्र की कब्रों में हज़ारों साल पुराना शहद मिला!", "honey pot"),
    ("पेंगुइन अपने साथी को पत्थर का कंकड़ तोहफ़े में देते हैं!", "penguin"),
    ("एक इंसान दिन में लगभग 20,000 बार साँस लेता है!", "lungs"),
    ("शुतुरमुर्ग की आँख उसके दिमाग से बड़ी होती है!", "eye"),
    ("सूरज की रोशनी को धरती तक पहुँचने में लगभग 8 मिनट लगते हैं!", "sun"),
    ("मधुमक्खियाँ नाच कर एक-दूसरे को फूलों का रास्ता बताती हैं!", "honeybee"),
]


def fallback_fact_story(seed=None):
    rnd = random.Random(seed)
    facts = rnd.sample(FACTS, 3)
    beats = [{"action": "walk_in"}, {"action": "think"}]
    for k, (txt, icon) in enumerate(facts):
        beats.append({"action": "fact", "text": txt, "icon": icon, "react": rnd.choice(["shock", "excited", "idea"])})
        if k < 2:
            beats.append({"action": rnd.choice(["facepalm", "dance", "cool", "scared", "shock"])})
    beats.append({"action": "laugh"})
    first = facts[0][0].split("!")[0][:40]
    return {"title": f"{first}? 🤯 | Fun Facts #shorts", "hook": "क्या आप जानते हो?", "prop": facts[0][1],
            "prop_edible": False, "scene": rnd.choice(["room", "classroom", "park", "office"]),
            "characters": {"a": {"type": rnd.choice(["teacher", "boy", "girl", "man", "woman"])}, "b": None},
            "beats": beats, "end_text": "आपको पता था?", "end_sub": "कमेंट करो!",
            "description": "ऐसे ही मज़ेदार facts के लिए follow करो 🤯\n#shorts #facts #amazingfacts #hindi #stickman"}


def fallback_story(seed=None, mode="comedy"):
    if mode == "fact":
        return fallback_fact_story(seed)
    """Grok fail ho toh bhi video bane: random combo."""
    rnd = random.Random(seed)
    food = rnd.choice(["samosa", "laddoo", "pizza", "jalebi", "burger", "ice cream", "donut", "mango"])
    templates = [
        ["walk_in", "notice", "sneaky_look", "tiptoe", "touch_hot", "blow", "grab", "slip_juggle", "sad",
         "pick_up", "eat", "spicy_fire", "laugh"],
        ["walk_in", "yawn_sleep", "wake_up", "notice", "grab", "dance", "drop", "angry", "pick_up", "eat", "laugh"],
        ["walk_in", "exercise", "notice", "think", "idea", "grab", "eat", "facepalm", "cheer", "laugh"],
    ]
    beats = [{"action": a} for a in rnd.choice(templates)]
    typ = rnd.choice(["boy", "girl", "man", "woman", "student"])
    hooks = {"samosa": "आखिरी समोसा", "laddoo": "मीठा लड्डू", "pizza": "आखिरी पिज़्ज़ा", "jalebi": "गरम जलेबी",
             "burger": "बर्गर की भूख", "ice cream": "आइसक्रीम चोर", "donut": "डोनट का लालच", "mango": "आम का मौसम"}
    return {"title": f"{hooks[food]} 😂 | Stickman Comedy #shorts", "hook": hooks[food], "prop": food,
            "prop_edible": True, "prop_hot": food in ("samosa", "jalebi", "pizza"),
            "scene": rnd.choice(["room", "kitchen", "park", "office"]),
            "characters": {"a": {"type": typ}, "b": None}, "beats": beats,
            "end_text": "तुम भी ऐसा करते हो?", "end_sub": "कमेंट करो!",
            "description": f"{hooks[food]} 😂\n#shorts #stickman #comedy #funny #hindi"}


if __name__ == "__main__":
    key, _ = get_key()
    if not key:
        sys.exit("GROQ_API_KEY ya XAI_API_KEY set karo")
    m = pick_model(key)
    print("model:", m)
    s = make_story(key, m)
    ensure_prop(s.get("prop"), key, m)
    print(json.dumps(s, ensure_ascii=False, indent=1))
