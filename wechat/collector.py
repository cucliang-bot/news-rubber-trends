#!/usr/bin/env python3
"""
微信公众号文章采集器 — 可插拔接口设计。

当前实现：通过腾讯新闻分发渠道（so.html5.qq.com）采集已同步的文章。
未来可替换为 RSS / 第三方API / Playwright 等方案。

设计原则：
  - 接口抽象，采集逻辑与业务逻辑分离
  - 去重基于文章URL
  - 支持增量采集（只拿新文章）
"""

import json
import os
import sys
import re
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from abc import ABC, abstractmethod

# ── 路径配置 ──────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR  = os.path.dirname(SCRIPT_DIR)
ACCOUNTS_PATH = os.path.join(SCRIPT_DIR, "accounts.json")
STORE_PATH    = os.path.join(SCRIPT_DIR, "articles_store.json")
DATA_DIR      = os.path.join(PROJECT_DIR, "data")

# ── 数据模型 ──────────────────────────────────────────────

class WechatArticle:
    """微信文章数据结构"""
    def __init__(self, title="", url="", pub_date="", author="", summary="",
                 content="", image_url="", account_name="", account_category="",
                 keywords=None):
        self.title = title
        self.url = url
        self.pub_date = pub_date              # "YYYY-MM-DD"
        self.author = author
        self.summary = summary
        self.content = content
        self.image_url = image_url
        self.account_name = account_name
        self.account_category = account_category
        self.keywords = keywords or []
        self.fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self):
        return {
            "title": self.title,
            "url": self.url,
            "pub_date": self.pub_date,
            "author": self.author,
            "summary": self.summary,
            "content": self.content,
            "image_url": self.image_url,
            "account_name": self.account_name,
            "account_category": self.account_category,
            "keywords": self.keywords,
            "fetched_at": self.fetched_at,
        }

    def to_candidate_dict(self, candidate_id: int):
        """转换为 candidates.json 兼容格式"""
        return {
            "id": candidate_id,
            "title_cn": self.title,
            "title_en": "",
            "source": f"微信公众号-{self.account_name}",
            "source_type": "wechat",
            "source_name": self.account_name,
            "source_urls": [self.url],
            "pub_date": self.pub_date,
            "summary_cn": self.summary or self._generate_summary(),
            "key_entities": self._extract_entities(),
            "image_url": self.image_url,
        }

    def _generate_summary(self):
        """从正文前200字生成摘要"""
        if not self.content:
            return ""
        clean = re.sub(r'<[^>]+>', '', self.content)
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean[:200] + ("..." if len(clean) > 200 else "")

    def _extract_entities(self):
        """从标题和关键词中提取关键实体"""
        entities = []
        text = self.title + " " + " ".join(self.keywords)
        # 提取中文词组（2-6字）
        for m in re.finditer(r'[\u4e00-\u9fff]{2,6}', text):
            w = m.group()
            if w not in entities and w not in ['文章来源', '主编', '本文编辑']:
                entities.append(w)
        return entities[:6]  # 最多6个


# ── 抽象采集器接口 ───────────────────────────────────────

class BaseWechatCollector(ABC):
    """采集器抽象接口 — 未来可替换实现"""

    @abstractmethod
    def get_latest_articles(self, account_config: dict, days_back: int = 3) -> List[Dict]:
        """
        获取公众号最新文章列表。
        返回: [{"title", "url", "pub_date", "summary", "image_url"}, ...]
        """
        pass

    @abstractmethod
    def get_article_content(self, article_url: str) -> Optional[str]:
        """
        获取单篇文章全文。
        返回: 文章HTML内容字符串，失败返回None
        """
        pass


# ── QQ新闻分发采集器 —————————————————————————

class QQNewsCollector(BaseWechatCollector):
    """
    通过腾讯新闻（so.html5.qq.com）采集微信公众号已同步的文章。

    原理：微信公众号文章会自动同步到腾讯新闻平台，
    搜索 signature + site 即可找到。这是当前最稳定可靠的方案。
    """

    def get_latest_articles(self, account_config: dict, days_back: int = 3) -> List[Dict]:
        """
        通过搜索签名获取文章列表。
        此方法依赖外部搜索（由AI agent调用WebSearch完成），
        本方法返回结构化的搜索结果占位。
        """
        return []  # 实际搜索由 AI agent 调用 WebSearch 完成

    def get_article_content(self, article_url: str) -> Optional[str]:
        """此方法由 AI agent 调用 WebFetch 完成"""
        return None

    @staticmethod
    def parse_qq_article_html(html: str) -> Optional[Dict]:
        """
        解析腾讯新闻文章页HTML，提取结构化信息。
        由 AI agent 调用 WebFetch 后传入解析。
        """
        result = {
            "title": "",
            "pub_date": "",
            "content": "",
            "image_url": "",
        }
        # 提取标题 (og:title)
        m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, re.I)
        if m:
            result["title"] = m.group(1)

        # 提取发布时间
        m = re.search(r'"pubtime"\s*:\s*"([^"]+)"', html)
        if not m:
            m = re.search(r'<span[^>]*class="[^"]*time[^"]*"[^>]*>([^<]+)</span>', html, re.I)
        if m:
            result["pub_date"] = m.group(1)[:10]

        # 提取正文
        # 腾讯新闻 body 常见 class: rich_media_content, article-content
        content_blocks = []
        for cls in ['rich_media_content', 'article-content', 'article_content', 'content-article']:
            pattern = rf'<[^>]+class="[^"]*{cls}[^"]*"[^>]*>(.*?)</(?:div|section|article)>'
            m = re.search(pattern, html, re.DOTALL | re.I)
            if m:
                content_blocks.append(m.group(1))

        if content_blocks:
            result["content"] = max(content_blocks, key=len)
        else:
            # fallback: 提取所有 <p> 标签
            ps = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
            result["content"] = "\n".join(ps) if ps else ""

        # 清理HTML标签
        result["content"] = re.sub(r'<[^>]+>', '', result["content"])
        result["content"] = re.sub(r'\n{3,}', '\n\n', result["content"]).strip()

        # 提取图片 (og:image)
        m = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html, re.I)
        if m:
            result["image_url"] = m.group(1)

        return result if result["title"] else None


