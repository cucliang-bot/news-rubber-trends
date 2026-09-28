#!/usr/bin/env python3
"""
Historical deduplication for news candidates.

Compares new candidates against selected_history.json to identify
and remove articles that have already been processed.

Matching logic:
1. Exact URL match → duplicate
2. Key entity overlap > 60% → likely duplicate (confirmed via DeepSeek)
3. Title similarity > 80% → duplicate

Usage:
    python scripts/dedup.py --candidates candidates.json --history data/selected_history.json
"""

import json
import logging
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent

# Add scripts dir to path for importing ai_filter
sys.path.insert(0, str(SCRIPT_DIR))


def _title_similarity(title_a: str, title_b: str) -> float:
    """Compute string similarity between two titles using SequenceMatcher."""
    if not title_a or not title_b:
        return 0.0
    # Normalize: lowercase, strip whitespace
    a = re.sub(r"\s+", " ", title_a.strip().lower())
    b = re.sub(r"\s+", " ", title_b.strip().lower())
    return SequenceMatcher(None, a, b).ratio()


def _entity_overlap(entities_a: list[str], entities_b: list[str]) -> float:
    """Compute overlap ratio between two entity lists."""
    if not entities_a or not entities_b:
        return 0.0
    set_a = {e.strip().lower() for e in entities_a}
    set_b = {e.strip().lower() for e in entities_b}
    intersection = set_a & set_b
    union = set_a | set_b
    if not union:
        return 0.0
    return len(intersection) / len(union)


def _url_match(urls_a: list[str], urls_b: list[str]) -> bool:
    """Check if any URL from list A appears in list B."""
    set_a = {u.rstrip("/").lower() for u in urls_a if u}
    set_b = {u.rstrip("/").lower() for u in urls_b if u}
    return bool(set_a & set_b)


