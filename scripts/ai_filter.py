#!/usr/bin/env python3
"""
DeepSeek API wrapper for article filtering, summarization, and rewriting.

Provides:
- call_deepseek(): Low-level API call
- filter_and_summarize(): Filter relevant articles + generate Chinese summaries
- merge_multi_source(): Merge duplicate stories from different sources
- rewrite_article(): Rewrite an English article in Chinese per translation guide

Usage:
    python scripts/ai_filter.py --input raw_articles.json --output filtered.json
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
TRANSLATION_GUIDE_PATH = PROJECT_DIR / "config" / "translation_guide.md"


def call_deepseek(
    messages: list[dict],
    model: str = "deepseek-chat",
    temperature: float = 0.3,
    max_tokens: int = 4000,
) -> str:
    """Call DeepSeek chat completion API and return the assistant's response text."""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY environment variable is not set")

    resp = requests.post(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _load_translation_guide() -> str:
    """Load the translation guide markdown file."""
    if TRANSLATION_GUIDE_PATH.exists():
        return TRANSLATION_GUIDE_PATH.read_text(encoding="utf-8")
    return ""


def _extract_json(text: str) -> list | dict:
    """Extract JSON from LLM response, handling markdown code blocks."""
    # Try to find JSON in code blocks
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    if m:
        text = m.group(1)
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find array or object
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"Could not extract JSON from response: {text[:200]}...")


def filter_and_summarize(
    raw_articles: list[dict],
    translation_guide: str = "",
) -> list[dict]:
    """
    Send raw articles to DeepSeek for relevance filtering and Chinese summarization.

    For each batch of articles, DeepSeek will:
    1. Filter out non-rubber/tire industry articles
    2. Generate a Chinese title (title_cn)
    3. Keep the English title (title_en) for English sources
    4. Write a 2-3 sentence Chinese summary (summary_cn)
    5. Extract key entities (company names, products, numbers)

    Args:
        raw_articles: List of {title, url, source, source_id, pub_date}
        translation_guide: Optional translation guide text for context

    Returns:
        List of {title_cn, title_en, source, source_id, source_urls, pub_date, summary_cn, key_entities}
    """
    if not raw_articles:
        return []

    if not translation_guide:
        translation_guide = _load_translation_guide()

    batch_size = 12
    all_results = []

    for i in range(0, len(raw_articles), batch_size):
        batch = raw_articles[i : i + batch_size]
        logger.info(
            "Filtering batch %d-%d of %d articles",
            i + 1,
            min(i + batch_size, len(raw_articles)),
            len(raw_articles),
        )

        articles_json = json.dumps(
            [
                {
                    "idx": j,
                    "title": a["title"],
                    "url": a["url"],
                    "source": a["source"],
                    "source_id": a["source_id"],
                    "pub_date": a.get("pub_date", ""),
                }
                for j, a in enumerate(batch)
            ],
            ensure_ascii=False,
            indent=2,
        )

        prompt = f"""你是一个橡胶轮胎行业新闻编辑助手。请对以下新闻文章列表进行处理：

## 任务
1. **筛选**：只保留与橡胶、轮胎、炭黑、合成橡胶、天然橡胶、轮胎翻新、橡胶制品行业直接相关的文章。过滤掉不相关的文章。
2. **中文标题**：为每篇保留的文章生成准确的中文标题（title_cn）。
3. **英文标题**：保留原始英文标题（title_en），中文来源的英文标题留空。
4. **中文摘要**：用2-3句中文概括文章核心内容（summary_cn）。
5. **关键实体**：提取关键实体（公司名称、产品名称、关键数字等），作为key_entities数组。

## 翻译规范
{translation_guide[:1500] if translation_guide else "无特殊规范"}

## 输入文章
{articles_json}

## 输出要求
请输出JSON数组，每个元素格式如下：
```json
{{
  "idx": 原始序号,
  "title_cn": "中文标题",
  "title_en": "英文原标题（中文来源留空）",
  "source": "来源名称",
  "source_id": "来源ID",
  "source_urls": ["原始URL"],
  "pub_date": "发布日期",
  "summary_cn": "2-3句中文摘要",
  "key_entities": ["实体1", "实体2"]
}}
```

只输出JSON，不要输出其他内容。"""

        try:
            response = call_deepseek(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=4000,
            )
            parsed = _extract_json(response)
            if isinstance(parsed, dict):
                parsed = [parsed]

            for item in parsed:
                idx = item.get("idx", 0)
                if 0 <= idx < len(batch):
                    original = batch[idx]
                    item.setdefault("source", original["source"])
                    item.setdefault("source_id", original["source_id"])
                    item.setdefault("source_urls", [original["url"]])
                    item.setdefault("pub_date", original.get("pub_date", ""))
                    all_results.append(item)

        except Exception as e:
            logger.error("DeepSeek filtering failed for batch starting at %d: %s", i, e)
            # On failure, include all articles from batch with minimal processing
            for a in batch:
                all_results.append({
                    "title_cn": a["title"],
                    "title_en": a["title"] if a.get("source_id") != "tanhei" and a.get("source_id") != "tireworld" else "",
                    "source": a["source"],
                    "source_id": a["source_id"],
                    "source_urls": [a["url"]],
                    "pub_date": a.get("pub_date", ""),
                    "summary_cn": "",
                    "key_entities": [],
                })

        # Rate limit: small delay between batches
        if i + batch_size < len(raw_articles):
            time.sleep(1)

    logger.info("Filtered and summarized: %d articles (from %d raw)", len(all_results), len(raw_articles))
    return all_results


