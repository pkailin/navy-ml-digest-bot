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
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import socket
from datetime import datetime, timedelta, timezone

import feedparser
import requests

STATE_FILE = "state.json"
LOOKBACK_HOURS = 120         # 5 days
MAX_SEEN_STORED = 1500       # cap the dedupe list so state.json doesn't grow forever
REQUEST_TIMEOUT = 15
FEED_TIMEOUT = 12            # hard cap per feed fetch so one slow source can't hang the run
MAX_ITEMS_PER_FEED = 10
MAX_WORKERS = 8              # parallel feed fetches
MAX_ITEMS_PER_SECTION = 30   # items are one line each now, so this fits comfortably
TELEGRAM_CHUNK = 3500        # Telegram's hard limit is 4096
LOOKBACK_DAYS_LABEL = "5 days"

# Set True to drop non-USV stories from the naval section entirely instead of
# just sorting them to the bottom.
NAVY_USV_ONLY = False

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def gnews(query: str, when: str = "5d") -> str:
    """Build a Google News RSS search URL for a query. No API key needed.
    `when:5d` tells Google News to only return results from the last 5 days,
    which matches LOOKBACK_HOURS and keeps each feed small."""
    q = urllib.parse.quote(f"{query} when:{when}")
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


# --- Feed sources -----------------------------------------------------------
# Add/remove/edit entries here any time to tune coverage.

NAVY_FEEDS = [
    # Broad naval sources - these get ranked below the USV-specific hits.
    ("Naval News", "https://www.navalnews.com/feed/"),

    ("USNI News", "https://news.usni.org/feed"),

    ("The War Zone", "https://www.twz.com/feed"),

    # USV-focused searches.
    ("Unmanned surface vessels", gnews('"unmanned surface vessel" OR "unmanned surface vehicle"')),

    ("Uncrewed surface vessels", gnews('"uncrewed surface vessel" OR "uncrewed surface vehicle"')),

    ("Drone boats / attack USVs", gnews('"drone boat" OR "sea drone" OR "surface drone" navy')),

    ("USV programs (MUSV, LUSV, Replicator)", gnews('MUSV OR LUSV OR "Ghost Fleet Overlord" OR Replicator unmanned vessel')),

    ("USV builders & contracts", gnews('Saronic OR "Ocean Aero" OR "Textron unmanned" OR "BlackSea Technologies" USV')),
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

    ("Global armed conflict", gnews('"armed conflict" OR offensive OR ceasefire military')),

    ("Middle East", gnews("Israel OR Gaza OR Lebanon OR Iran OR Syria strike military")),

    ("Red Sea / Houthi attacks", gnews("Houthi drone OR missile attack Red Sea shipping")),

    ("Africa (Sudan, Sahel, DRC)", gnews("Sudan OR Sahel OR Congo OR Somalia fighting conflict")),

    ("Asia-Pacific tensions", gnews("Taiwan OR \"South China Sea\" OR Korea military tension incursion")),

    ("Ukraine-Russia war", gnews("Ukraine Russia war strike drone offensive")),

    ("Drone & battlefield autonomy", gnews("drone warfare OR battlefield autonomy OR \"AI weapons\" military")),

    ("Naval drone warfare (any theatre)", gnews('naval drone attack OR "USV strike" OR "drone boat attack"')),
]

# --- USV industry / company watch ---------------------------------------------
# LinkedIn has no native RSS (and blocks datacenter IPs, so scraping it from a
# GitHub runner will not work). These Google News queries catch the press
# coverage of the same announcements. To follow the actual LinkedIn posts, see
# LINKEDIN_FEEDS below.

