"""
Trump Stock Tracker v2 — Multi-Source Edition
===============================================
Combines three ideas:
  1. Broader source monitoring (like trumptrack.app) — Trump's own channels
     (Truth Social, White House releases) PLUS news coverage, not just news.
  2. Direct-vs-indirect classification — a statement FROM Trump about a
     company is treated differently from a news article that merely
     mentions a ticker. This is the single most useful idea from
     trumptrack.app's feed.
  3. Personal 3-4 day price tracking + email digest — the piece neither
     trumptrack.app nor trump-code really does for you individually.

WHAT THIS DELIBERATELY DOES NOT DO
-----------------------------------
It does NOT brute-force thousands of "rules" to find a historical hit rate
(that's the trump-code approach, and their own README admits the risk of
data-snooping bias from testing 31.5M combinations). This script does not
claim any predictive edge. It just surfaces information faster and more
completely than checking manually, and gives you clean before/after price
context so YOU can judge it.

SOURCES MONITORED
------------------
1. Truth Social — via an unofficial mirror/RSS. These mirrors are run by
   third parties and can go down or change format without notice. Treat
   this source as "best effort," and check the mirror URL still works
   before relying on it.
2. whitehouse.gov press releases — official RSS feed, reliable.
3. News wires — via NewsAPI, same as v1.
4. (NOT INCLUDED: OGE Form 278-T stock trade disclosures) — these are
   filed as PDFs on financialdisclosures.house.gov and there's no clean
   public API for them. Automating this reliably would need a PDF-scraping
   pipeline that's beyond what's reasonable to bolt on here. If you want
   this specific data, trumptrack.app's /trades page already does it —
   worth checking that page directly rather than rebuilding it.

SETUP
-----
pip install requests yfinance feedparser --break-system-packages

Environment variables needed:
  NEWSAPI_KEY, EMAIL_FROM, EMAIL_APP_PASSWORD, EMAIL_TO
  TRUTHSOCIAL_MIRROR_RSS (optional — see note in fetch_truth_social())

WEBSITE
-------
Every run writes mentions.json alongside this script (see export_site_json
below). Put tracker_dashboard.html in the same folder/repo and it will
fetch mentions.json automatically — see DEPLOYMENT.md for wiring this up
via GitHub Pages so the whole pipeline (detect -> track -> email -> site)
runs and publishes itself daily with no manual steps.
"""

import os
import re
import json
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
import yfinance as yf

try:
    import feedparser
except ImportError:
    feedparser = None  # RSS sources will be skipped with a warning

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")

# Unofficial Truth Social mirrors change over time. trumpstruth.org has been
# a commonly used public archive; verify it's still live before relying on
# it, and swap in whatever mirror is currently working.
TRUTHSOCIAL_MIRROR_RSS = os.environ.get(
    "TRUTHSOCIAL_MIRROR_RSS", "https://trumpstruth.org/feed"
)
WHITEHOUSE_RSS = "https://www.whitehouse.gov/feed/"

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
MENTIONS_JSON_FILE = os.path.join(os.path.dirname(__file__), "mentions.json")
TRACK_DAYS = 4
LOOKBACK_HOURS = 30

WATCHLIST = {
    "mp materials":      "MP",
    "usa rare earth":    "USAR",
    "vulcan elements":   None,
    "aclara resources":  "ARA.TO",
    "lithium americas":  "LAC",
    "palantir":          "PLTR",
    "intel":             "INTC",
    "nvidia":            "NVDA",
    "coinbase":          "COIN",
    "nucor":             "NUE",
    "cameco":            "CCJ",
    "trump media":       "DJT",
    "dell":              "DELL",
    "rare earth magnets": None,
    "critical minerals":  None,
    "tariff":              None,
}

# ---------------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"mentions": []}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# SOURCE 1: Truth Social (direct — highest confidence signal)
# ---------------------------------------------------------------------------

def fetch_truth_social():
    """
    Pull recent posts from an unofficial Truth Social mirror's RSS feed.
    These mentions are tagged source='truth_social', direct=True, since
    they're Trump's own words, not a journalist's paraphrase.
    """
    if feedparser is None:
        print("feedparser not installed — skipping Truth Social source.")
        return []
    try:
        feed = feedparser.parse(TRUTHSOCIAL_MIRROR_RSS)
        if feed.bozo:  # malformed feed / mirror likely down
            print(f"Truth Social mirror feed looks broken: {feed.bozo_exception}")
            return []
        items = []
        for entry in feed.entries[:50]:
            items.append({
                "title": entry.get("title", ""),
                "description": entry.get("summary", ""),
                "url": entry.get("link", ""),
                "publishedAt": entry.get("published", dt.datetime.utcnow().isoformat()),
                "source": "truth_social",
                "direct": True,
            })
        return items
    except Exception as e:
        print(f"Truth Social fetch failed: {e}")
        return []


