import os
import sys
import json
import re
import csv
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "state.json"
WEIGHTS_FILE = DATA_DIR / "weights.json"
QUEUE_FILE = DATA_DIR / "queue.json"
HISTORY_FILE = DATA_DIR / "history.json"
IMPORT_DIR = DATA_DIR / "import"

DATA_DIR.mkdir(parents=True, exist_ok=True)
IMPORT_DIR.mkdir(parents=True, exist_ok=True)

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def get_service():
    raw_token = os.environ.get("AUTH_TOKEN") or os.environ.get("YOUTUBE_TOKEN_JSON")
    token_path = BASE_DIR / "token.json"
    alt_token = BASE_DIR.parent / "The AI Method" / "token.json"

    if raw_token:
        info = json.loads(raw_token)
        creds = Credentials.from_authorized_user_info(info)
    elif token_path.exists():
        with open(token_path, "r", encoding="utf-8") as f:
            creds = Credentials.from_authorized_user_info(json.load(f))
    elif alt_token.exists():
        with open(alt_token, "r", encoding="utf-8") as f:
            creds = Credentials.from_authorized_user_info(json.load(f))
    else:
        raise FileNotFoundError("Authentication credentials not found.")

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        if token_path.exists():
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)

def parse_iso8601_duration(duration_str: str) -> int:
    match = re.match(r"PT(?:(\d+)M)?(?:(\d+)S)?", duration_str)
    if not match:
        h = re.search(r"(\d+)H", duration_str)
        m = re.search(r"(\d+)M", duration_str)
        s = re.search(r"(\d+)S", duration_str)
        hours = int(h.group(1)) if h else 0
        mins = int(m.group(1)) if m else 0
        secs = int(s.group(1)) if s else 0
        return hours * 3600 + mins * 60 + secs
    mins = int(match.group(1)) if match.group(1) else 0
    secs = int(match.group(2)) if match.group(2) else 0
    return mins * 60 + secs

def sync_metrics():
    print("[SYNC] Fetching telemetry metrics...")
    service = get_service()
    ch_resp = service.channels().list(part="contentDetails,statistics", mine=True).execute()
    if not ch_resp.get("items"):
        return {}

    ch = ch_resp["items"][0]
    uploads_id = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    stats = ch["statistics"]

    video_ids = []
    page_token = None
    while True:
        p_resp = service.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_id,
            maxResults=50,
            pageToken=page_token
        ).execute()
        for it in p_resp.get("items", []):
            video_ids.append(it["contentDetails"]["videoId"])
        page_token = p_resp.get("nextPageToken")
        if not page_token:
            break

    records = []
    keyword_freq = {}
    ch_info = service.channels().list(part="snippet", mine=True).execute()
    channel_name = ch_info["items"][0]["snippet"]["title"].lower() if ch_info.get("items") else ""

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        v_resp = service.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch)
        ).execute()
        for v in v_resp.get("items", []):
            v_id = v["id"]
            snip = v.get("snippet", {})
            st = v.get("statistics", {})
            cd = v.get("contentDetails", {})

            v_count = int(st.get("viewCount", 0))
            dur_sec = parse_iso8601_duration(cd.get("duration", "PT0S"))
            pub = snip.get("publishedAt", "")
            tags = snip.get("tags", [])

            for t in tags:
                word = t.lower().strip()
                if len(word) > 2 and word != channel_name and "ai method" not in word:
                    keyword_freq[word] = keyword_freq.get(word, 0) + v_count

            records.append({
                "id": v_id,
                "m1": v_count,
                "m2": int(st.get("likeCount", 0)),
                "m3": int(st.get("commentCount", 0)),
                "dur": dur_sec,
                "ts": pub
            })

    # Ingest any local imported csv
    for csv_file in IMPORT_DIR.glob("*.csv"):
        try:
            with open(csv_file, "r", encoding="utf-8-sig") as cf:
                reader = csv.DictReader(cf)
                for row in reader:
                    cid = row.get("Content") or row.get("Video")
                    for r in records:
                        if r["id"] == cid:
                            if "Engaged views" in row:
                                r["eng"] = float(row["Engaged views"].replace(",", ""))
                            if "Average percentage viewed (%)" in row:
                                r["ret"] = float(row["Average percentage viewed (%)"].replace("%", ""))
        except Exception:
            pass

    state_data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_items": len(records),
        "totals": {
            "m1": int(stats.get("viewCount", 0)),
            "m2": int(stats.get("subscriberCount", 0)),
            "m3": int(stats.get("videoCount", 0))
        },
        "top_keywords": sorted(keyword_freq.keys(), key=lambda k: keyword_freq[k], reverse=True)[:15],
        "records": records
    }

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state_data, f, indent=2)

    print(f"[SYNC] Completed. Synced {len(records)} records (aggregate: {state_data['totals']['m1']}).")
    return state_data