COMPANY_FEEDS = [
    ("ST Engineering (VENUS USV)", gnews('"ST Engineering" USV OR VENUS OR unmanned vessel')),

    ("Splash Industries (Typhoon, Tempest)", gnews('"Splash Industries" OR "Splash Inc" USV OR "drone boat" OR Typhoon OR Tempest')),

    ("Saronic Technologies", gnews('Saronic USV OR "surface vessel"')),

    ("Anduril (maritime)", gnews('Anduril maritime OR "surface vessel" OR Kraken')),

    ("HavocAI", gnews('HavocAI OR "Havoc AI" unmanned vessel')),

    ("BlackSea Technologies (GARC)", gnews('"BlackSea Technologies" OR GARC unmanned vessel')),

    ("Saildrone", gnews('Saildrone navy OR surveillance OR contract')),

    ("Seasats", gnews('Seasats USV OR "Lightfish"')),

    ("Ocean Aero", gnews('"Ocean Aero" Triton OR autonomous vessel')),

    ("Maritime Robotics", gnews('"Maritime Robotics" USV OR Otter OR Mariner')),

    ("Exail", gnews('Exail USV OR DriX OR "mine countermeasure"')),

    ("Kongsberg / Elbit Seagull", gnews('Kongsberg OR "Elbit Seagull" unmanned surface vessel')),

    ("Textron & L3Harris unmanned maritime", gnews('"Textron Systems" OR L3Harris unmanned surface vessel')),

    ("Ocius / Zycraft (APAC)", gnews('Ocius OR Zycraft OR "Bluebottle" unmanned vessel')),

    ("Kraken Technology Group", gnews('"Kraken Technology Group" OR K-series unmanned vessel')),

    ("Magura", gnews('"Magura" OR unmanned vessel')), 
]

# --- LinkedIn posts (needs a one-time bridge setup) ---------------------------
# LinkedIn shut off RSS in 2013 and there is no official replacement. Use a
# hosted bridge that fetches on its own infrastructure and hands you a normal
# RSS URL, then paste those URLs here - the fetcher below treats them like any
# other feed. Bridges that generate LinkedIn company-page feeds: rss.app,
# feedspot.com, narro.info. Free tiers are usually rate-limited to a handful of
# feeds, so start with the two or three pages you care most about.
#
# Example once you have a URL:
#   ("ST Engineering (LinkedIn)", "https://rss.app/feeds/XXXXXXXX.xml"),

LINKEDIN_FEEDS = [
    # ("ST Engineering (LinkedIn)", "PASTE_BRIDGE_URL_HERE"),
    # ("Splash Industries (LinkedIn)", "PASTE_BRIDGE_URL_HERE"),
    # ("Saronic (LinkedIn)", "PASTE_BRIDGE_URL_HERE"),
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


USV_PATTERN = re.compile(
    r"\b(usv|usvs|musv|lusv|unmanned surface|uncrewed surface|drone boat|drone boats|"
    r"sea drone|surface drone|maritime drone|naval drone|unmanned vessel|uncrewed vessel|"
    r"unmanned boat|robotic boat|magura|sea baby|saronic|ghost fleet)\b",
    re.IGNORECASE,
)


def is_usv(title: str) -> bool:
    return bool(USV_PATTERN.search(title))


def rank_usv_first(items: list) -> list:
    """USV stories to the top, everything else after, order preserved within each group."""
    hits = [it for it in items if is_usv(it["title"])]
    if NAVY_USV_ONLY:
        return hits
    return hits + [it for it in items if not is_usv(it["title"])]


def normalize_title(title: str) -> str:
    """Google News appends ' - Publisher'; strip it so the same story from two
    different search feeds collapses to one entry."""
    base = title.rsplit(" - ", 1)[0] if " - " in title else title
    return "".join(ch for ch in base.lower() if ch.isalnum() or ch == " ").strip()


def fetch_all(feeds) -> dict:
    """Fetch every feed in parallel. This is I/O-bound, so the run takes about
    as long as the slowest single feed instead of the sum of all of them."""
    parsed = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_feed_with_timeout, url): (label, url) for label, url in feeds}
        for fut in as_completed(futures):
            label, url = futures[fut]
            try:
                parsed[url] = fut.result()
            except Exception as e:
                print(f"WARN: failed to fetch {label} ({url}): {e}", flush=True)
                parsed[url] = None
    return parsed


