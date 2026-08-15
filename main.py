#!/usr/bin/env python3
"""
Daily naval news + autonomous-vehicle/ML/NVIDIA news digest, sent to Telegram.
Intended to run once a day via GitHub Actions (see .github/workflows/daily-digest.yml).
No server required - GitHub's cloud runner does the work.
"""

import os
import json
import time
import html
import urllib.parse
import socket
from datetime import datetime, timedelta, timezone

import feedparser
import requests

STATE_FILE = "state.json"
LOOKBACK_HOURS = 30          # only include items published within this window
MAX_SEEN_STORED = 1500       # cap the dedupe list so state.json doesn't grow forever
REQUEST_TIMEOUT = 15
FEED_TIMEOUT = 12            # hard cap per feed fetch so one slow source can't hang the run
MAX_ITEMS_PER_FEED = 15

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def gnews(query: str) -> str:
    """Build a Google News RSS search URL for a query. No API key needed."""
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


# --- Feed sources -----------------------------------------------------------
# Add/remove/edit entries here any time to tune coverage.

NAVY_FEEDS = [
    ("Naval News", "https://www.navalnews.com/feed/"),
    ("USNI News", "https://news.usni.org/feed"),
    ("The War Zone", "https://www.twz.com/feed"),
    ("USV / unmanned surface vessel", gnews('"unmanned surface vessel" OR USV navy')),
    ("Strait of Hormuz", gnews("Strait of Hormuz navy")),
    ("Navy exercises", gnews("navy exercise OR naval drill deployment")),
]

ML_FEEDS = [
    ("NVIDIA Blog", "https://blogs.nvidia.com/feed/"),
    ("Autonomous vehicle ML", gnews('"autonomous vehicle" machine learning')),
    ("NVIDIA autonomy / robotics", gnews("NVIDIA autonomous driving OR robotics")),
    ("Autonomous drones / UAV", gnews("autonomous drone OR UAV machine learning")),
    ("Autonomous underwater/surface vehicles", gnews('"autonomous underwater vehicle" OR "unmanned surface vehicle" machine learning')),
]

WAR_FEEDS = [
    ("ISW - Institute for the Study of War", "https://iswresearch.org/feeds/posts/default?alt=rss"),
    ("Ukraine naval drones (Magura, Sea Baby, etc.)", gnews("Ukraine Magura OR Sea Baby naval drone")),
    ("Russia-Ukraine war UAV/drone tech", gnews("Russia Ukraine war drone strike technology")),
    ("Russia-Ukraine battlefield tech advances", gnews("Ukraine war weapons technology advance")),
    ("Black Sea drone warfare", gnews("Black Sea naval drone Ukraine Russia")),
    ("Houthi / Red Sea drone attacks", gnews("Houthi drone missile attack Red Sea")),
    ("War robotics / battlefield autonomy", gnews("battlefield autonomous drone AI war")),
]


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"seen": []}


def save_state(state: dict) -> None:
    state["seen"] = state["seen"][-MAX_SEEN_STORED:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
    return None


def fetch_feed_with_timeout(url: str):
    """Fetch a feed URL with a hard timeout, then hand the bytes to feedparser.
    (feedparser.parse() alone has no timeout and can hang forever on a slow server.)"""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; navy-ml-digest-bot/1.0)"}
    resp = requests.get(url, headers=headers, timeout=FEED_TIMEOUT)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def fetch_section(feeds, seen_set: set, cutoff: datetime) -> list:
    items = []
    for label, url in feeds:
        try:
            parsed = fetch_feed_with_timeout(url)
        except Exception as e:
            print(f"WARN: failed to fetch {label} ({url}): {e}")
            continue
        for entry in parsed.entries[:MAX_ITEMS_PER_FEED]:
            link = entry.get("link")
            if not link or link in seen_set:
                continue
            pub = entry_time(entry)
            if pub and pub < cutoff:
                continue
            title = html.unescape(entry.get("title", "").strip())
            if not title:
                continue
            items.append({"label": label, "title": title, "link": link})
            seen_set.add(link)
    return items


def escape_md(text: str) -> str:
    # Keep it simple: strip characters that break basic Telegram Markdown links.
    return text.replace("[", "(").replace("]", ")").replace("_", " ").replace("*", "")


def format_section(header: str, items: list) -> str:
    if not items:
        return ""
    lines = [f"*{header}*"]
    for it in items:
        safe_title = escape_md(it["title"])
        safe_label = escape_md(it["label"])
        lines.append(f"• [{safe_title}]({it['link']})\n  _{safe_label}_")
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    # Telegram's hard limit is 4096 chars; chunk safely under that.
    remaining = text
    chunks = []
    while remaining:
        if len(remaining) <= 3500:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n\n", 0, 3500)
        if split_at == -1:
            split_at = 3500
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]

    for chunk in chunks:
        resp = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if not resp.ok:
            print("Telegram error:", resp.status_code, resp.text)
        time.sleep(1)


def main() -> None:
    state = load_state()
    seen_set = set(state["seen"])
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    navy_items = fetch_section(NAVY_FEEDS, seen_set, cutoff)
    war_items = fetch_section(WAR_FEEDS, seen_set, cutoff)
    ml_items = fetch_section(ML_FEEDS, seen_set, cutoff)

    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    parts = [f"\U0001F5DE\uFE0F *Daily Digest \u2014 {today}*"]

    navy_section = format_section("\U0001F6A2 Navy / Naval News", navy_items)
    war_section = format_section("\u2694\uFE0F War & Conflict Tech (Ukraine, Red Sea, etc.)", war_items)
    ml_section = format_section("\U0001F916 ML \u2022 Autonomous Vehicles \u2022 NVIDIA", ml_items)

    if navy_section:
        parts.append(navy_section)
    if war_section:
        parts.append(war_section)
    if ml_section:
        parts.append(ml_section)

    if len(parts) == 1:
        parts.append("_No new items in the last 24h._")

    message = "\n\n".join(parts)
    send_telegram(message)

    state["seen"] = list(seen_set)
    save_state(state)


if __name__ == "__main__":
    main()
