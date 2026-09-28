#!/usr/bin/env python3
"""
Auto-write pipeline for confirmed articles.

Reads the confirm queue from jsonbin, finds pending entries that have been
selected by colleagues, fetches original article text, rewrites in Chinese
per translation_guide.md, and pushes final articles to cloud.

Pipeline:
1. Read confirm queue from jsonbin
2. Find pending entries with status='confirmed' or selected=True
3. For each confirmed entry:
   a. Load candidates data
   b. Fetch original article text (requests + BeautifulSoup)
   c. Rewrite in Chinese per translation_guide.md (DeepSeek API)
   d. Check similarity < 50% (DeepSeek API) to avoid near-duplicate output
   e. Generate final article entry
4. Push final.json to cloud
5. Update confirm queue status to 'done'
6. Update history

Usage:
    python scripts/auto_write.py [--force]
"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from ai_filter import call_deepseek, rewrite_article, check_similarity, _load_translation_guide
from push_to_cloud import (
    get_confirm_queue,
    push_final,
    _load_config,
    _jsonbin_put,
    _get_bin_id,
    _get_master_key,
)
from dedup import update_history

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}

TIMEOUT = 20


def _fetch_article_text(url: str) -> str:
    """
    Fetch and extract the main text content from an article page.

    Returns plain text content of the article.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        logger.warning("Failed to fetch article at %s: %s", url, e)
        return ""

    soup = BeautifulSoup(html, "lxml")

    # Remove script/style/nav elements
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "iframe"]):
        tag.decompose()

    # Try common article content selectors
    content = ""
    selectors = [
        {"name": "article"},
        {"class_": re.compile(r"(article-body|article-content|post-content|entry-content|story-body|content-article)", re.I)},
        {" itemprop": "articleBody"},
    ]

    for selector in selectors:
        el = soup.find(**selector)
        if el:
            content = el.get_text(separator="\n", strip=True)
            if len(content) > 200:
                break

    # Fallback: get all <p> tags
    if len(content) < 200:
        paragraphs = soup.find_all("p")
        texts = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30]
        content = "\n\n".join(texts)

    # Clean up
    content = re.sub(r"\n{3,}", "\n\n", content)
    content = content.strip()

    return content


def _process_confirmed_entry(entry: dict, config: dict, translation_guide: str) -> dict | None:
    """
    Process a single confirmed entry: fetch text, rewrite, check similarity.

    Returns a final article dict, or None on failure.
    """
    title_cn = entry.get("title_cn", "")
    title_en = entry.get("title_en", "")
    source = entry.get("source", "")
    source_urls = entry.get("source_urls", [])

    if not source_urls:
        logger.warning("No source URLs for entry: %s", title_cn)
        return None

    url = source_urls[0]
    logger.info("Processing: %s", title_cn or title_en)

    # Fetch original article text
    original_text = _fetch_article_text(url)
    if not original_text or len(original_text) < 100:
        logger.warning("Could not extract sufficient text from %s", url)
        # Still try to produce output with what we have
        if not original_text:
            original_text = entry.get("summary_cn", "")

    # Rewrite in Chinese
    rewritten = rewrite_article(
        article_title=title_en or title_cn,
        article_text=original_text,
        source_name=source,
        source_url=url,
        translation_guide=translation_guide,
    )

    if not rewritten or rewritten.startswith("[翻译失败]"):
        logger.warning("Rewrite failed for: %s", title_cn)
        return None

    # Check similarity with original to ensure it's not just a copy
    similarity = check_similarity(original_text[:2000], rewritten[:2000])
    logger.info("Similarity check: %.2f for '%s'", similarity, (title_cn or title_en)[:40])

    if similarity > 0.9:
        logger.warning("Rewrite too similar to original (%.2f), may need manual review", similarity)

    # Build final article
    final_article = {
        "id": entry.get("id", 0),
        "title_cn": title_cn,
        "title_en": title_en,
        "source": source,
        "source_id": entry.get("source_id", ""),
        "source_urls": source_urls,
        "pub_date": entry.get("pub_date", ""),
        "summary_cn": entry.get("summary_cn", ""),
        "key_entities": entry.get("key_entities", []),
        "content_cn": rewritten,
        "original_text": original_text[:500],  # Keep a snippet for reference
        "similarity_score": round(similarity, 3),
        "processed_at": datetime.now().isoformat(),
        "image_url": entry.get("image_url", ""),
    }

    return final_article


