"""Aaj ke trending topics (India): viral YouTube Shorts + Google Trends RSS + YouTube trending (mostPopular).
Instagram ka official API nahi hai, isliye ye do public/official source.

    python trends.py
"""
import sys
import urllib.request
import xml.etree.ElementTree as ET

GT_RSS = "https://trends.google.com/trending/rss?geo=IN"
NS = {"ht": "https://trends.google.com/trending/rss"}


def google_trends(limit=20):
    req = urllib.request.Request(GT_RSS, headers={"User-Agent": "Mozilla/5.0 (stickman-shorts)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        root = ET.fromstring(r.read())
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        traffic = (it.findtext("ht:approx_traffic", namespaces=NS) or "").strip()
        news = [n.findtext("ht:news_item_title", namespaces=NS) or "" for n in it.findall("ht:news_item", NS)][:1]
        if title:
            out.append(f"{title} ({traffic}) — {news[0][:110] if news else ''}".strip(" —"))
    return out[:limit]


def youtube_trending(limit=20):
    """YouTube India trending titles (1 quota unit). Channel 1 ka token use hota hai."""
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    import uploader as U
    from googleapiclient.discovery import build
    api = build("youtube", "v3", credentials=U.credentials("youtube"), cache_discovery=False)
    res = api.videos().list(part="snippet", chart="mostPopular", regionCode="IN", maxResults=limit).execute()
    return [v["snippet"]["title"][:110] for v in res.get("items", [])]


def viral_shorts(limit=25, days=2):
    """Pichhle 2 din ke sabse zyada dekhe gaye Hindi/India Shorts (search = 100 quota units, roz ~1 baar)."""
    import datetime as dt
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    import uploader as U
    from googleapiclient.discovery import build
    api = build("youtube", "v3", credentials=U.credentials("youtube"), cache_discovery=False)
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = api.search().list(part="snippet", type="video", videoDuration="short", order="viewCount", regionCode="IN",
                            relevanceLanguage="hi", publishedAfter=since, maxResults=limit, safeSearch="strict").execute()
    return [it["snippet"]["title"][:110] for it in res.get("items", [])]


def get_trends():
    items = []
    for name, fn in (("Viral Shorts", viral_shorts), ("Google", google_trends), ("YouTube", youtube_trending)):
        try:
            got = fn()
            items += [f"[{name}] {x}" for x in got]
            print(f"📈 {name} trends: {len(got)}")
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  {name} trends nahi mile: {e}")
    return items


if __name__ == "__main__":
    for x in get_trends():
        print(x)
