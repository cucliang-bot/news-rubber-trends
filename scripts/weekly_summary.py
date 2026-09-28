#!/usr/bin/env python3
"""
Weekly summary pipeline.

Collects all candidates from the week's daily folders, merges and deduplicates
them, and generates a weekly summary for review.

Pipeline:
1. Collect all candidates from the week's daily folders (data/YYYY-MM-DD/)
2. Merge and dedup across the week
3. Generate weekly_candidates.json + weekly_candidates.html
4. Push to cloud

Usage:
    python scripts/weekly_summary.py [--week 2026-W39]
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from dedup import dedup_candidates, _load_history, _title_similarity, _entity_overlap
from push_to_cloud import push_candidates, _load_config, push_to_gist

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_iso_week(week_str: str) -> tuple[datetime, datetime]:
    """
    Parse an ISO week string like '2026-W39' into (start_date, end_date).

    Returns Monday 00:00 to Sunday 23:59 of that week.
    """
    m = re.match(r"(\d{4})-W(\d{2})", week_str)
    if not m:
        raise ValueError(f"Invalid week format: {week_str}. Expected YYYY-Www (e.g., 2026-W39)")
    year = int(m.group(1))
    week = int(m.group(2))
    # Monday of the given ISO week
    monday = datetime.strptime(f"{year}-W{week:02d}-1", "%Y-W%W-%w")
    # Correct ISO week calculation
    monday = datetime.fromisocalendar(year, week, 1)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _collect_daily_candidates(data_dir: Path, start_date: datetime, end_date: datetime) -> list[dict]:
    """
    Collect all candidates from daily folders within the date range.

    Looks for data/YYYY-MM-DD/candidates.json files.
    """
    all_candidates = []
    current = start_date

    while current <= end_date:
        date_str = current.strftime("%Y-%m-%d")
        day_dir = data_dir / date_str
        candidates_file = day_dir / "candidates.json"

        if candidates_file.exists():
            try:
                with open(candidates_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    day_candidates = data
                elif isinstance(data, dict):
                    day_candidates = data.get("candidates", [])
                else:
                    day_candidates = []

                # Tag each candidate with its date
                for c in day_candidates:
                    c["week_date"] = date_str

                all_candidates.extend(day_candidates)
                logger.info("Collected %d candidates from %s", len(day_candidates), date_str)
            except Exception as e:
                logger.warning("Failed to load candidates from %s: %s", candidates_file, e)

        current += timedelta(days=1)

    return all_candidates


def _dedup_weekly(candidates: list[dict]) -> list[dict]:
    """
    Deduplicate candidates within the weekly set.

    Uses title similarity and entity overlap to find duplicates across days.
    """
    if len(candidates) <= 1:
        return candidates

    # Group by rough similarity
    kept = []
    for candidate in candidates:
        is_dup = False
        cand_title = candidate.get("title_cn", "") or candidate.get("title_en", "")
        cand_entities = candidate.get("key_entities", [])

        for existing in kept:
            exist_title = existing.get("title_cn", "") or existing.get("title_en", "")
            # Title similarity check
            sim = _title_similarity(cand_title, exist_title)
            if sim > 0.8:
                is_dup = True
                # Merge source_urls
                existing_urls = set(existing.get("source_urls", []))
                new_urls = set(candidate.get("source_urls", []))
                existing["source_urls"] = list(existing_urls | new_urls)
                # Merge entities
                exist_ent = set(existing.get("key_entities", []))
                new_ent = set(cand_entities)
                existing["key_entities"] = list(exist_ent | new_ent)
                break

            # Entity overlap check
            if cand_entities:
                overlap = _entity_overlap(cand_entities, existing.get("key_entities", []))
                if overlap > 0.7:
                    is_dup = True
                    existing_urls = set(existing.get("source_urls", []))
                    new_urls = set(candidate.get("source_urls", []))
                    existing["source_urls"] = list(existing_urls | new_urls)
                    break

        if not is_dup:
            kept.append(candidate)

    logger.info("Weekly dedup: %d → %d candidates", len(candidates), len(kept))
    return kept


def _generate_weekly_html(candidates: list[dict], week_str: str, start_date: datetime, end_date: datetime, output_path: Path):
    """Generate an HTML summary page for the week's candidates."""
    # Group by source
    by_source = {}
    for c in candidates:
        src = c.get("source", "Unknown")
        by_source.setdefault(src, []).append(c)

    source_sections = []
    for source, items in sorted(by_source.items()):
        rows = []
        for c in items:
            urls = c.get("source_urls", [])
            url_html = " ".join(f'<a href="{u}" target="_blank">链接</a>' for u in urls[:3])
            rows.append(f"""
            <tr>
                <td>{c.get('week_date', '')}</td>
                <td><strong>{c.get('title_cn', '')}</strong></td>
                <td>{c.get('title_en', '')}</td>
                <td>{c.get('summary_cn', '')}</td>
                <td>{url_html}</td>
            </tr>""")

        source_sections.append(f"""
        <h3>{source} ({len(items)}篇)</h3>
        <table>
            <thead>
                <tr><th>日期</th><th>中文标题</th><th>英文标题</th><th>摘要</th><th>链接</th></tr>
            </thead>
            <tbody>{''.join(rows)}</tbody>
        </table>""")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>橡胶行业新闻周报 - {week_str}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1 {{ color: #333; }}
        h3 {{ color: #4472C4; margin-top: 25px; }}
        table {{ border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.12); margin-bottom: 20px; }}
        th {{ background: #4472C4; color: white; padding: 8px; text-align: left; font-size: 13px; }}
        td {{ padding: 8px; border-bottom: 1px solid #eee; font-size: 13px; vertical-align: top; }}
        tr:hover {{ background: #f0f4ff; }}
        a {{ color: #4472C4; }}
        .meta {{ color: #666; font-size: 14px; margin-bottom: 15px; }}
        .summary {{ background: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.12); }}
    </style>
</head>
<body>
    <h1>橡胶行业新闻周报 - {week_str}</h1>
    <p class="meta">
        周期: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')} |
        共 {len(candidates)} 条新闻 |
        生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}
    </p>
    <div class="summary">
        <strong>来源分布：</strong>
        {', '.join(f'{src} ({len(items)}篇)' for src, items in sorted(by_source.items()))}
    </div>
    {''.join(source_sections)}
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("Generated weekly HTML: %s", output_path)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Weekly news summary pipeline")
    parser.add_argument("--week", type=str, default=None, help="ISO week (e.g., 2026-W39). Defaults to current week.")
    parser.add_argument("--skip-push", action="store_true", help="Skip pushing to cloud")
    args = parser.parse_args()

    # Determine week range
    if args.week:
        week_str = args.week
        start_date, end_date = _parse_iso_week(week_str)
    else:
        today = datetime.now()
        iso_cal = today.isocalendar()
        week_str = f"{iso_cal[0]}-W{iso_cal[1]:02d}"
        start_date, end_date = _parse_iso_week(week_str)
        # Don't include future days
        if end_date > today:
            end_date = today

    logger.info("=== Weekly Summary: %s (%s ~ %s) ===", week_str, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))

    data_dir = PROJECT_DIR / "data"

    # Step 1: Collect daily candidates
    logger.info("Step 1: Collecting daily candidates...")
    all_candidates = _collect_daily_candidates(data_dir, start_date, end_date)
    if not all_candidates:
        logger.info("No candidates found for this week. Nothing to do.")
        return
    logger.info("Collected %d total candidates from the week", len(all_candidates))

    # Step 2: Merge and dedup
    logger.info("Step 2: Deduplicating across the week...")
    weekly_candidates = _dedup_weekly(all_candidates)

    # Assign IDs
    for i, c in enumerate(weekly_candidates, 1):
        c["id"] = i

    # Step 3: Generate output files
    logger.info("Step 3: Generating weekly output files...")
    week_dir = data_dir / week_str
    week_dir.mkdir(parents=True, exist_ok=True)

    # weekly_candidates.json
    weekly_json_path = week_dir / "weekly_candidates.json"
    with open(weekly_json_path, "w", encoding="utf-8") as f:
        json.dump(weekly_candidates, f, ensure_ascii=False, indent=2)

    # weekly_candidates.html
    weekly_html_path = week_dir / "weekly_candidates.html"
    _generate_weekly_html(weekly_candidates, week_str, start_date, end_date, weekly_html_path)

    logger.info("Generated weekly summary: %d candidates", len(weekly_candidates))

    # Step 4: Push to cloud
    if not args.skip_push:
        logger.info("Step 4: Pushing to cloud...")
        config = _load_config()
        push_candidates(weekly_candidates, week_str, config)
        push_to_gist(weekly_candidates, config=config)
    else:
        logger.info("Step 4: Skipping cloud push")

    logger.info("=== Weekly Summary Complete: %d candidates ===", len(weekly_candidates))


if __name__ == "__main__":
    main()
