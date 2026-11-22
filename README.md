# Drive → YouTube Shorts Auto Uploader

Google Drive folder se videos aur metadata CSV padh ke, GitHub Actions ke zariye **ek ke baad ek, loop mein** (default: har ~6 ghante 1 video, jab tak saare upload na ho jayein) YouTube pe upload karta hai.

## Drive folder ka format
- Videos kisi bhi subfolder mein hon (jaise `VID0001_edited.mp4`)
- `all_shorts_data.csv` jisme `Unique ID`, `File Name`, `Title`, `Description` columns hon
  (video `File Name` se match hota hai; woh na mile toh file naam ID se shuru hona chahiye)
- Kya upload ho chuka hai, ye **YouTube channel se hi** pata chalta hai: har video pe uski ID (`VID0001`) tag lagti hai, aur same title wala video channel pe ho toh bhi skip hota hai. Koi alag log file nahi.

## Setup (ek baar)

### 1. Google Cloud
- Project mein **YouTube Data API v3** aur **Google Drive API** enable karo
- OAuth consent screen → **Publishing status: "In production"** karo
  (Testing mode mein token 7 din mein expire ho jata hai aur automation band ho jayega)
- OAuth client type **Desktop app** → `client_secret.json` download karke is folder mein rakho

### 2. Login (apne computer pe)
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python uploader.py auth
```
Browser khulega → woh Google account chuno jisme Drive folder **aur** YouTube channel dono hain. "Google hasn't verified this app" aaye toh *Advanced → Continue*.

**Drive folder aur YouTube channel alag accounts mein hain?** (jaise channel Brand Account pe hai) Toh do login karo:
```bash
.venv/bin/python uploader.py auth youtube   # channel wala account  -> token.json
.venv/bin/python uploader.py auth drive     # Drive folder wala account -> token_drive.json
```

### 3. Local test
```bash
export DRIVE_FOLDER_ID=<drive folder link ka last part>
.venv/bin/python uploader.py status
.venv/bin/python uploader.py upload --dry-run
```
Kuch IDs zabardasti skip karni hon toh GitHub Variable `SKIP_IDS` mein daalo, jaise `VID0001-VID0010,VID0042`.

### 4. GitHub Secrets
```bash
gh secret set GOOGLE_CLIENT_SECRET < client_secret.json
gh secret set GOOGLE_TOKEN < token.json
gh secret set GOOGLE_DRIVE_TOKEN < token_drive.json   # sirf agar alag Drive login kiya
gh secret set DRIVE_FOLDER_ID --body "<folder id>"
```
Optional variables (Settings → Secrets and variables → Actions → Variables):
| Variable | Default | |
|---|---|---|
| `VIDEO_PRIVACY` | `public` | `private` / `unlisted` / `public` |
| `PLAYLIST_TITLE` | `AI Shorts` | khaali = playlist nahi |
| `PER_RUN_COUNT` | `1` | har run mein kitne videos (har 6 ghante ek run) |
| `SKIP_IDS` | | ye IDs kabhi upload nahi hongi |
| `MADE_FOR_KIDS` | `false` | bachchon ke liye bana content hai toh `true` (YouTube/COPPA rule) |

### 5. Chalao (sirf ek baar)
*Actions → YouTube Upload Loop → Run workflow*
- `interval_minutes`: har video ke beech ka gap (default `350` ≈ 6 ghante, max 350; test ke liye `5`)
- `count`: har run mein kitne videos (default `1`)

Har run 1 video upload karta hai, phir intezaar karke khud ko dobara start karta hai. Saare videos upload hone pe loop apne aap ruk jata hai.
- **Loop rokna ho:** Actions mein chal raha run kholo → *Cancel workflow*
- **Loop ruk gaya ho** (jaise token expire hone pe): problem theek karke dobara *Run workflow* dabao, wahin se continue hoga
- Ek saath do baar *Run workflow* mat dabana, warna do loop chalenge

## Dhyan rakhne wali baatein
- **Private lock:** Jab tak Google aapke API project ka audit nahi karta, API se upload hue videos YouTube **private** pe lock kar deta hai, `VIDEO_PRIVACY=public` set karne pe bhi. Inhe Studio se haath se public karo, ya [audit form](https://support.google.com/youtube/contact/yt_api_form) bharo.
- **Quota:** Roz 10,000 units milte hain, aur ek upload ~1,600 ka padta hai. Isliye ek din mein max ~6 uploads ho sakte hain.
- Sirf apne ya permission wale videos upload karo.
- `client_secret.json`, `token.json` aur CSV `.gitignore` mein hain. Inhe kabhi commit mat karna.