# ---------------------------------------------------------------------------
# SOURCE 2: White House press releases (direct — official channel)
# ---------------------------------------------------------------------------

def fetch_whitehouse():
    if feedparser is None:
        return []
    try:
        feed = feedparser.parse(WHITEHOUSE_RSS)
        items = []
        for entry in feed.entries[:30]:
            items.append({
                "title": entry.get("title", ""),
                "description": entry.get("summary", ""),
                "url": entry.get("link", ""),
                "publishedAt": entry.get("published", dt.datetime.utcnow().isoformat()),
                "source": "whitehouse.gov",
                "direct": True,
            })
        return items
    except Exception as e:
        print(f"White House RSS fetch failed: {e}")
        return []


# ---------------------------------------------------------------------------
# SOURCE 3: News wires (indirect — someone reporting ON Trump, not him
# speaking directly; still useful, weighted lower)
# ---------------------------------------------------------------------------

def fetch_news():
    if not NEWSAPI_KEY:
        print("NEWSAPI_KEY not set — skipping news source.")
        return []
    since = (dt.datetime.utcnow() - dt.timedelta(hours=LOOKBACK_HOURS)).isoformat()
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "Trump AND (stock OR stocks OR invest OR market OR company)",
        "from": since,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 100,
        "apiKey": NEWSAPI_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
        for a in articles:
            a["source"] = a.get("source", {}).get("name", "news")
            a["direct"] = False
        return articles
    except Exception as e:
        print(f"News fetch failed: {e}")
        return []


# ---------------------------------------------------------------------------
# MENTION DETECTION (combined across sources)
# ---------------------------------------------------------------------------

def find_new_mentions(all_items, state):
    already_logged_today = {
        (m["keyword"], m["date_detected"][:10]) for m in state["mentions"]
    }
    today_str = dt.date.today().isoformat()

    new_mentions = []
    for item in all_items:
        text = f"{item.get('title','')} {item.get('description','')}".lower()
        for keyword, ticker in WATCHLIST.items():
            if keyword in text and (keyword, today_str) not in already_logged_today:
                price = get_price(ticker) if ticker else None
                mention = {
                    "keyword": keyword,
                    "ticker": ticker,
                    "date_detected": dt.datetime.utcnow().isoformat(),
                    "price_at_detection": price,
                    "headline": item.get("title"),
                    "url": item.get("url"),
                    "source": item.get("source", "unknown"),
                    "direct": item.get("direct", False),
                }
                new_mentions.append(mention)
                already_logged_today.add((keyword, today_str))
    return new_mentions


# ---------------------------------------------------------------------------
# PRICE DATA
# ---------------------------------------------------------------------------

def get_price(ticker):
    if not ticker:
        return None
    try:
        data = yf.Ticker(ticker).history(period="1d")
        return round(float(data["Close"].iloc[-1]), 4) if not data.empty else None
    except Exception:
        return None


def get_price_history_context(ticker, days=30):
    if not ticker:
        return None
    try:
        data = yf.Ticker(ticker).history(period=f"{days+5}d")
        return round(float(data["Close"].iloc[0]), 4) if not data.empty else None
    except Exception:
        return None


def build_trend_updates(state):
    updates = []
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=TRACK_DAYS)
    still_active = []

    for m in state["mentions"]:
        detected = dt.datetime.fromisoformat(m["date_detected"])
        if detected < cutoff:
            continue
        still_active.append(m)

        if not m["ticker"] or m["price_at_detection"] is None:
            continue

        current_price = get_price(m["ticker"])
        if current_price is None:
            continue

        pct_change = round(
            (current_price - m["price_at_detection"]) / m["price_at_detection"] * 100, 2
        )
        days_tracked = (dt.datetime.utcnow() - detected).days

        updates.append({
            "ticker": m["ticker"],
            "keyword": m["keyword"],
            "days_tracked": days_tracked,
            "price_at_detection": m["price_at_detection"],
            "current_price": current_price,
            "pct_change": pct_change,
            "headline": m["headline"],
            "direct": m.get("direct", False),
            "source": m.get("source", "unknown"),
        })

    state["mentions"] = still_active
    return updates


# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------