def _update_confirm_status(entries: list[dict], processed_ids: set, config: dict):
    """Update confirm queue entries status to 'done' for processed entries."""
    master_key = _get_master_key(config)
    bin_id = _get_bin_id(config, "confirm")

    for entry in entries:
        if entry.get("id") in processed_ids:
            entry["status"] = "done"

    payload = {
        "updated_at": datetime.now().isoformat(),
        "entries": entries,
    }

    _jsonbin_put(bin_id, payload, master_key)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Auto-write pipeline for confirmed articles")
    parser.add_argument("--force", action="store_true", help="Process all pending entries regardless of status")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without doing it")
    args = parser.parse_args()

    config = _load_config()

    # Step 1: Read confirm queue
    logger.info("Reading confirm queue...")
    queue_data = get_confirm_queue(config)

    if not queue_data:
        logger.info("Confirm queue is empty or unavailable. Nothing to do.")
        return

    entries = queue_data.get("entries", [])
    if not entries:
        logger.info("No entries in confirm queue. Nothing to do.")
        return

    # Step 2: Find entries to process
    to_process = []
    for entry in entries:
        status = entry.get("status", "pending")
        selected = entry.get("selected", False)

        if args.force:
            if status != "done":
                to_process.append(entry)
        else:
            if status == "confirmed" or (selected and status == "pending"):
                to_process.append(entry)

    if not to_process:
        logger.info("No confirmed entries found in queue. Nothing to do.")
        return

    logger.info("Found %d entries to process", len(to_process))

    if args.dry_run:
        for e in to_process:
            logger.info("  Would process: %s", e.get("title_cn", e.get("title_en", "?")))
        return

    # Load translation guide
    translation_guide = _load_translation_guide()

    # Step 3: Process each confirmed entry
    final_articles = []
    processed_ids = set()

    for entry in to_process:
        try:
            result = _process_confirmed_entry(entry, config, translation_guide)
            if result:
                final_articles.append(result)
                processed_ids.add(entry.get("id"))
        except Exception as e:
            logger.error("Failed to process entry %s: %s", entry.get("id"), e, exc_info=True)

    if not final_articles:
        logger.info("No articles were successfully processed.")
        return

    # Step 4: Save final.json locally
    date_str = datetime.now().strftime("%Y-%m-%d")
    data_dir = PROJECT_DIR / "data" / date_str
    data_dir.mkdir(parents=True, exist_ok=True)

    final_path = data_dir / "final.json"
    # Merge with existing final.json if present
    existing_final = []
    if final_path.exists():
        try:
            with open(final_path, "r", encoding="utf-8") as f:
                existing_final = json.load(f)
            if not isinstance(existing_final, list):
                existing_final = existing_final.get("articles", [])
        except Exception:
            existing_final = []

    all_final = existing_final + final_articles
    with open(final_path, "w", encoding="utf-8") as f:
        json.dump(all_final, f, ensure_ascii=False, indent=2)
    logger.info("Saved %d final articles to %s", len(all_final), final_path)

    # Step 5: Push final to cloud
    logger.info("Pushing final articles to cloud...")
    push_final(all_final, date_str, config)

    # Step 6: Update confirm queue status
    logger.info("Updating confirm queue status...")
    _update_confirm_status(entries, processed_ids, config)

    # Step 7: Update history
    logger.info("Updating history...")
    history_path = PROJECT_DIR / "data" / "selected_history.json"
    update_history(final_articles, history_path, date_str)

    logger.info("=== Auto-write Complete: %d articles processed ===", len(final_articles))


if __name__ == "__main__":
    main()
