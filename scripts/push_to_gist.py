#!/usr/bin/env python3
"""
推送候选和最终稿到 GitHub Gist（公开可读）
"""

import json
import os
import requests
from datetime import datetime

GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
GIST_ID = os.environ.get("GIST_ID", "")

GIST_API = "https://api.github.com/gists"


def push_to_gist(data: dict, filename: str):
    """推送数据到 Gist"""
    if not GIST_TOKEN or not GIST_ID:
        print("⚠️  GIST_TOKEN 或 GIST_ID 未配置，跳过推送")
        return False
    
    headers = {
        "Authorization": f"token {GIST_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    payload = {
        "files": {
            filename: {
                "content": json.dumps(data, ensure_ascii=False, indent=2)
            }
        }
    }
    
    try:
        resp = requests.patch(f"{GIST_API}/{GIST_ID}", headers=headers, json=payload)
        resp.raise_for_status()
        print(f"✅ 推送成功：{filename}")
        return True
    except Exception as e:
        print(f" 推送失败：{e}")
        return False


def push_candidates(candidates: list, date_str: str):
    """推送候选数据"""
    data = {
        "date": date_str,
        "updated_at": datetime.now().isoformat(),
        "count": len(candidates),
        "data": candidates
    }
    return push_to_gist(data, "candidates.json")


def push_final(articles: list, date_str: str):
    """推送最终稿"""
    data = {
        "date": date_str,
        "updated_at": datetime.now().isoformat(),
        "count": len(articles),
        "data": articles
    }
    return push_to_gist(data, "final.json")


if __name__ == "__main__":
    # 测试推送
    test_data = [{"test": True}]
    push_candidates(test_data, "2026-09-28")