def merge_multi_source(articles: list[dict]) -> list[dict]:
    """
    Merge articles from multiple sources that cover the same story.

    Uses DeepSeek to identify duplicates by content similarity.
    Merges their source_urls and keeps the best summary.

    Args:
        articles: List of filtered article dicts

    Returns:
        Merged list with combined source_urls
    """
    if len(articles) <= 1:
        return articles

    articles_info = json.dumps(
        [
            {
                "idx": i,
                "title_cn": a.get("title_cn", ""),
                "title_en": a.get("title_en", ""),
                "source": a.get("source", ""),
                "source_urls": a.get("source_urls", []),
                "summary_cn": a.get("summary_cn", ""),
                "key_entities": a.get("key_entities", []),
            }
            for i, a in enumerate(articles)
        ],
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""你是一个新闻编辑。以下文章列表中可能包含报道同一事件的不同来源文章。
请识别哪些文章是同一新闻事件的不同报道（即重复报道），并将它们合并。

## 判断标准
- 中文标题含义相同或高度相似
- 关键实体重叠度高（>60%）
- 摘要描述的是同一事件

## 输入
{articles_info}

## 输出要求
输出JSON数组，每个元素代表合并后的一条新闻：
```json
{{
  "merged_indices": [0, 3],
  "title_cn": "选择最好的中文标题",
  "title_en": "保留英文标题",
  "source": "主要来源名称",
  "source_id": "主要来源ID",
  "source_urls": ["合并所有URL"],
  "pub_date": "最早的日期",
  "summary_cn": "合并后最完整的摘要",
  "key_entities": ["合并所有实体，去重"]
}}
```

只输出JSON，不要输出其他内容。"""

    try:
        response = call_deepseek(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4000,
        )
        parsed = _extract_json(response)
        if isinstance(parsed, dict):
            parsed = [parsed]

        merged = []
        covered_indices = set()
        for item in parsed:
            indices = item.get("merged_indices", [])
            for idx in indices:
                covered_indices.add(idx)
            # Ensure source_urls is a list
            if isinstance(item.get("source_urls"), str):
                item["source_urls"] = [item["source_urls"]]
            merged.append(item)

        # Add any articles that weren't included in merge groups
        for i, a in enumerate(articles):
            if i not in covered_indices:
                merged.append(a)

        logger.info(
            "Merged %d articles into %d unique stories",
            len(articles),
            len(merged),
        )
        return merged

    except Exception as e:
        logger.error("DeepSeek merge failed: %s", e)
        return articles


def rewrite_article(
    article_title: str,
    article_text: str,
    source_name: str,
    source_url: str,
    translation_guide: str = "",
) -> str:
    """
    Rewrite an article in Chinese following the translation guide.

    Args:
        article_title: Original article title
        article_text: Original article full text (plain text)
        source_name: Source publication name
        source_url: Original article URL
        translation_guide: Translation guide content

    Returns:
        Chinese rewritten article text
    """
    if not translation_guide:
        translation_guide = _load_translation_guide()

    # Truncate article text to avoid token limits
    max_chars = 8000
    if len(article_text) > max_chars:
        article_text = article_text[:max_chars] + "\n[...内容截断...]"

    prompt = f"""你是一位专业的橡胶轮胎行业新闻翻译编辑。请将以下英文新闻翻译/改写为中文新闻稿。

## 翻译规范
{translation_guide}

## 输出格式要求
1. **标题**：准确、简洁的中文新闻标题
2. **来源行**：注明新闻来源（如"据{source_name}报道"）
3. **摘要**：2-3句话概括核心内容
4. **正文**：完整的中文新闻稿，结构清晰

## 注意事项
- 保持所有数据、数字精确不变
- 机构名称首次出现时用"中文名（外文原名）"格式
- 语言自然流畅，符合中文新闻写作习惯
- 不添加原文没有的评论或分析

## 原文信息
- 标题：{article_title}
- 来源：{source_name}
- 原文链接：{source_url}

## 原文内容
{article_text}

## 输出
请直接输出翻译后的完整中文新闻稿（包含标题、来源行、正文），不要输出其他内容。"""

    try:
        response = call_deepseek(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=4000,
        )
        return response.strip()
    except Exception as e:
        logger.error("DeepSeek rewrite failed for '%s': %s", article_title, e)
        return f"[翻译失败] {article_title}\n来源：{source_name}\n原文链接：{source_url}"


def check_similarity(text1: str, text2: str) -> float:
    """
    Use DeepSeek to estimate similarity between two texts.

    Returns a float between 0.0 and 1.0.
    """
    prompt = f"""请评估以下两段文本的内容相似度（0到1之间的数值）。
只考虑核心事实和关键信息，忽略措辞差异。

文本A：
{text1[:2000]}

文本B：
{text2[:2000]}

请只输出一个数字（0到1之间的小数），不要输出其他内容。"""

    try:
        response = call_deepseek(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=20,
        )
        # Extract number from response
        m = re.search(r"(\d+\.?\d*)", response.strip())
        if m:
            val = float(m.group(1))
            if val > 1:
                val = val / 100.0
            return min(1.0, max(0.0, val))
        return 0.5
    except Exception as e:
        logger.error("Similarity check failed: %s", e)
        return 0.5


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI filter and summarize articles")
    parser.add_argument("--input", type=str, required=True, help="Input JSON file with raw articles")
    parser.add_argument("--output", type=str, required=True, help="Output JSON file for filtered articles")
    parser.add_argument("--merge", action="store_true", help="Also merge multi-source duplicates")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        raw_articles = json.load(f)

    logger.info("Loaded %d raw articles", len(raw_articles))

    filtered = filter_and_summarize(raw_articles)

    if args.merge:
        filtered = merge_multi_source(filtered)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    logger.info("Wrote %d filtered articles to %s", len(filtered), out_path)


if __name__ == "__main__":
    main()
