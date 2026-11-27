"""Ek nayi stickman video: Grok kahani -> render -> Drive pe upload + CSV mein nayi row (ST0001, ST0002...).
Uploader (../uploader.py) phir use YouTube pe daal deta hai.

    python stickman/auto.py            # repo root se
    python stickman/auto.py --local    # sirf banao, Drive pe mat bhejo
Env: GROQ_API_KEY ya XAI_API_KEY, AI_MODEL (opt), DRIVE_FOLDER_ID + Google tokens (uploader jaise)
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import engine  # noqa: E402
import props as PR  # noqa: E402
import story_ai  # noqa: E402

PREFIX = os.environ.get("STICKMAN_PREFIX", "ST")
CREDIT = "Icons: OpenMoji (openmoji.org, CC BY-SA 4.0)"


def next_id(meta):
    nums = [int(m.group(1)) for i in meta if (m := re.fullmatch(rf"{PREFIX}(\d+)", i))]
    return f"{PREFIX}{(max(nums) + 1 if nums else 1):04d}"


def title_key(t):
    return " ".join(str(t).lower().split())


def pick_mode(seed):
    """STORY_MIX = fact wali video ka hissa (0..1), default 0.5"""
    try:
        mix = float(os.environ.get("STORY_MIX", "0.5"))
    except ValueError:
        mix = 0.5
    import random
    return "fact" if random.Random(seed).random() < mix else "comedy"


def get_story(recent_titles, used_keys, seed):
    mode = pick_mode(seed)
    print(f"🎭 Mode: {'fun fact' if mode == 'fact' else 'real-life comedy'}")
    key, base = story_ai.get_key()
    if key:
        try:
            model = story_ai.pick_model(key)
            print(f"🤖 AI ({'Groq' if 'groq' in base else 'xAI Grok'} / {model}) se kahani...")
            for attempt in range(3):
                s = story_ai.make_story(key, model, recent_titles, seed=seed + attempt, mode=mode)
                if title_key(s.get("title", "")) not in used_keys:
                    icons = [b.get("icon") for b in s.get("beats", []) if isinstance(b, dict)]
                    for name in [s.get("prop"), s.get("throw_item")] + icons:
                        story_ai.ensure_prop(name, key, model)
                    return s, "grok"
                print("   title repeat hua, dobara...")
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  AI fail: {e} — backup kahani use kar rahe hain")
    else:
        print("⚠️  GROQ_API_KEY / XAI_API_KEY nahi hai — backup kahani")
    return story_ai.fallback_story(seed, mode), "fallback"


def unique_title(title, used_keys, vid_id):
    t = " ".join(str(title or "Stickman Comedy #shorts").split())[:90]
    if title_key(t) in used_keys:
        t = f"{t[:80]} | {vid_id}"
    return t


def upload_to_drive(lib, mp4, vid_id, title, desc, duration):
    from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
    api = lib.drive.api
    folder = os.environ["DRIVE_FOLDER_ID"].strip()
    fname = f"{vid_id}_stickman.mp4"
    print(f"☁️  Drive pe upload: {fname}")
    api.files().create(body={"name": fname, "parents": [folder]},
                       media_body=MediaFileUpload(str(mp4), mimetype="video/mp4", resumable=True),
                       fields="id", supportsAllDrives=True).execute()
    # CSV mein row (video pehle, row baad mein — taaki uploader ko adhoori row na mile)
    mf = lib.meta_file
    if mf["mimeType"] == "application/vnd.google-apps.spreadsheet":
        raise RuntimeError("Metadata Google Sheet hai, CSV nahi — row add nahi kar sakte")
    raw = lib.drive.read_bytes(mf["id"]).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(raw)))
    header = rows[0]
    vals = {"Unique ID": vid_id, "File Name": fname, "Title": title, "Description": desc,
            "Duration (seconds)": f"{duration:.0f}"}
    row = [vals.get(h.strip(), "") for h in header]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    body = raw if raw.endswith("\n") else raw + "\n"
    w.writerow(row)
    new = ("﻿" if lib.csv_bom else "") + body + buf.getvalue()
    api.files().update(fileId=mf["id"], media_body=MediaIoBaseUpload(io.BytesIO(new.encode("utf-8")),
                                                                      mimetype="text/csv"),
                       supportsAllDrives=True).execute()
    print(f"📝 CSV mein row judi: {vid_id} — {title}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="Drive pe mat bhejo, sirf stickman/out/ mein banao")
    ap.add_argument("--story", help="Grok ki jagah ye story JSON use karo")
    args = ap.parse_args()
    PR.ensure_assets()
    seed = int(time.time())

    lib, meta, used = None, {}, set()
    if not args.local:
        import uploader as U
        lib = U.Library(U.Drive(U.credentials("drive")))
        meta = lib.meta
        used = {title_key(lib.title_of(i)) for i in meta}
    recent = [r.get("Title", "") for i, r in meta.items() if i.startswith(PREFIX)]
    vid_id = next_id(meta)

    if args.story:
        story, src = json.loads(Path(args.story).read_text()), "file"
    else:
        story, src = get_story(recent, used, seed)
    title = unique_title(story.get("title"), used, vid_id)
    desc = (str(story.get("description") or title).strip() + "\n\n" + CREDIT)[:4800]
    print(f"🎬 {vid_id} ({src}): {title}")

    out_dir = engine.OUT if args.local else Path(tempfile.mkdtemp())
    mp4 = out_dir / f"{vid_id}_stickman.mp4"
    (out_dir / f"{vid_id}_story.json").write_text(json.dumps(story, ensure_ascii=False, indent=1))
    duration = engine.render(story, mp4)
    if args.local:
        print("✅ Local video:", mp4)
        return
    upload_to_drive(lib, mp4, vid_id, title, desc, duration)
    print("✅ Ho gaya")


if __name__ == "__main__":
    main()