# ── 去重与存储 ────────────────────────────────────────────

class ArticleStore:
    """文章存储与去重管理器"""

    def __init__(self, store_path=STORE_PATH):
        self.store_path = store_path
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.store_path):
            with open(self.store_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save(self):
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def is_duplicate(self, account_name: str, article_url: str) -> bool:
        """检查文章是否已存在"""
        if account_name not in self.data:
            return False
        urls = {a.get("url", "") for a in self.data[account_name].get("articles", [])}
        return article_url in urls

    def add_article(self, account_name: str, article: WechatArticle):
        """添加文章到存储"""
        if account_name not in self.data:
            self.data[account_name] = {"last_check": None, "articles": []}
        self.data[account_name]["articles"].append({
            "title": article.title,
            "url": article.url,
            "pub_date": article.pub_date,
            "fetched_at": article.fetched_at,
        })
        self.data[account_name]["last_check"] = article.fetched_at
        self._save()

    def update_check_time(self, account_name: str):
        """更新最后检查时间"""
        if account_name not in self.data:
            self.data[account_name] = {"last_check": None, "articles": []}
        self.data[account_name]["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save()

    def get_new_count_today(self, account_name: str) -> int:
        """获取今日抓取的新文章数"""
        if account_name not in self.data:
            return 0
        today = datetime.now().strftime("%Y-%m-%d")
        count = 0
        for a in self.data[account_name].get("articles", []):
            if a.get("fetched_at", "").startswith(today):
                count += 1
        return count


# ── 账户管理 ──────────────────────────────────────────────

class AccountManager:
    """公众号账户管理器"""

    def __init__(self, accounts_path=ACCOUNTS_PATH):
        self.accounts_path = accounts_path
        self.accounts = self._load()

    def _load(self) -> List[Dict]:
        if os.path.exists(self.accounts_path):
            with open(self.accounts_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _save(self):
        os.makedirs(os.path.dirname(self.accounts_path), exist_ok=True)
        with open(self.accounts_path, "w", encoding="utf-8") as f:
            json.dump(self.accounts, f, ensure_ascii=False, indent=2)

    def get_enabled(self) -> List[Dict]:
        """获取所有启用的账户"""
        return [a for a in self.accounts if a.get("enabled", False)]

    def update_last_check(self, name: str):
        """更新账户最后检查时间"""
        for a in self.accounts:
            if a["name"] == name:
                a["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break
        self._save()

    def update_last_fetch(self, name: str):
        """更新账户最后抓取时间"""
        for a in self.accounts:
            if a["name"] == name:
                a["last_fetch"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break
        self._save()


# ── 命令行入口 ────────────────────────────────────────────

def main():
    """命令行接口 — 列出账户状态"""
    mgr = AccountManager()
    store = ArticleStore()

    print("=" * 60)
    print("微信公众号监控模块 — 状态概览")
    print("=" * 60)

    enabled = mgr.get_enabled()
    if not enabled:
        print("⚠️  没有启用的公众号。请在 wechat/accounts.json 中配置。")
        return

    for acc in enabled:
        name = acc["name"]
        total = len(store.data.get(name, {}).get("articles", []))
        today = store.get_new_count_today(name)
        last_check = acc.get("last_check", "从未")
        print(f"\n📱 {name} ({acc.get('category', '')})")
        print(f"   累计文章: {total} 篇 | 今日新增: {today} 篇")
        print(f"   类型: {acc.get('collector_type', 'qq_news_syndication')}")
        print(f"   最后检查: {last_check}")
        print(f"   签名: {acc.get('search_signature', 'N/A')}")

    print(f"\n{'=' * 60}")
    print(f"配置文件: {ACCOUNTS_PATH}")
    print(f"去重存储: {STORE_PATH}")


if __name__ == "__main__":
    main()
