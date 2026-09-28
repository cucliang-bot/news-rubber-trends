#!/usr/bin/env python3
"""
Multi-source news fetcher for rubber/tire industry.

Fetches article lists from 8 configured sources, parses HTML to extract
article links (title, url, date), and returns a unified list of dicts.

Usage:
    python scripts/fetch_news.py [--days-back 3] [--source erj]
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
SOURCES_PATH = PROJECT_DIR / "config" / "sources.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}

TIMEOUT = 15

GOOGLE_NEWS_RSS_URL = (
    "https://news.google.com/rss/search?q=rubber+tire&hl=en&gl=US&ceid=US:en"
)


def _fetch_html(url: str, extra_headers: dict | None = None) -> str | None:
    """Fetch a URL and return HTML text, or None on failure."""
    hdrs = dict(HEADERS)
    if extra_headers:
        hdrs.update(extra_headers)
    try:
        resp = requests.get(url, headers=hdrs, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return None


def _parse_date(text: str) -> str:
    """Try to extract a YYYY-MM-DD date from text."""
    if not text:
        return ""
    for pattern in [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}/\d{2}/\d{2})",
        r"(\d{4}\.\d{2}\.\d{2})",
        r"(\d{2}/\d{2}/\d{4})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
    ]:
        m = re.search(pattern, text)
        if m:
            raw = m.group(1)
            if "年" in raw:
                raw = raw.replace("年", "-").replace("月", "-").replace("日", "")
            elif "/" in raw and raw.startswith("20"):
                raw = raw.replace("/", "-")
            elif "/" in raw:
                parts = raw.split("/")
                raw = f"{parts[2]}-{parts[0]}-{parts[1]}"
            return raw
    return ""


# ── Source-specific parsers ──────────────────────────────────────────────────


def _parse_tanhei(source_cfg: dict, days_back: int) -> list[dict]:
    """Parse tanhei.com (炭黑产业网) homepage."""
    base = source_cfg["base_url"].rstrip("/")
    html = _fetch_html(base + "/")
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    today = datetime.now()
    cutoff = today - timedelta(days=days_back)
    results = []
    seen_urls = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 6:
            continue
        if "/news/info/" not in href and "/article/" not in href:
            continue
        url = urljoin(base, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        date_text = ""
        parent = a_tag.find_parent(["li", "div", "span"])
        if parent:
            date_text = _parse_date(parent.get_text())

        results.append({
            "title": title,
            "url": url,
            "source": source_cfg["name"],
            "source_id": source_cfg["id"],
            "pub_date": date_text,
            "fetched_date": today.strftime("%Y-%m-%d"),
        })

    logger.info("tanhei: found %d articles", len(results))
    return results


def _parse_tireworld(source_cfg: dict, days_back: int) -> list[dict]:
    """Parse tireworld.com.cn (轮胎世界网) homepage."""
    base = source_cfg["base_url"].rstrip("/")
    html = _fetch_html(base + "/")
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    today = datetime.now()
    results = []
    seen_urls = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 6:
            continue
        if "/news/info/" not in href and "/article/" not in href and "/content/" not in href:
            continue
        url = urljoin(base, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        date_text = ""
        parent = a_tag.find_parent(["li", "div", "span"])
        if parent:
            date_text = _parse_date(parent.get_text())

        results.append({
            "title": title,
            "url": url,
            "source": source_cfg["name"],
            "source_id": source_cfg["id"],
            "pub_date": date_text,
            "fetched_date": today.strftime("%Y-%m-%d"),
        })

    logger.info("tireworld: found %d articles", len(results))
    return results


def _parse_erj(source_cfg: dict, days_back: int) -> list[dict]:
    """Parse european-rubber-journal.com /section/1/news.

    Falls back to Google News RSS if Cloudflare blocks the request.
    """
    base = source_cfg["base_url"].rstrip("/")
    list_url = base + source_cfg["list_page"]
    html = _fetch_html(list_url)
    today = datetime.now()

    if html and "article" in html.lower() and len(html) > 2000:
        return _parse_erj_html(html, base, source_cfg, today)

    logger.info("ERJ: direct fetch failed or blocked, falling back to Google News RSS")
    return _google_news_rss_fallback(source_cfg, days_back, query="site:european-rubber-journal.com rubber")


def _parse_erj_html(html: str, base: str, source_cfg: dict, today: datetime) -> list[dict]:
    """Parse ERJ article list from HTML."""
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen_urls = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 10:
            continue
        # ERJ article URLs: /YYYY/MM/DD/slug or /article/...
        if not re.match(r".*/\d{4}/\d{2}/\d{2}/", href) and "/article/" not in href:
            continue
        url = urljoin(base, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        date_match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", href)
        pub_date = ""
        if date_match:
            pub_date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"

        results.append({
            "title": title,
            "url": url,
            "source": source_cfg["name"],
            "source_id": source_cfg["id"],
            "pub_date": pub_date,
            "fetched_date": today.strftime("%Y-%m-%d"),
        })

    logger.info("erj: found %d articles", len(results))
    return results


def _parse_rubber_world(source_cfg: dict, days_back: int) -> list[dict]:
    """Parse rubberworld.com homepage."""
    base = source_cfg["base_url"].rstrip("/")
    html = _fetch_html(base + "/")
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    today = datetime.now()
    results = []
    seen_urls = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 10:
            continue
        # Rubber World article URLs typically contain /news/ or article slugs
        if "/news/" not in href and "/article/" not in href and "/feature/" not in href:
            continue
        url = urljoin(base, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        date_text = ""
        parent = a_tag.find_parent(["li", "div", "article", "time"])
        if parent:
            time_tag = parent.find("time")
            if time_tag:
                date_text = _parse_date(time_tag.get("datetime", "") or time_tag.get_text())
            if not date_text:
                date_text = _parse_date(parent.get_text())

        results.append({
            "title": title,
            "url": url,
            "source": source_cfg["name"],
            "source_id": source_cfg["id"],
            "pub_date": date_text,
            "fetched_date": today.strftime("%Y-%m-%d"),
        })

    logger.info("rubber_world: found %d articles", len(results))
    return results


def _parse_just_auto(source_cfg: dict, days_back: int) -> list[dict]:
    """Parse just-auto.com /news/ page."""
    base = source_cfg["base_url"].rstrip("/")
    html = _fetch_html(base + "/news/")
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    today = datetime.now()
    results = []
    seen_urls = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 10:
            continue
        if "/news/" not in href:
            continue
        # Skip the /news/ listing page itself
        if href.rstrip("/") == "/news" or href.rstrip("/") == "/news/":
            continue
        url = urljoin(base, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        date_text = ""
        parent = a_tag.find_parent(["li", "div", "article"])
        if parent:
            time_tag = parent.find("time")
            if time_tag:
                date_text = _parse_date(time_tag.get("datetime", "") or time_tag.get_text())
            if not date_text:
                date_text = _parse_date(parent.get_text())

        results.append({
            "title": title,
            "url": url,
            "source": source_cfg["name"],
            "source_id": source_cfg["id"],
            "pub_date": date_text,
            "fetched_date": today.strftime("%Y-%m-%d"),
        })

    logger.info("just_auto: found %d articles", len(results))
    return results


def _parse_chemanalyst(source_cfg: dict, days_back: int) -> list[dict]:
    """Parse chemanalyst.com /NewsAndDeals/NewsHome page."""
    base = source_cfg["base_url"].rstrip("/")
    html = _fetch_html(base + "/NewsAndDeals/NewsHome")
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    today = datetime.now()
    results = []
    seen_urls = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 10:
            continue
        # Chemanalyst article URLs: /NewsAndDeals/... or /news/...
        if "/NewsAndDeals/" not in href and "/news/" not in href:
            continue
        if href.rstrip("/").endswith("NewsHome"):
            continue
        url = urljoin(base, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        date_text = ""
        parent = a_tag.find_parent(["li", "div", "article", "tr"])
        if parent:
            date_text = _parse_date(parent.get_text())

        results.append({
            "title": title,
            "url": url,
            "source": source_cfg["name"],
            "source_id": source_cfg["id"],
            "pub_date": date_text,
            "fetched_date": today.strftime("%Y-%m-%d"),
        })

    logger.info("chemanalyst: found %d articles", len(results))
    return results


def _parse_tyrepress(source_cfg: dict, days_back: int) -> list[dict]:
    """Parse tyrepress.com /category/news/ page."""
    base = source_cfg["base_url"].rstrip("/")
    html = _fetch_html(base + "/category/news/")
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    today = datetime.now()
    results = []
    seen_urls = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 10:
            continue
        # Tyrepress article URLs: /category/news/YYYY/MM/slug or similar
        if "/category/news/" not in href:
            continue
        # Skip the listing page itself
        if href.rstrip("/") == "/category/news":
            continue
        url = urljoin(base, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        date_text = ""
        parent = a_tag.find_parent(["li", "div", "article"])
        if parent:
            time_tag = parent.find("time")
            if time_tag:
                date_text = _parse_date(time_tag.get("datetime", "") or time_tag.get_text())
            if not date_text:
                date_text = _parse_date(parent.get_text())

        results.append({
            "title": title,
            "url": url,
            "source": source_cfg["name"],
            "source_id": source_cfg["id"],
            "pub_date": date_text,
            "fetched_date": today.strftime("%Y-%m-%d"),
        })

    logger.info("tyrepress: found %d articles", len(results))
    return results


def _parse_tyre_trends(source_cfg: dict, days_back: int) -> list[dict]:
    """Parse tyre-trends.com /news/ page."""
    base = source_cfg["base_url"].rstrip("/")
    html = _fetch_html(base + "/news/")
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    today = datetime.now()
    results = []
    seen_urls = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 10:
            continue
        if "/news/" not in href:
            continue
        if href.rstrip("/") == "/news":
            continue
        url = urljoin(base, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        date_text = ""
        parent = a_tag.find_parent(["li", "div", "article"])
        if parent:
            time_tag = parent.find("time")
            if time_tag:
                date_text = _parse_date(time_tag.get("datetime", "") or time_tag.get_text())
            if not date_text:
                date_text = _parse_date(parent.get_text())

        results.append({
            "title": title,
            "url": url,
            "source": source_cfg["name"],
            "source_id": source_cfg["id"],
            "pub_date": date_text,
            "fetched_date": today.strftime("%Y-%m-%d"),
        })

    logger.info("tyre_trends: found %d articles", len(results))
    return results


# ── Google News RSS fallback ─────────────────────────────────────────────────


def _google_news_rss_fallback(source_cfg: dict, days_back: int, query: str = "rubber tire") -> list[dict]:
    """Fetch articles from Google News RSS as fallback for blocked sources."""
    url = f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
    html = _fetch_html(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    today = datetime.now()
    results = []

    for item in soup.find_all("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        source_el = item.find("source")

        title = title_el.get_text(strip=True) if title_el else ""
        link = link_el.get_text(strip=True) if link_el else ""
        pub_date = ""
        if pub_el:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub_el.get_text(strip=True))
                pub_date = dt.strftime("%Y-%m-%d")
            except Exception:
                pub_date = _parse_date(pub_el.get_text())

        source_name = source_el.get_text(strip=True) if source_el else source_cfg["name"]

        if title and link:
            results.append({
                "title": title,
                "url": link,
                "source": source_name,
                "source_id": source_cfg["id"],
                "pub_date": pub_date,
                "fetched_date": today.strftime("%Y-%m-%d"),
            })

    logger.info("google_news_rss fallback: found %d articles for query '%s'", len(results), query)
    return results


# ── Dispatch table ───────────────────────────────────────────────────────────

SOURCE_PARSERS = {
    "tanhei": _parse_tanhei,
    "tireworld": _parse_tireworld,
    "erj": _parse_erj,
    "rubber_world": _parse_rubber_world,
    "just_auto": _parse_just_auto,
    "chemanalyst": _parse_chemanalyst,
    "tyrepress": _parse_tyrepress,
    "tyre_trends": _parse_tyre_trends,
}


def fetch_all_sources(sources_path: str | Path | None = None, days_back: int = 3, exclude: list[str] | None = None) -> list[dict]:
    """
    Fetch articles from all configured sources.

    Args:
        sources_path: Path to sources.json. Defaults to config/sources.json.
        days_back: Only include articles from the last N days.
        exclude: List of source IDs to skip (e.g., ['erj'] for GitHub Actions).

    Returns:
        List of article dicts with keys:
        title, url, source, source_id, pub_date, fetched_date
    """
    if sources_path is None:
        sources_path = SOURCES_PATH
    sources_path = Path(sources_path)

    with open(sources_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    sources = config.get("trends_sources", [])
    all_articles = []
    exclude = exclude or []

    for src in sources:
        src_id = src["id"]
        if src_id in exclude:
            logger.info("Skipping excluded source '%s'", src_id)
            continue
        parser = SOURCE_PARSERS.get(src_id)
        if parser is None:
            logger.warning("No parser for source '%s', skipping", src_id)
            continue
        try:
            articles = parser(src, days_back)
            all_articles.extend(articles)
        except Exception as e:
            logger.error("Error parsing source '%s': %s", src_id, e, exc_info=True)

    # Deduplicate by URL within the fetch
    seen = set()
    deduped = []
    for art in all_articles:
        if art["url"] not in seen:
            seen.add(art["url"])
            deduped.append(art)

    logger.info("Total articles fetched: %d (from %d sources)", len(deduped), len(sources))
    return deduped


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch news from rubber industry sources")
    parser.add_argument("--days-back", type=int, default=3, help="Look back N days")
    parser.add_argument("--source", type=str, default=None, help="Fetch only this source ID")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file path")
    args = parser.parse_args()

    if args.source:
        sources_path = SOURCES_PATH
        with open(sources_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        src_cfg = [s for s in config["trends_sources"] if s["id"] == args.source]
        if not src_cfg:
            logger.error("Source '%s' not found in config", args.source)
            sys.exit(1)
        src = src_cfg[0]
        parser_fn = SOURCE_PARSERS.get(args.source)
        articles = parser_fn(src, args.days_back) if parser_fn else []
    else:
        articles = fetch_all_sources(days_back=args.days_back)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        logger.info("Wrote %d articles to %s", len(articles), out_path)
    else:
        print(json.dumps(articles, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
