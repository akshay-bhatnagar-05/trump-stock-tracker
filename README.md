# Trump Stock Tracker

A personal research tool that watches for statements from the US president referencing specific companies, sectors, or investing keywords, then tracks how the mentioned stock's price moves over the following four trading days. Delivers a daily email digest and publishes a live dashboard.

**Live site:** https://akshay-bhatnagar-05.github.io/trump-stock-tracker/

---

## What it does

1. **Monitors three sources daily:**
   - Truth Social (via an unofficial RSS mirror) — direct
   - White House press releases (official RSS) — direct
   - News wire coverage (via NewsAPI) — indirect
2. **Matches against a watchlist** of companies/tickers defined in `tracker_v2.py`
3. **Tags every mention** as `direct` (his own words) or `indirect` (someone reporting on him) — direct mentions are generally more meaningful evidence
4. **Tracks price for 4 trading days** after each new mention, using Yahoo Finance data
5. **Sends a daily email digest** with new mentions + trend updates for anything still in its tracking window
6. **Publishes a live dashboard** (`index.html`) reading from `mentions.json`, which the script regenerates on every run

## Why it exists

Built as a hands-on way to catch market-moving statements faster than checking the news manually, and to see — with real before/after price data — whether "Trump mentioned it" is actually a useful signal or mostly noise that's already priced in by the time it's confirmed.

## Architecture

```
GitHub Actions (daily cron, 1pm UTC)
    │
    ▼
tracker_v2.py
    ├── fetches Truth Social / White House / news
    ├── matches against WATCHLIST
    ├── pulls price data via yfinance
    ├── sends email digest (Gmail SMTP)
    └── writes mentions.json + state.json
    │
    ▼
Committed back to repo
    │
    ▼
GitHub Pages rebuilds
    │
    ▼
index.html fetches mentions.json → live dashboard
```

## Files

| File | Purpose |
|---|---|
| `tracker_v2.py` | Main script — detection, tracking, email, JSON export |
| `index.html` | Dashboard — fetches `mentions.json` and renders the feed |
| `mentions.json` | Auto-generated snapshot the dashboard reads from |
| `state.json` | Auto-generated tracking memory (which mentions are still in their 4-day window) |
| `.github/workflows/daily.yml` | Runs the script automatically once a day |

## Setup (for reference / if rebuilding elsewhere)

Requires four repo secrets under **Settings → Secrets and variables → Actions**:

| Secret | What it is |
|---|---|
| `NEWSAPI_KEY` | Free key from [newsapi.org](https://newsapi.org) |
| `EMAIL_FROM` | Gmail address sending the digest |
| `EMAIL_APP_PASSWORD` | Gmail [app password](https://myaccount.google.com/apppasswords) (not your real password) |
| `EMAIL_TO` | Where the digest gets sent |

GitHub Pages is set to deploy from the `main` branch, root folder.

## Editing the watchlist

Open `tracker_v2.py`, find the `WATCHLIST` dictionary near the top, and add/remove entries:

```python
WATCHLIST = {
    "company name": "TICKER",   # tracked with price data
    "thematic keyword": None,   # flagged only, no single ticker to track
}
```

## Limitations — read before trusting this

- **Reactive, not predictive.** By the time a mention is caught, it's already public — other market participants are seeing it too.
- **The Truth Social source depends on an unofficial third-party mirror.** These can go down or change format without notice.
- **Keyword matching is simple substring search**, not true language understanding. It will occasionally misfire on coincidental word matches.
- **This is a research/awareness tool, not a trading signal.** A confirmed multi-day trend has usually already been priced in by the time it's confirmed. Nothing here should be treated as investment advice.

## License / disclaimer

Personal project, provided as-is with no warranty. Not financial advice. The author assumes no liability for any decisions made based on this tool's output.
