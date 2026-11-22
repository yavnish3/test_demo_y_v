"""
Google Drive folder -> YouTube Shorts auto uploader.

Drive folder mein:
  - videos (kisi bhi subfolder mein)
  - ek CSV (default: all_shorts_data.csv) jisme Unique ID, File Name, Title, Description

Commands:
  python uploader.py auth                 # ek baar, apne computer pe: Google login -> token.json
  python uploader.py status               # kitne upload hue / baaki hain
  python uploader.py upload --count 3     # agle 3 pending videos upload
  python uploader.py upload --dry-run     # sirf dikhao kya upload hoga
  python uploader.py mark-done VID0001-VID0066   # in IDs ko "pehle se uploaded" maan lo (dobara upload nahi honge)

Settings environment variables se aati hain (GitHub Secrets / Variables), ya local files se.
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
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload

ROOT = Path(__file__).resolve().parent

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",          # playlist ke liye
    "https://www.googleapis.com/auth/drive.readonly",   # Drive se video + CSV padhna
    "https://www.googleapis.com/auth/drive.file",       # Drive mein apni log file likhna
]

# ─────────────── settings ───────────────
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "").strip()
VIDEO_PRIVACY = os.environ.get("VIDEO_PRIVACY", "private").strip() or "private"   # private / unlisted / public
PLAYLIST_TITLE = os.environ.get("PLAYLIST_TITLE", "AI Shorts").strip()               # khaali = playlist nahi
CATEGORY_ID = os.environ.get("CATEGORY_ID", "22").strip() or "22"                     # 22 People & Blogs, 23 Comedy
MADE_FOR_KIDS = os.environ.get("MADE_FOR_KIDS", "false").strip().lower() == "true"
ID_COLUMN = os.environ.get("ID_COLUMN", "Unique ID")
FILE_COLUMN = os.environ.get("FILE_COLUMN", "File Name")
TITLE_COLUMN = os.environ.get("TITLE_COLUMN", "Title")
DESC_COLUMN = os.environ.get("DESC_COLUMN", "Description")
CSV_NAME = os.environ.get("CSV_NAME", "").strip()      # khaali = all_shorts_data.csv ya akeli CSV
DEFAULT_CSV = "all_shorts_data.csv"
LOG_NAME = "youtube_upload_log.csv"                     # Drive folder mein ye script khud banati hai
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
LOG_FIELDS = ["id", "status", "video_id", "file", "title", "uploaded_at", "playlist"]

FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"


def die(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def norm_id(value):
    """'1', '0001', '1.0' -> '0001';  'vid0001 ' -> 'VID0001'"""
    s = str(value).strip()
    if re.fullmatch(r"\d+(\.0+)?", s):
        return str(int(float(s))).zfill(4)
    return s.upper()


def natural_key(x):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", x)]


def expand_ids(spec):
    """'VID0001-VID0066,VID0070' ya '1-66' -> list of IDs"""
    out = []
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(r"([A-Za-z_]*)(\d+)\s*-\s*([A-Za-z_]*)(\d+)", part)
        if m:
            prefix, a, _, b = m.groups()
            width = len(a) if prefix else 4
            out += [norm_id(f"{prefix}{str(i).zfill(width)}") for i in range(int(a), int(b) + 1)]
        elif part:
            out.append(norm_id(part))
    return out


# ─────────────── auth ───────────────
def client_config():
    env = os.environ.get("GOOGLE_CLIENT_SECRET")
    if env:
        return json.loads(env)
    path = ROOT / "client_secret.json"
    if not path.exists():
        die("client_secret.json nahi mila (ya GOOGLE_CLIENT_SECRET env set karo)")
    return json.loads(path.read_text())


def credentials():
    env = os.environ.get("GOOGLE_TOKEN")
    if env:
        creds = Credentials.from_authorized_user_info(json.loads(env), SCOPES)
    elif (ROOT / "token.json").exists():
        creds = Credentials.from_authorized_user_file(str(ROOT / "token.json"), SCOPES)
    else:
        die("Token nahi mila. Pehle apne computer pe chalao: python uploader.py auth")
    if not creds.valid:
        if creds.refresh_token:
            creds.refresh(Request())
        else:
            die("Token expire ho gaya. Dobara chalao: python uploader.py auth")
    return creds


def cmd_auth(_args):
    flow = InstalledAppFlow.from_client_config(client_config(), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    out = ROOT / "token.json"
    out.write_text(creds.to_json())
    os.chmod(out, 0o600)
    print("✅ Login ho gaya, token.json ban gaya (ise kabhi git mein commit mat karna).")
    print("   GitHub secret mein daalne ke liye:  gh secret set GOOGLE_TOKEN < token.json")


# ─────────────── drive ───────────────
class Drive:
    def __init__(self, creds):
        self.api = build("drive", "v3", credentials=creds, cache_discovery=False)

    def children(self, folder_id):
        items, token = [], None
        while True:
            res = self.api.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, size)",
                pageSize=1000, pageToken=token,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            items += res.get("files", [])
            token = res.get("nextPageToken")
            if not token:
                return items

    def walk(self, folder_id):
        for f in self.children(folder_id):
            if f["mimeType"] == FOLDER_MIME:
                yield from self.walk(f["id"])
            else:
                yield f

    def read_bytes(self, file_id, export_mime=None):
        if export_mime:
            req = self.api.files().export_media(fileId=file_id, mimeType=export_mime)
        else:
            req = self.api.files().get_media(fileId=file_id, supportsAllDrives=True)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, req, chunksize=32 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
        return buf.getvalue()

    def download(self, file_id, dest):
        req = self.api.files().get_media(fileId=file_id, supportsAllDrives=True)
        with open(dest, "wb") as fh:
            dl = MediaIoBaseDownload(fh, req, chunksize=32 * 1024 * 1024)
            done = False
            while not done:
                _, done = dl.next_chunk()

    def write_text(self, name, parent, text, file_id=None):
        media = MediaIoBaseUpload(io.BytesIO(text.encode("utf-8")), mimetype="text/csv", resumable=False)
        if file_id:
            self.api.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
            return file_id
        meta = {"name": name, "parents": [parent], "mimeType": "text/csv"}
        return self.api.files().create(body=meta, media_body=media, fields="id",
                                       supportsAllDrives=True).execute()["id"]


# ─────────────── data: CSV + videos + log ───────────────
class Library:
    def __init__(self, drive):
        if not DRIVE_FOLDER_ID:
            die("DRIVE_FOLDER_ID set nahi hai")
        self.drive = drive
        files = list(drive.walk(DRIVE_FOLDER_ID))
        root_files = drive.children(DRIVE_FOLDER_ID)

        # log file (sirf root folder mein)
        log = next((f for f in root_files if f["name"] == LOG_NAME), None)
        self.log_file_id = log["id"] if log else None
        self.log = {}
        if log:
            text = drive.read_bytes(log["id"]).decode("utf-8-sig")
            for row in csv.DictReader(io.StringIO(text)):
                self.log[norm_id(row["id"])] = row

        # metadata CSV
        csvs = [f for f in files if f["name"] != LOG_NAME and
                (f["name"].lower().endswith(".csv") or f["mimeType"] in ("text/csv", SHEET_MIME))]
        if CSV_NAME:
            csvs = [f for f in csvs if f["name"] == CSV_NAME]
        elif len(csvs) > 1 and any(f["name"] == DEFAULT_CSV for f in csvs):
            csvs = [f for f in csvs if f["name"] == DEFAULT_CSV]
        if not csvs:
            die("Drive folder mein metadata CSV nahi mili")
        if len(csvs) > 1:
            die(f"Ek se zyada CSV mili: {[f['name'] for f in csvs]} — CSV_NAME set karo")
        meta_file = csvs[0]
        raw = drive.read_bytes(meta_file["id"], "text/csv" if meta_file["mimeType"] == SHEET_MIME else None)
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        if rows and ID_COLUMN not in rows[0]:
            die(f"CSV mein '{ID_COLUMN}' column nahi hai. Columns: {list(rows[0].keys())}")
        self.meta = {}
        for r in rows:
            if str(r.get(ID_COLUMN, "")).strip():
                self.meta.setdefault(norm_id(r[ID_COLUMN]), r)

        # videos: pehle CSV ke "File Name" se exact match, warna naam ID se shuru ho
        vids = [f for f in files
                if Path(f["name"]).suffix.lower() in VIDEO_EXT or f["mimeType"].startswith("video/")]
        by_name = {f["name"].strip().lower(): f for f in vids}
        self.videos = {}
        for vid_id, r in self.meta.items():
            fname = str(r.get(FILE_COLUMN) or "").strip().lower()
            f = by_name.get(fname) if fname else None
            if not f:
                f = next((v for v in vids if self._id_of(v["name"]) == vid_id), None)
            if f:
                self.videos[vid_id] = f
        matched = {f["id"] for f in self.videos.values()}
        self.unmatched_files = sorted(f["name"] for f in vids if f["id"] not in matched)

    @staticmethod
    def _id_of(name):
        m = re.match(r"^([A-Za-z_]*\d+)", name)
        return norm_id(m.group(1)) if m else None

    def pending(self):
        ids = sorted(set(self.meta) & set(self.videos), key=natural_key)
        return [i for i in ids if self.log.get(i, {}).get("status") not in ("UPLOADED", "SKIPPED")]

    def save_log(self):
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=LOG_FIELDS, extrasaction="ignore")
        w.writeheader()
        for k in sorted(self.log, key=natural_key):
            w.writerow(self.log[k])
        self.log_file_id = self.drive.write_text(LOG_NAME, DRIVE_FOLDER_ID, out.getvalue(), self.log_file_id)


# ─────────────── youtube ───────────────
def clean_title(t):
    t = re.sub(r"[<>]", "", " ".join(str(t).split()))
    return t[:100].rstrip() or "Shorts"


def clean_desc(d, title):
    d = "" if d is None else str(d).strip()
    if d.lower() == "nan":
        d = ""
    d = re.sub(r"[<>]", "", d)
    if "#shorts" not in (d + " " + title).lower():
        d = (d + "\n\n#Shorts").strip()
    return d[:5000]


class YouTube:
    def __init__(self, creds):
        self.api = build("youtube", "v3", credentials=creds, cache_discovery=False)
        self.playlist_id = None

    def playlist(self):
        if not PLAYLIST_TITLE:
            return None
        if self.playlist_id:
            return self.playlist_id
        token = None
        while True:
            res = self.api.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=token).execute()
            for it in res.get("items", []):
                if it["snippet"]["title"].strip().lower() == PLAYLIST_TITLE.lower():
                    self.playlist_id = it["id"]
                    return self.playlist_id
            token = res.get("nextPageToken")
            if not token:
                break
        res = self.api.playlists().insert(part="snippet,status", body={
            "snippet": {"title": PLAYLIST_TITLE},
            "status": {"privacyStatus": "public"},
        }).execute()
        self.playlist_id = res["id"]
        return self.playlist_id

    def upload(self, path, title, desc):
        body = {
            "snippet": {"title": title, "description": desc, "categoryId": CATEGORY_ID},
            "status": {"privacyStatus": VIDEO_PRIVACY, "selfDeclaredMadeForKids": MADE_FOR_KIDS},
        }
        media = MediaFileUpload(str(path), mimetype="video/*", chunksize=8 * 1024 * 1024, resumable=True)
        req = self.api.videos().insert(part="snippet,status", body=body, media_body=media)
        resp, retries = None, 0
        while resp is None:
            try:
                status, resp = req.next_chunk()
                if status:
                    print(f"   ⬆️  {int(status.progress() * 100)}%", flush=True)
            except HttpError as e:
                if e.resp.status in (500, 502, 503, 504) and retries < 5:
                    retries += 1
                    time.sleep(2 ** retries)
                    continue
                raise
        return resp["id"]

    def add_to_playlist(self, video_id):
        pid = self.playlist()
        if not pid:
            return ""
        self.api.playlistItems().insert(part="snippet", body={
            "snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": video_id}}
        }).execute()
        return "ADDED"


def is_quota_error(e):
    return isinstance(e, HttpError) and e.resp.status == 403 and (
        b"quotaExceeded" in e.content or b"uploadLimitExceeded" in e.content)


# ─────────────── commands ───────────────
def cmd_status(_args):
    lib = Library(Drive(credentials()))
    done = sum(1 for r in lib.log.values() if r.get("status") in ("UPLOADED", "SKIPPED"))
    no_video = sorted(set(lib.meta) - set(lib.videos))
    no_meta = lib.unmatched_files
    print(f"📄 CSV rows: {len(lib.meta)}   🎬 Videos: {len(lib.videos)}")
    print(f"✅ Uploaded/skipped: {done}   ⏳ Pending: {len(lib.pending())}")
    if no_video:
        print(f"⚠️  CSV mein hai, video nahi: {', '.join(no_video[:20])}{' ...' if len(no_video) > 20 else ''}")
    if no_meta:
        print(f"⚠️  Drive mein video hai, CSV row nahi: {', '.join(no_meta[:20])}{' ...' if len(no_meta) > 20 else ''}")


def cmd_mark_done(args):
    lib = Library(Drive(credentials()))
    ids = expand_ids(args.ids)
    for i in ids:
        if lib.log.get(i, {}).get("status") != "UPLOADED":
            lib.log[i] = {"id": i, "status": "SKIPPED", "uploaded_at": datetime.now(timezone.utc).isoformat()}
    lib.save_log()
    print(f"✅ {len(ids)} IDs ko done mark kiya")


def cmd_upload(args):
    creds = credentials()
    lib = Library(Drive(creds))
    todo = lib.pending()[: args.count]
    print(f"⏳ Pending: {len(lib.pending())}  →  aaj upload: {len(todo)}  (privacy: {VIDEO_PRIVACY}, kids: {MADE_FOR_KIDS})")
    if not todo:
        print("🎉 Kuch baaki nahi hai")
        return
    if args.dry_run:
        for i in todo:
            print(f"   {i}  {lib.videos[i]['name']}  →  {clean_title(lib.meta[i].get(TITLE_COLUMN, ''))}")
        return

    yt = YouTube(creds)
    failed = 0
    for i in todo:
        row, vfile = lib.meta[i], lib.videos[i]
        title = clean_title(row.get(TITLE_COLUMN, ""))
        desc = clean_desc(row.get(DESC_COLUMN), title)
        print(f"\n🎬 {i}  ({vfile['name']})")
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / f"video{Path(vfile['name']).suffix or '.mp4'}"
            try:
                print("   ⬇️  Drive se download...")
                lib.drive.download(vfile["id"], local)
                video_id = yt.upload(local, title, desc)
            except Exception as e:  # noqa: BLE001
                if is_quota_error(e):
                    print("⛔ YouTube quota/limit khatam — kal phir try hoga")
                    break
                failed += 1
                print(f"   ❌ Fail: {e}")
                lib.log[i] = {"id": i, "status": "FAILED", "file": vfile["name"], "title": title,
                              "uploaded_at": datetime.now(timezone.utc).isoformat()}
                lib.save_log()
                continue
        try:
            pl = yt.add_to_playlist(video_id)
        except HttpError as e:
            print(f"   ⚠️  Playlist mein add nahi hua: {e}")
            pl = "FAILED"
        lib.log[i] = {"id": i, "status": "UPLOADED", "video_id": video_id, "file": vfile["name"], "title": title,
                      "uploaded_at": datetime.now(timezone.utc).isoformat(), "playlist": pl}
        lib.save_log()   # har upload ke baad save, taaki crash pe dobara upload na ho
        print(f"   ✅ Uploaded: https://youtu.be/{video_id}")
        time.sleep(3)
    if failed:
        sys.exit(f"❌ {failed} video fail hue")


def main():
    ap = argparse.ArgumentParser(description="Drive -> YouTube Shorts uploader")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="Google login (local, ek baar)").set_defaults(fn=cmd_auth)
    sub.add_parser("status", help="kitne baaki hain").set_defaults(fn=cmd_status)
    up = sub.add_parser("upload", help="pending videos upload karo")
    up.add_argument("--count", type=int, default=int(os.environ.get("DAILY_COUNT", "3") or 3))
    up.add_argument("--dry-run", action="store_true")
    up.set_defaults(fn=cmd_upload)
    md = sub.add_parser("mark-done", help="IDs ko pehle se uploaded maano, e.g. VID0001-VID0066 ya VID0003,VID0007")
    md.add_argument("ids")
    md.set_defaults(fn=cmd_mark_done)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
