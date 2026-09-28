#!/usr/bin/env python3
"""
微信文章 → candidates.json 集成脚本。

读取 wechat/articles_store.json 中指定日期范围的文章，
转换为 candidates.json 兼容格式，合并到当日的 candidates.json。

用法：
  python wechat_to_candidates.py --date 2026-08-11
  python wechat_to_candidates.py --date 2026-08-11 --days-back 3
  python wechat_to_candidates.py --list          # 列出所有已存储文章
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
STORE_PATH  = os.path.join(SCRIPT_DIR, "articles_store.json")
ACCOUNTS_PATH = os.path.join(SCRIPT_DIR, "accounts.json")
DATA_DIR    = os.path.join(PROJECT_DIR, "data")


def load_store():
    if os.path.exists(STORE_PATH):
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_accounts():
    if os.path.exists(ACCOUNTS_PATH):
        with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
            return {a["name"]: a for a in json.load(f)}
    return {}


def get_articles_in_range(days_back=3):
    """
    获取指定天数范围内的所有微信文章。
    返回: [WechatArticle dict, ...]
    """
    store = load_store()
    accounts = load_accounts()
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    articles = []

    for account_name, acc_data in store.items():
        account_info = accounts.get(account_name, {})
        for a in acc_data.get("articles", []):
            pub_date = a.get("pub_date", "")
            if pub_date >= cutoff:
                articles.append({
                    "title": a.get("title", ""),
                    "url": a.get("url", ""),
                    "pub_date": pub_date,
                    "account_name": account_name,
                    "account_category": account_info.get("category", ""),
                    "keywords": account_info.get("keywords", []),
                })

    # 按 pub_date 倒序
    articles.sort(key=lambda x: x["pub_date"], reverse=True)
    return articles


def load_existing_candidates(date_str):
    """读取当日已有的 candidates.json"""
    path = os.path.join(DATA_DIR, date_str, "candidates.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_candidates(date_str, candidates):
    """保存 candidates.json"""
    date_dir = os.path.join(DATA_DIR, date_str)
    os.makedirs(date_dir, exist_ok=True)
    path = os.path.join(date_dir, "candidates.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
    print(f"已保存: {path} ({len(candidates)} 条)")


def article_to_candidate(article, candidate_id):
    """将微信文章转为 candidates 格式"""
    title = article["title"]
    keywords = article.get("keywords", [])

    # 提取关键实体
    entities = []
    for kw in keywords:
        if kw not in entities:
            entities.append(kw)
    # 从标题提取补充
    import re
    for m in re.finditer(r'[\u4e00-\u9fff]{2,6}', title):
        w = m.group()
        if w not in entities[:6]:
            entities.append(w)
    entities = entities[:6]

    return {
        "id": candidate_id,
        "title_cn": title,
        "title_en": "",
        "source": f"微信公众号-{article['account_name']}",
        "source_type": "wechat",
        "source_name": article["account_name"],
        "source_urls": [article["url"]],
        "pub_date": article["pub_date"],
        "summary_cn": article.get("summary_cn", ""),
        "key_entities": entities,
        "image_url": article.get("image_url", ""),
    }


def merge_candidates(existing, wechat_articles):
    """
    将微信文章按 pub_date 倒序合并到现有候选列表。
    去重：URL 已存在则跳过。
    """
    existing_urls = set()
    for c in existing:
        for u in c.get("source_urls", []):
            existing_urls.add(u)

    # 过滤掉已存在的
    new_articles = [a for a in wechat_articles if a["url"] not in existing_urls]

    if not new_articles:
        print("没有新的微信文章需要合并。")
        return existing, 0

    # 转换为候选格式
    start_id = max([c.get("id", 0) for c in existing], default=0) + 1
    new_candidates = []
    for i, a in enumerate(new_articles):
        new_candidates.append(article_to_candidate(a, start_id + i))

    # 合并并按 pub_date 倒序排列
    merged = existing + new_candidates
    merged.sort(key=lambda x: (x.get("pub_date", ""), x.get("source", "")), reverse=True)

    # 重新编号
    for i, c in enumerate(merged):
        c["id"] = i + 1

    return merged, len(new_candidates)


def main():
    parser = argparse.ArgumentParser(description="微信文章 → candidates.json 集成")
    parser.add_argument("--date", help="目标日期 YYYY-MM-DD")
    parser.add_argument("--list", action="store_true", help="列出所有已存储文章")
    args = parser.parse_args()

    if args.list:
        store = load_store()
        if not store:
            print("暂无已存储的文章。")
            return
        for name, acc_data in store.items():
            print(f"\n📱 {name}")
            for a in acc_data.get("articles", []):
                print(f"  [{a.get('pub_date', '?')}] {a.get('title', '')}")
        return

    if not args.date:
        print("错误：需要 --date YYYY-MM-DD")
        sys.exit(1)

    date_str = args.date

    # 获取微信文章
    wechat_articles = get_articles_in_range(days_back=4)  # 覆盖周末
    print(f"微信文章库中近4天文章: {len(wechat_articles)} 篇")

    if not wechat_articles:
        print("没有符合时间范围的文章。")
        return

    # 读取现有 candidates
    existing = load_existing_candidates(date_str)
    print(f"当日已有候选: {len(existing)} 条")

    # 合并
    merged, new_count = merge_candidates(existing, wechat_articles)
    print(f"新增微信文章: {new_count} 篇")
    print(f"合并后总计: {len(merged)} 条")

    # 保存
    save_candidates(date_str, merged)

    # 列出新增文章
    if new_count > 0:
        print("\n新增微信文章:")
        for a in wechat_articles[-new_count:]:
            print(f"  [{a['pub_date']}] {a['title']} ({a['account_name']})")


if __name__ == "__main__":
    main()
