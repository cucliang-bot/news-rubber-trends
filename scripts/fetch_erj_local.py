#!/usr/bin/env python3
"""
ERJ 本地补抓脚本 — 只在电脑开着时运行。

逻辑：
1. 检查 data/ 目录下最近 N 天的文件夹
2. 找出哪些天缺少 ERJ 数据（candidates.json 中没有 source_id='erj' 的文章）
3. 如果缺失天数 <= 3：自动补齐
4. 如果缺失天数 > 3：询问用户要补几天

用法：
    python scripts/fetch_erj_local.py              # 自动模式（<=3天）
    python scripts/fetch_erj_local.py --days 5     # 手动指定补几天
    python scripts/fetch_erj_local.py --check      # 只检查不抓取
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from fetch_news import _parse_erj, SOURCES_PATH
from ai_filter import filter_and_summarize
from dedup import dedup_candidates, update_history
from fetch_image import fetch_article_image
from push_to_cloud import push_candidates, reset_confirm_queue

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _has_erj_data(date_dir: Path) -> bool:
    """检查某天是否已有 ERJ 数据"""
    candidates_file = date_dir / "candidates.json"
    if not candidates_file.exists():
        return False
    
    try:
        with open(candidates_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return any(c.get("source_id") == "erj" for c in data)
        elif isinstance(data, dict):
            candidates = data.get("candidates", data.get("data", []))
            return any(c.get("source_id") == "erj" for c in candidates)
    except Exception:
        pass
    return False


def _find_missing_days(data_dir: Path, lookback: int = 7) -> list[str]:
    """找出最近 lookback 天内缺少 ERJ 数据的日期"""
    missing = []
    today = datetime.now()
    
    for i in range(lookback):
        date = today - timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        date_dir = data_dir / date_str
        
        if not date_dir.exists():
            missing.append(date_str)
        elif not _has_erj_data(date_dir):
            missing.append(date_str)
    
    return missing


def _fetch_and_process_erj(date_str: str, days_back: int = 1):
    """抓取并处理指定日期的 ERJ 文章"""
    logger.info(f"=== 处理 {date_str} 的 ERJ 数据 ===")
    
    # 加载 sources.json 获取 ERJ 配置
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    erj_cfg = next((s for s in config["trends_sources"] if s["id"] == "erj"), None)
    if not erj_cfg:
        logger.error("ERJ 配置未找到")
        return
    
    # 抓取 ERJ
    raw_articles = _parse_erj(erj_cfg, days_back)
    if not raw_articles:
        logger.warning(f"{date_str} 未抓到 ERJ 文章")
        return
    
    # 过滤到目标日期
    raw_articles = [a for a in raw_articles if a.get("pub_date", "").startswith(date_str)]
    logger.info(f"抓到 {len(raw_articles)} 篇 ERJ 文章（{date_str}）")
    
    if not raw_articles:
        return
    
    # AI 筛选 + 摘要
    candidates = filter_and_summarize(raw_articles)
    logger.info(f"筛选后 {len(candidates)} 篇")
    
    # 去重
    history_path = PROJECT_DIR / "data" / "selected_history.json"
    candidates = dedup_candidates(candidates, history_path)
    logger.info(f"去重后 {len(candidates)} 篇")
    
    if not candidates:
        logger.info("无新候选")
        return
    
    # 分配 ID
    for i, c in enumerate(candidates, 1):
        c["id"] = i
    
    # 下载图片
    data_dir = PROJECT_DIR / "data" / date_str
    for c in candidates:
        urls = c.get("source_urls", [])
        if urls:
            try:
                image_path = fetch_article_image(urls[0], "erj", date_str)
                if image_path:
                    c["image_url"] = image_path
            except Exception as e:
                logger.warning(f"图片下载失败：{e}")
    
    # 保存 candidates.json
    candidates_file = data_dir / "candidates.json"
    existing = []
    if candidates_file.exists():
        try:
            with open(candidates_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = existing.get("candidates", existing.get("data", []))
        except Exception:
            existing = []
    
    # 合并（去重 URL）
    existing_urls = {u for c in existing for u in c.get("source_urls", [])}
    new_candidates = [c for c in candidates if not any(u in existing_urls for u in c.get("source_urls", []))]
    merged = existing + new_candidates
    
    # 重新编号
    for i, c in enumerate(merged, 1):
        c["id"] = i
    
    with open(candidates_file, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    logger.info(f"保存 {len(merged)} 篇候选到 {candidates_file}")
    
    # 推送到云端
    push_candidates(merged, date_str)
    reset_confirm_queue(merged, date_str)
    
    # 更新历史
    update_history(new_candidates, history_path, date_str)
    
    logger.info(f"=== {date_str} ERJ 处理完成 ===\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ERJ 本地补抓脚本")
    parser.add_argument("--days", type=int, default=None, help="手动指定补几天（默认自动）")
    parser.add_argument("--check", action="store_true", help="只检查不抓取")
    args = parser.parse_args()
    
    data_dir = PROJECT_DIR / "data"
    
    # 找出缺失天数
    missing = _find_missing_days(data_dir, lookback=7)
    
    if not missing:
        logger.info("✅ 最近 7 天 ERJ 数据完整，无需补抓")
        return
    
    logger.info(f"发现 {len(missing)} 天缺少 ERJ 数据：{', '.join(missing)}")
    
    if args.check:
        return
    
    # 决定补几天
    days_to_fix = args.days
    if days_to_fix is None:
        if len(missing) <= 3:
            days_to_fix = len(missing)
            logger.info(f"自动补齐 {days_to_fix} 天")
        else:
            # 交互式询问
            print(f"\n⚠️  发现 {len(missing)} 天缺少 ERJ 数据：{', '.join(missing)}")
            print("间隔较长，请确认要补几天：")
            print("  1. 补最近 3 天")
            print("  2. 补最近 5 天")
            print("  3. 全部补齐")
            print("  0. 跳过")
            
            choice = input("\n请选择 (0-3): ").strip()
            if choice == "1":
                days_to_fix = 3
            elif choice == "2":
                days_to_fix = 5
            elif choice == "3":
                days_to_fix = len(missing)
            else:
                logger.info("跳过补抓")
                return
    
    # 按日期从旧到新处理
    missing.sort()
    to_process = missing[:days_to_fix]
    
    logger.info(f"开始补抓 {len(to_process)} 天：{', '.join(to_process)}\n")
    
    for date_str in to_process:
        _fetch_and_process_erj(date_str, days_back=7)  # 多抓几天确保覆盖
    
    logger.info("✅ 全部补抓完成")


if __name__ == "__main__":
    main()