def fetch_section(feeds, seen_set: set, cutoff: datetime) -> list:
    parsed_by_url = fetch_all(feeds)
    items = []
    seen_titles = set()
    # Walk feeds in declared order so output doesn't shuffle based on which
    # feed happened to respond first.
    for label, url in feeds:
        parsed = parsed_by_url.get(url)
        if parsed is None:
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
            key = normalize_title(title)
            if key in seen_titles:
                continue
            seen_titles.add(key)
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
        lines.append(f"• [{escape_md(it['title'])}]({it['link']})")
    return "\n".join(lines)


def chunk_message(text: str, limit: int = TELEGRAM_CHUNK) -> list:
    """Split a long message on line boundaries, always making forward progress."""
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit]
        split_at = window.rfind("\n\n")
        if split_at <= 0:
            split_at = window.rfind("\n")
        if split_at <= 0:
            split_at = limit          # no newline at all: hard cut
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return [c for c in chunks if c.strip()]


def send_telegram(text: str) -> bool:
    """Send the digest, splitting it across messages. Returns True if all parts sent."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    chunks = chunk_message(text)
    print(f"Sending {len(chunks)} message(s), {len(text)} chars total", flush=True)

    all_ok = True
    for i, chunk in enumerate(chunks, 1):
        try:
            resp = requests.post(
                url,
                data={
                    "chat_id": CHAT_ID,
                    "text": chunk,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=REQUEST_TIMEOUT,
            )
        except Exception as e:
            print(f"Telegram request failed on chunk {i}: {e}", flush=True)
            all_ok = False
            continue
        if not resp.ok:
            print(f"Telegram error on chunk {i}:", resp.status_code, resp.text, flush=True)
            all_ok = False
        time.sleep(1)
    return all_ok


def main() -> None:
    state = load_state()
    seen_list = state.get("seen", [])
    seen_set = set(seen_list)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    navy_items = rank_usv_first(fetch_section(NAVY_FEEDS, seen_set, cutoff))[:MAX_ITEMS_PER_SECTION]
    war_items = fetch_section(WAR_FEEDS, seen_set, cutoff)[:MAX_ITEMS_PER_SECTION]
    ml_items = fetch_section(ML_FEEDS, seen_set, cutoff)[:MAX_ITEMS_PER_SECTION]
    company_items = fetch_section(COMPANY_FEEDS + LINKEDIN_FEEDS, seen_set, cutoff)[:MAX_ITEMS_PER_SECTION]
    print(f"Found: navy={len(navy_items)} war={len(war_items)} ml={len(ml_items)} "
          f"companies={len(company_items)}", flush=True)

    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    parts = [f"\U0001F5DE\uFE0F *Daily Digest \u2014 {today}*"]

    sections = [
        format_section("\U0001F6A2 Navy / Naval News", navy_items),
        format_section("\u2694\uFE0F War & Conflict (global)", war_items),
        format_section("\U0001F916 ML \u2022 Autonomous Vehicles \u2022 NVIDIA", ml_items),
        format_section("\U0001F3ED USV Industry (ST Engineering, Splash, Saronic\u2026)", company_items),
    ]
    parts.extend(s for s in sections if s)

    if len(parts) == 1:
        parts.append(f"_No new items in the last {LOOKBACK_DAYS_LABEL}._")

    if not send_telegram("\n\n".join(parts)):
        print("Some messages failed to send; not recording items as seen.", flush=True)
        raise SystemExit(1)

    # Only mark items seen once they've actually been delivered.
    for it in navy_items + war_items + ml_items + company_items:
        seen_list.append(it["link"])
    state["seen"] = seen_list
    save_state(state)


if __name__ == "__main__":
    main()