def _load_history(history_path: Path) -> list[dict]:
    """Load selected history from JSON file."""
    if not history_path.exists():
        return []
    try:
        with open(history_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Failed to load history from %s: %s", history_path, e)
        return []


def _save_history(history: list[dict], history_path: Path):
    """Save selected history to JSON file."""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _confirm_duplicate_via_ai(candidate: dict, history_entry: dict) -> bool:
    """
    Use DeepSeek to confirm whether a candidate is a duplicate of a history entry
    when entity overlap is high but not certain.
    """
    try:
        from ai_filter import call_deepseek, _extract_json
    except ImportError:
        # If ai_filter is not available, fall back to heuristic
        return _entity_overlap(
            candidate.get("key_entities", []),
            history_entry.get("key_entities", []),
        ) > 0.7

    prompt = f"""请判断以下两条新闻是否是同一事件的重复报道。

新闻A（新候选）：
- 标题：{candidate.get('title_cn', '')}
- 英文标题：{candidate.get('title_en', '')}
- 摘要：{candidate.get('summary_cn', '')}
- 关键实体：{json.dumps(candidate.get('key_entities', []), ensure_ascii=False)}

新闻B（历史记录）：
- 标题：{history_entry.get('title_cn', '')}
- 关键实体：{json.dumps(history_entry.get('key_entities', []), ensure_ascii=False)}
- 日期：{history_entry.get('date', '')}

请只输出JSON：{{"is_duplicate": true/false, "reason": "简要原因"}}"""

    try:
        response = call_deepseek(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=100,
        )
        result = _extract_json(response)
        return result.get("is_duplicate", False)
    except Exception as e:
        logger.warning("AI duplicate confirmation failed: %s", e)
        # Fall back to entity overlap
        return _entity_overlap(
            candidate.get("key_entities", []),
            history_entry.get("key_entities", []),
        ) > 0.7


def dedup_candidates(candidates: list[dict], history_path: str | Path) -> list[dict]:
    """
    Compare candidates against selected_history.json and remove duplicates.

    History format: [{{date, title_cn, key_entities, source_urls}}, ...]

    Matching logic:
    1. Exact URL match → duplicate
    2. Key entity overlap > 60% → likely duplicate (use DeepSeek to confirm)
    3. Title similarity > 80% → duplicate

    Args:
        candidates: List of candidate article dicts
        history_path: Path to selected_history.json

    Returns:
        Filtered candidates list (duplicates removed)
    """
    history_path = Path(history_path)
    history = _load_history(history_path)

    if not history:
        logger.info("No history found, all %d candidates pass", len(candidates))
        return candidates

    # Build URL index for fast lookup
    history_urls = set()
    for h in history:
        for url in h.get("source_urls", []):
            history_urls.add(url.rstrip("/").lower())

    filtered = []
    dup_count = 0

    for candidate in candidates:
        is_dup = False
        dup_reason = ""

        # Check 1: Exact URL match
        candidate_urls = [u.rstrip("/").lower() for u in candidate.get("source_urls", [])]
        for url in candidate_urls:
            if url in history_urls:
                is_dup = True
                dup_reason = f"URL match: {url}"
                break

        # Check 2: Title similarity > 80%
        if not is_dup:
            cand_title_cn = candidate.get("title_cn", "")
            cand_title_en = candidate.get("title_en", "")
            for h in history:
                hist_title = h.get("title_cn", "")
                sim_cn = _title_similarity(cand_title_cn, hist_title)
                sim_en = _title_similarity(cand_title_en, h.get("title_en", ""))
                if sim_cn > 0.8 or sim_en > 0.8:
                    is_dup = True
                    dup_reason = f"Title similarity: cn={sim_cn:.2f}, en={sim_en:.2f}"
                    break

        # Check 3: Key entity overlap > 60% → confirm with AI
        if not is_dup:
            cand_entities = candidate.get("key_entities", [])
            if cand_entities:
                for h in history:
                    overlap = _entity_overlap(cand_entities, h.get("key_entities", []))
                    if overlap > 0.6:
                        # Confirm with AI
                        if _confirm_duplicate_via_ai(candidate, h):
                            is_dup = True
                            dup_reason = f"Entity overlap {overlap:.2f} + AI confirmed"
                            break

        if is_dup:
            dup_count += 1
            logger.debug(
                "Duplicate: '%s' — %s",
                candidate.get("title_cn", candidate.get("title_en", "?"))[:50],
                dup_reason,
            )
        else:
            filtered.append(candidate)

    logger.info(
        "Dedup result: %d candidates → %d unique (%d duplicates removed)",
        len(candidates),
        len(filtered),
        dup_count,
    )
    return filtered


def update_history(
    candidates: list[dict],
    history_path: str | Path,
    date_str: str,
):
    """
    Add new candidates to the selected history file.

    Args:
        candidates: List of candidate dicts to add
        history_path: Path to selected_history.json
        date_str: Date string (YYYY-MM-DD) for the new entries
    """
    history_path = Path(history_path)
    history = _load_history(history_path)

    for c in candidates:
        entry = {
            "date": date_str,
            "title_cn": c.get("title_cn", ""),
            "title_en": c.get("title_en", ""),
            "key_entities": c.get("key_entities", []),
            "source_urls": c.get("source_urls", []),
            "source": c.get("source", ""),
            "source_id": c.get("source_id", ""),
        }
        history.append(entry)

    _save_history(history, history_path)
    logger.info("Updated history: added %d entries, total %d", len(candidates), len(history))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Deduplicate candidates against history")
    parser.add_argument("--candidates", type=str, required=True, help="Candidates JSON file")
    parser.add_argument("--history", type=str, default="data/selected_history.json", help="History JSON file")
    parser.add_argument("--output", type=str, default=None, help="Output file (default: overwrite candidates)")
    parser.add_argument("--date", type=str, default=None, help="Date string for history update")
    args = parser.parse_args()

    with open(args.candidates, "r", encoding="utf-8") as f:
        candidates = json.load(f)

    filtered = dedup_candidates(candidates, args.history)

    output_path = args.output or args.candidates
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)
    logger.info("Wrote %d deduplicated candidates to %s", len(filtered), output_path)

    if args.date:
        update_history(filtered, args.history, args.date)


if __name__ == "__main__":
    main()