def update_weights(state: dict):
    records = state.get("records", [])
    if not records:
        return {}

    type_a = [r for r in records if r["dur"] <= 60]
    type_b = [r for r in records if r["dur"] > 60]

    avg_a = (sum(r["m1"] for r in type_a) / len(type_a)) if type_a else 0
    avg_b = (sum(r["m1"] for r in type_b) / len(type_b)) if type_b else 0

    ratio = round(avg_a / max(1.0, avg_b), 2)

    now = datetime.now(timezone.utc)
    flagged = []
    for r in records:
        try:
            pub_dt = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            age_h = (now - pub_dt).total_seconds() / 3600.0
            if age_h > 18.0 and r["m1"] <= 5:
                flagged.append(r["id"])
        except Exception:
            pass

    weights_data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "ratio": ratio,
        "keywords": state.get("top_keywords", [])[:10],
        "slots": ["11:30:00", "15:30:00", "18:30:00"],
        "flagged": flagged[:10]
    }

    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(weights_data, f, indent=2)

    print(f"[WEIGHTS] Ratio: {ratio}x | Priority tokens: {len(weights_data['keywords'])} | Flagged: {len(flagged)}")
    return weights_data

def process_queue(weights: dict):
    if not QUEUE_FILE.exists():
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump({"tasks": []}, f, indent=2)
        print("[QUEUE] Standby (empty).")
        return

    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        queue_data = json.load(f)

    tasks = queue_data.get("tasks", [])
    if not tasks:
        print("[QUEUE] Standby (0 pending tasks).")
        return

    task = tasks[0]
    payload_path = Path(task.get("file", ""))
    if not payload_path.exists():
        print(f"[QUEUE] Payload not found: {payload_path}")
        return

    service = get_service()
    meta = task.get("meta", {})
    title = meta.get("title", payload_path.stem)
    description = meta.get("description", "")
    tags = list(meta.get("tags", []))

    # Enrich tags from calculated weights
    for kw in weights.get("keywords", []):
        if kw.lower() not in [t.lower() for t in tags]:
            tags.append(kw)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:50],
            "categoryId": meta.get("category", "28")
        },
        "status": {
            "selfDeclaredMadeForKids": False
        }
    }

    schedule_slot = task.get("slot")
    if schedule_slot:
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = schedule_slot
    else:
        body["status"]["privacyStatus"] = task.get("privacy", "public")

    print(f"[DISPATCH] Processing task: {title[:40]}...")
    media = MediaFileUpload(str(payload_path), chunksize=10*1024*1024, resumable=True)
    req = service.videos().insert(part="snippet,status", body=body, media_body=media)

    resp = None
    while resp is None:
        st, resp = req.next_chunk()
        if st:
            print(f"  Progress: {int(st.progress() * 100)}%")

    item_id = resp.get("id")
    print(f"[DISPATCH] Item deployed: {item_id}")

    thumb = task.get("thumb")
    if thumb and Path(thumb).exists():
        try:
            m_thumb = MediaFileUpload(str(thumb), mimetype="image/png", resumable=True)
            service.thumbnails().set(videoId=item_id, media_body=m_thumb).execute()
            print("  Custom asset attached.")
        except Exception:
            pass

    history = []
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as hf:
                history = json.load(hf)
        except Exception:
            history = []

    history.append({
        "id": item_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "title": title
    })
    with open(HISTORY_FILE, "w", encoding="utf-8") as hf:
        json.dump(history, hf, indent=2)

    queue_data["tasks"] = tasks[1:]
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue_data, f, indent=2)

    print(f"[DISPATCH] Queue updated ({len(queue_data['tasks'])} remaining).")