def build_email_body(new_mentions, trend_updates):
    lines = [f"Trump Stock Digest (multi-source) — {dt.date.today().isoformat()}\n"]

    direct_new = [m for m in new_mentions if m["direct"]]
    indirect_new = [m for m in new_mentions if not m["direct"]]

    lines.append("=== DIRECT MENTIONS (Truth Social / White House) ===")
    if not direct_new:
        lines.append("None today.\n")
    else:
        for m in direct_new:
            baseline = get_price_history_context(m["ticker"], 30) if m["ticker"] else None
            lines.append(f"- [{m['source']}] Keyword: '{m['keyword']}'")
            if m["ticker"]:
                lines.append(f"  Ticker: {m['ticker']} | Price now: {m['price_at_detection']}"
                              f" | 30d ago: {baseline}")
            lines.append(f"  \"{m['headline']}\"")
            lines.append(f"  {m['url']}\n")

    lines.append("=== INDIRECT MENTIONS (news coverage) ===")
    if not indirect_new:
        lines.append("None today.\n")
    else:
        for m in indirect_new:
            lines.append(f"- [{m['source']}] '{m['keyword']}' -> "
                         f"{m['ticker'] or 'no ticker'}: {m['headline']}")
        lines.append("")

    lines.append("=== TREND UPDATES (active tracking window) ===")
    if not trend_updates:
        lines.append("None active.\n")
    else:
        for u in trend_updates:
            tag = "DIRECT" if u["direct"] else "indirect"
            arrow = "UP" if u["pct_change"] > 0 else "DOWN" if u["pct_change"] < 0 else "FLAT"
            lines.append(
                f"- [{tag}] {u['ticker']} (\"{u['keyword']}\"), day {u['days_tracked']}: "
                f"{u['price_at_detection']} -> {u['current_price']} ({arrow} {u['pct_change']}%)"
            )
        lines.append("")

    lines.append(
        "Note: DIRECT mentions (Trump's own channels) are generally more meaningful "
        "than indirect news coverage, but neither is a trading signal. Confirmed "
        "multi-day trends are usually already priced in by the time they're 'confirmed.'"
    )
    return "\n".join(lines)


def send_email(subject, body):
    if not (EMAIL_FROM and EMAIL_APP_PASSWORD and EMAIL_TO):
        print("Email credentials not set — printing digest instead:\n")
        print(body)
        return
    msg = MIMEMultipart()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    print("Email sent.")


# ---------------------------------------------------------------------------
# WEBSITE JSON EXPORT
# ---------------------------------------------------------------------------

def humanize_delta(iso_timestamp):
    """Turn an ISO timestamp into a rough '2h ago' style string for the site."""
    try:
        then = dt.datetime.fromisoformat(iso_timestamp)
    except Exception:
        return "recently"
    delta = dt.datetime.utcnow() - then
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def export_site_json(new_mentions, trend_updates, state):
    """
    Write mentions.json in the exact shape tracker_dashboard.html expects:
      { "directMentions": [...], "indirectMentions": [...], "tracked": [...] }
    Overwrite each run so the site always shows the latest snapshot. Only
    today's new mentions go in the feed columns; ALL still-active tracked
    mentions (not just today's) go in the tracked-positions table.
    """
    direct, indirect = [], []
    for m in new_mentions:
        entry = {
            "source": m["source"],
            "time": humanize_delta(m["date_detected"]),
            "headline": m["headline"] or "(no headline)",
            "ticker": m["ticker"],
            "keyword": m["keyword"],
            "url": m["url"] or "#",
        }
        (direct if m["direct"] else indirect).append(entry)

    tracked = [
        {
            "ticker": u["ticker"],
            "keyword": u["keyword"],
            "at": u["price_at_detection"],
            "now": u["current_price"],
            "days": u["days_tracked"],
        }
        for u in trend_updates
    ]

    payload = {
        "generatedAt": dt.datetime.utcnow().isoformat(),
        "directMentions": direct,
        "indirectMentions": indirect,
        "tracked": tracked,
    }

    with open(MENTIONS_JSON_FILE, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {MENTIONS_JSON_FILE}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    state = load_state()

    all_items = []
    all_items.extend(fetch_truth_social())
    all_items.extend(fetch_whitehouse())
    all_items.extend(fetch_news())

    new_mentions = find_new_mentions(all_items, state)
    state["mentions"].extend(new_mentions)

    trend_updates = build_trend_updates(state)
    save_state(state)
    export_site_json(new_mentions, trend_updates, state)  # always refresh the site data

    direct_count = sum(1 for m in new_mentions if m["direct"])
    if new_mentions or trend_updates:
        body = build_email_body(new_mentions, trend_updates)
        send_email(
            subject=f"Trump Stock Digest — {direct_count} direct, "
                    f"{len(new_mentions) - direct_count} indirect mention(s)",
            body=body,
        )
    else:
        print("Nothing new to report today.")


if __name__ == "__main__":
    main()
