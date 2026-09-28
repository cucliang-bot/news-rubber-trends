#!/usr/bin/env python3
"""
Cloud storage push module.

Pushes candidates and final articles to jsonbin and GitHub Gist as backup.
Reads config from config/cloud_config.json or environment variables.

Usage:
    python scripts/push_to_cloud.py --candidates data/2026-09-28/candidates.json --date 2026-09-28
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
CLOUD_CONFIG_PATH = PROJECT_DIR / "config" / "cloud_config.json"

JSONBIN_API_BASE = "https://api.jsonbin.io/v3/b"


def _load_config() -> dict:
    """Load cloud config from file or environment variables."""
    config = {}

    # Try loading from file
    if CLOUD_CONFIG_PATH.exists():
        try:
            with open(CLOUD_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Failed to load cloud config from file: %s", e)

    # Override with environment variables
    master_key = os.environ.get("JSONBIN_MASTER_KEY", "")
    if master_key:
        config.setdefault("jsonbin", {})["master_key"] = master_key

    gist_token = os.environ.get("GIST_TOKEN", "")
    if gist_token:
        config.setdefault("gist", {})["token"] = gist_token

    gist_id = os.environ.get("GIST_ID", "")
    if gist_id:
        config.setdefault("gist", {})["id"] = gist_id

    return config


def _get_bin_id(config: dict, bin_name: str) -> str:
    """Get a jsonbin bin ID from config."""
    bins = config.get("jsonbin", {}).get("bins", {})
    return bins.get(bin_name, "")


def _get_master_key(config: dict) -> str:
    """Get jsonbin master key from config."""
    return config.get("jsonbin", {}).get("master_key", "")


def _jsonbin_put(bin_id: str, data, master_key: str) -> bool:
    """PUT data to a jsonbin bin."""
    if not bin_id or not master_key:
        logger.warning("jsonbin bin_id or master_key not configured, skipping push")
        return False

    url = f"{JSONBIN_API_BASE}/{bin_id}"
    headers = {
        "X-Master-Key": master_key,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.put(url, headers=headers, json=data, timeout=30)
        resp.raise_for_status()
        logger.info("jsonbin PUT success: bin=%s", bin_id)
        return True
    except Exception as e:
        logger.error("jsonbin PUT failed for bin %s: %s", bin_id, e)
        return False


def _jsonbin_get(bin_id: str, master_key: str) -> dict | list | None:
    """GET latest data from a jsonbin bin."""
    if not bin_id or not master_key:
        logger.warning("jsonbin bin_id or master_key not configured, skipping get")
        return None

    url = f"{JSONBIN_API_BASE}/{bin_id}/latest"
    headers = {"X-Master-Key": master_key}

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # jsonbin wraps data in a 'record' key
        return data.get("record", data)
    except Exception as e:
        logger.error("jsonbin GET failed for bin %s: %s", bin_id, e)
        return None


def push_candidates(candidates: list[dict], date_str: str, config: dict | None = None) -> bool:
    """
    Push candidates to jsonbin candidates bin.

    Args:
        candidates: List of candidate article dicts
        date_str: Date string (YYYY-MM-DD)
        config: Cloud config dict (loaded from file/env if None)

    Returns:
        True on success
    """
    if config is None:
        config = _load_config()

    master_key = _get_master_key(config)
    bin_id = _get_bin_id(config, "candidates")

    payload = {
        "date": date_str,
        "updated_at": datetime.now().isoformat(),
        "count": len(candidates),
        "data": candidates,
    }

    return _jsonbin_put(bin_id, payload, master_key)


def push_final(articles: list[dict], date_str: str, config: dict | None = None) -> bool:
    """
    Push final articles to jsonbin final bin.

    Args:
        articles: List of final article dicts (with full text)
        date_str: Date string (YYYY-MM-DD)
        config: Cloud config dict

    Returns:
        True on success
    """
    if config is None:
        config = _load_config()

    master_key = _get_master_key(config)
    bin_id = _get_bin_id(config, "final")

    payload = {
        "date": date_str,
        "updated_at": datetime.now().isoformat(),
        "count": len(articles),
        "data": articles,
    }

    return _jsonbin_put(bin_id, payload, master_key)


def reset_confirm_queue(candidates: list[dict], date_str: str, config: dict | None = None) -> bool:
    """
    Reset the confirm queue with current candidates data.

    The confirm queue is read by the web selection page and written to
    when colleagues select articles.

    Args:
        candidates: List of candidate article dicts
        date_str: Date string (YYYY-MM-DD)
        config: Cloud config dict

    Returns:
        True on success
    """
    if config is None:
        config = _load_config()

    master_key = _get_master_key(config)
    bin_id = _get_bin_id(config, "confirm")

    # Build confirm queue entries
    queue_entries = []
    for i, c in enumerate(candidates):
        queue_entries.append({
            "id": i + 1,
            "title_cn": c.get("title_cn", ""),
            "title_en": c.get("title_en", ""),
            "source": c.get("source", ""),
            "source_id": c.get("source_id", ""),
            "source_urls": c.get("source_urls", []),
            "summary_cn": c.get("summary_cn", ""),
            "key_entities": c.get("key_entities", []),
            "pub_date": c.get("pub_date", ""),
            "status": "pending",
            "selected": False,
        })

    payload = {
        "date": date_str,
        "updated_at": datetime.now().isoformat(),
        "count": len(queue_entries),
        "entries": queue_entries,
    }

    return _jsonbin_put(bin_id, payload, master_key)


def get_confirm_queue(config: dict | None = None) -> dict | None:
    """
    Read the current confirm queue from jsonbin.

    Returns:
        Confirm queue data dict, or None on failure
    """
    if config is None:
        config = _load_config()

    master_key = _get_master_key(config)
    bin_id = _get_bin_id(config, "confirm")

    return _jsonbin_get(bin_id, master_key)


def push_to_gist(candidates: list[dict], final: list[dict] | None = None, config: dict | None = None) -> bool:
    """
    Push data to GitHub Gist as backup.

    Args:
        candidates: Current candidates list
        final: Optional final articles list
        config: Cloud config dict

    Returns:
        True on success
    """
    if config is None:
        config = _load_config()

    gist_config = config.get("gist", {})
    token = gist_config.get("token", "")
    gist_id = gist_config.get("id", "")

    if not token or not gist_id:
        logger.warning("Gist token or ID not configured, skipping gist push")
        return False

    # Build gist content
    files = {}

    if candidates:
        files["candidates.json"] = {
            "content": json.dumps(
                {
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "count": len(candidates),
                    "candidates": candidates,
                },
                ensure_ascii=False,
                indent=2,
            )
        }

    if final:
        files["final.json"] = {
            "content": json.dumps(
                {
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "count": len(final),
                    "articles": final,
                },
                ensure_ascii=False,
                indent=2,
            )
        }

    if not files:
        return True

    url = f"https://api.github.com/gists/{gist_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    payload = {"files": files}

    try:
        resp = requests.patch(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        logger.info("Gist update success: gist_id=%s", gist_id)
        return True
    except Exception as e:
        logger.error("Gist update failed: %s", e)
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Push data to cloud storage")
    parser.add_argument("--candidates", type=str, default=None, help="Candidates JSON file")
    parser.add_argument("--final", type=str, default=None, help="Final articles JSON file")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--push-gist", action="store_true", help="Also push to gist")
    args = parser.parse_args()

    config = _load_config()

    candidates = []
    if args.candidates:
        with open(args.candidates, "r", encoding="utf-8") as f:
            data = json.load(f)
            candidates = data if isinstance(data, list) else data.get("candidates", [])

    final = []
    if args.final:
        with open(args.final, "r", encoding="utf-8") as f:
            data = json.load(f)
            final = data if isinstance(data, list) else data.get("articles", [])

    if candidates:
        push_candidates(candidates, args.date, config)
        reset_confirm_queue(candidates, args.date, config)

    if final:
        push_final(final, args.date, config)

    if args.push_gist:
        push_to_gist(candidates, final if final else None, config)


if __name__ == "__main__":
    main()
