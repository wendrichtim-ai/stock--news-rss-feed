from __future__ import annotations

import hashlib
import html
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
OUTPUT_DIR = Path("docs")
MAX_TICKERS = 5
ARTICLES_PER_TICKER = 20


@dataclass(frozen=True)
class Signal:
    ticker: str
    label: str
    score: float


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_tickers(raw: str) -> list[str]:
    values: list[str] = []
    for part in raw.split(","):
        ticker = part.strip().upper()
        if ticker and ticker not in values:
            values.append(ticker)
        if len(values) >= MAX_TICKERS:
            break
    return values or ["AAPL", "MSFT", "NVDA"]


def normalize_sentiment(raw_label: str, score: float) -> str:
    label = (raw_label or "").lower().replace("_", "-")
    if "bullish" in label or score >= 0.15:
        return "Bullish"
    if "bearish" in label or score <= -0.15:
        return "Bearish"
    return "Neutral"


def parse_provider_time(value: str | None) -> datetime:
    if value:
        for pattern in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
            try:
                return datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return datetime.now(timezone.utc)


def signal_for_ticker(article: dict[str, Any], ticker: str) -> Signal:
    for entry in article.get("ticker_sentiment", []) or []:
        if str(entry.get("ticker", "")).upper() == ticker:
            score = safe_float(entry.get("ticker_sentiment_score"))
            raw = str(entry.get("ticker_sentiment_label", "Neutral"))
            return Signal(ticker, normalize_sentiment(raw, score), score)

    score = safe_float(article.get("overall_sentiment_score"))
    raw = str(article.get("overall_sentiment_label", "Neutral"))
    return Signal(ticker, normalize_sentiment(raw, score), score)


def fetch_ticker(ticker: str, api_key: str) -> list[dict[str, Any]]:
    response = requests.get(
        ALPHA_VANTAGE_URL,
        params={
            "function": "NEWS_SENTIMENT",
            "tickers": ticker,
            "sort": "LATEST",
            "limit": 100,
            "apikey": api_key,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    error = payload.get("Error Message") or payload.get("Information") or payload.get("Note")
    if error and not payload.get("feed"):
        raise RuntimeError(str(error))

    feed = payload.get("feed", [])
    return feed if isinstance(feed, list) else []


def collect_news(tickers: list[str], api_key: str) -> tuple[list[dict[str, Any]], list[str]]:
    merged: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for index, ticker in enumerate(tickers):
        try:
            articles = fetch_ticker(ticker, api_key)
        except Exception as exc:  # Preserve successful tickers if one request fails.
            errors.append(f"{ticker}: {exc}")
            articles = []

        for article in articles[:ARTICLES_PER_TICKER]:
            title = str(article.get("title", "Untitled")).strip()
            url = str(article.get("url", "")).strip()
            key = url or hashlib.sha256(title.encode("utf-8")).hexdigest()
            signal = signal_for_ticker(article, ticker)

            if key not in merged:
                merged[key] = {
                    "title": title,
                    "url": url,
                    "summary": str(article.get("summary", "")).strip(),
                    "source": str(article.get("source", "Unknown source")).strip(),
                    "published": parse_provider_time(article.get("time_published")),
                    "signals": [],
                }

            existing = {item.ticker for item in merged[key]["signals"]}
            if ticker not in existing:
                merged[key]["signals"].append(signal)

        # Keep calls comfortably separated for free-tier rate limits.
        if index < len(tickers) - 1:
            time.sleep(13)

    news = sorted(merged.values(), key=lambda item: item["published"], reverse=True)
    return news[:80], errors


def build_rss(tickers: list[str], news: list[dict[str, Any]], site_url: str) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"Stock News Signal: {', '.join(tickers)}"
    ET.SubElement(channel, "link").text = site_url
    ET.SubElement(channel, "description").text = (
        "Stock news with automated ticker-specific bullish, neutral, or bearish labels."
    )
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(channel, "ttl").text = "480"

    for article in news:
        item = ET.SubElement(channel, "item")
        signal_text = ", ".join(
            f"{signal.ticker} {signal.label} ({signal.score:+.2f})"
            for signal in article["signals"]
        )
        ET.SubElement(item, "title").text = f"[{signal_text}] {article['title']}"
        ET.SubElement(item, "link").text = article["url"] or site_url
        guid_source = f"{article['url']}|{article['title']}|{signal_text}"
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = hashlib.sha256(
            guid_source.encode("utf-8")
        ).hexdigest()
        ET.SubElement(item, "pubDate").text = format_datetime(article["published"])
        ET.SubElement(item, "source").text = article["source"]
        ET.SubElement(item, "category").text = signal_text
        ET.SubElement(item, "description").text = (
            f"Sentiment: {signal_text}\n\n{article['summary']}"
        )

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def build_html(tickers: list[str], news: list[dict[str, Any]], errors: list[str]) -> str:
    updated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    cards: list[str] = []
    for article in news:
        badges = "".join(
            f'<span class="signal {signal.label.lower()}">'
            f'{html.escape(signal.ticker)} · {signal.label} ({signal.score:+.2f})</span>'
            for signal in article["signals"]
        )
        cards.append(
            f"""
            <article class="card">
              <div class="signals">{badges}</div>
              <h2><a href="{html.escape(article['url'], quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(article['title'])}</a></h2>
              <div class="meta">{html.escape(article['source'])} · {article['published'].strftime('%d %b %Y, %H:%M UTC')}</div>
              <p>{html.escape(article['summary'][:700])}</p>
            </article>
            """
        )

    error_html = "".join(f'<div class="error">{html.escape(error)}</div>' for error in errors)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stock News Signal</title>
  <style>
    :root {{ color-scheme: light dark; --bg:#f4f6f8; --panel:#fff; --text:#15202b; --muted:#5f6b76; --line:#dfe3e8; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--text); }}
    header {{ background:#101820; color:#fff; padding:34px 20px 28px; }}
    .wrap {{ width:min(1000px,calc(100% - 32px)); margin:0 auto; }}
    h1 {{ margin:0 0 8px; font-size:clamp(2rem,6vw,3.3rem); letter-spacing:-.04em; }}
    header p {{ margin:0; color:#cbd5df; }}
    .feedbar,.error {{ margin-top:18px; padding:14px 16px; background:var(--panel); border:1px solid var(--line); border-radius:10px; overflow-wrap:anywhere; }}
    .feedbar a {{ font-weight:750; }}
    .error {{ border-color:#cf3c4f; }}
    main {{ display:grid; gap:16px; padding:20px 0 50px; }}
    .card {{ padding:18px; background:var(--panel); border:1px solid var(--line); border-radius:14px; }}
    .signals {{ display:flex; flex-wrap:wrap; gap:7px; margin-bottom:10px; }}
    .signal {{ padding:5px 9px; border-radius:999px; font-size:.78rem; font-weight:800; }}
    .bullish {{ background:#d9f7e6; color:#087b42; }}
    .bearish {{ background:#ffe1e5; color:#b31f36; }}
    .neutral {{ background:#e8edf2; color:#4f5b66; }}
    h2 {{ margin:0 0 7px; font-size:1.15rem; line-height:1.35; }}
    h2 a {{ color:inherit; text-decoration:none; }}
    h2 a:hover {{ text-decoration:underline; }}
    .meta,.card p,footer {{ color:var(--muted); }}
    .meta {{ font-size:.86rem; }}
    .card p {{ line-height:1.55; margin:12px 0 0; }}
    footer {{ padding:0 0 40px; font-size:.86rem; }}
    @media (prefers-color-scheme:dark) {{ :root {{ --bg:#0d1319; --panel:#151d25; --text:#eef3f7; --muted:#a9b5c0; --line:#2a3742; }} .bullish {{ background:#123c2a; color:#7ae4aa; }} .bearish {{ background:#481c25; color:#ff9aaa; }} .neutral {{ background:#29343e; color:#d6dee5; }} }}
  </style>
</head>
<body>
<header><div class="wrap"><h1>Stock News Signal</h1><p>{html.escape(', '.join(tickers))} · Updated {updated}</p></div></header>
<div class="wrap">
  <div class="feedbar"><strong>Feedly URL:</strong> <a href="feed.xml">feed.xml</a></div>
  {error_html}
  <main>{''.join(cards) or '<div class="card">No articles are available yet.</div>'}</main>
  <footer>Automated article sentiment is not a price forecast or investment recommendation.</footer>
</div>
</body>
</html>"""


def main() -> int:
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
    if not api_key:
        print("ALPHA_VANTAGE_API_KEY is missing.", file=sys.stderr)
        return 2

    tickers = parse_tickers(os.getenv("STOCK_TICKERS", "AAPL,MSFT,NVDA"))
    repository = os.getenv("GITHUB_REPOSITORY", "YOUR-USER/stock-news-rss")
    owner, _, repo = repository.partition("/")
    site_url = f"https://{owner}.github.io/{repo}/" if owner and repo else "./"

    news, errors = collect_news(tickers, api_key)
    if not news:
        print("No news was returned; keeping the existing published feed.", file=sys.stderr)
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "feed.xml").write_bytes(build_rss(tickers, news, site_url))
    (OUTPUT_DIR / "index.html").write_text(build_html(tickers, news, errors), encoding="utf-8")
    (OUTPUT_DIR / ".nojekyll").write_text("", encoding="utf-8")

    print(f"Generated {len(news)} articles for {', '.join(tickers)}")
    for error in errors:
        print(f"Warning: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
