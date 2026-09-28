#!/usr/bin/env python3
"""
微信公众号文章采集器 — Playwright 实现 v2
反爬优化：自然浏览器指纹 + 慢速操作 + 搜狗 approve 流程处理
"""

import json
import re
import time
import random
import datetime
from pathlib import Path
from urllib.parse import quote, urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

BASE_DIR = Path(__file__).resolve().parent
STORE_FILE = BASE_DIR / "articles_store.json"
ACCOUNTS_FILE = BASE_DIR / "accounts.json"
CHROME_PATH = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


def rdelay(min_ms=800, max_ms=2000):
    """随机延迟，模拟人类操作节奏"""
    time.sleep(random.uniform(min_ms, max_ms) / 1000)


def load_store():
    if STORE_FILE.exists():
        with open(STORE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_store(data):
    with open(STORE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_article(page):
    """从微信文章页面提取完整元数据和正文"""
    result = {'title': '', 'pub_date': '', 'account_name': '',
              '__biz': '', 'content': '', 'summary': '', 'images': []}

    try:
        page.wait_for_selector('#js_content', timeout=20000)
        rdelay(1500, 3000)

        # 提取 JS 变量
        js = page.evaluate("""() => {
            const v = {};
            for (const k of ['msg_title','nickname','biz','ct','msg_desc']) {
                try { v[k] = window[k]; } catch(e) {}
            }
            return v;
        }""")

        result['title'] = js.get('msg_title', '') or ''
        result['account_name'] = js.get('nickname', '') or ''
        result['__biz'] = js.get('biz', '') or ''

        ct = js.get('ct', '')
        if ct:
            result['pub_date'] = datetime.datetime.fromtimestamp(int(ct)).strftime('%Y-%m-%d')

        # 摘要
        result['summary'] = js.get('msg_desc', '') or ''
        if not result['summary']:
            meta = page.query_selector('meta[name="description"]')
            if meta:
                result['summary'] = meta.get_attribute('content') or ''

        # 正文
        content_el = page.query_selector('#js_content')
        if content_el:
            result['content'] = content_el.inner_text().strip()[:5000]  # 截取前5000字

        # 图片
        imgs = page.query_selector_all('#js_content img[data-src]')
        for img in imgs[:5]:
            src = img.get_attribute('data-src') or ''
            if src and 'mmbiz' in src:
                result['images'].append(src)

        # 标题后备
        if not result['title']:
            t = page.query_selector('#activity-name')
            if t:
                result['title'] = t.inner_text().strip()

        return result
    except PlaywrightTimeout:
        return result
    except Exception as e:
        print(f"    extract error: {e}")
        return result


def search_and_collect(account_name, query, max_articles=15):
    """搜狗搜索 → 微信文章 → 提取内容"""
    store = load_store()
    acct = store.get(account_name, {'articles': [], 'last_check': None})
    existing_urls = {a['url'] for a in acct['articles']}
    new_articles = []

    print(f"🔍 {account_name} | 已入库 {len(acct['articles'])} 篇 | 搜索词: {query}")

    with sync_playwright() as p:
        # 自然浏览器指纹
        browser = p.chromium.launch(
            executable_path=CHROME_PATH,
            headless=True,
            args=[
                '--no-sandbox', '--disable-gpu', '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage', '--window-size=1440,900',
            ]
        )

        context = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            viewport={'width': 1440, 'height': 900},
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
        )

        # 屏蔽 webdriver 检测
        page = context.new_page()
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        """)

        try:
            # Step 1: 先访问搜狗首页建立 cookie
            print("  → 访问搜狗首页...")
            page.goto('https://weixin.sogou.com/', timeout=20000, wait_until='domcontentloaded')
            rdelay(2000, 3500)

            # Step 2: 搜索
            encoded_q = quote(query)
            search_url = f'https://weixin.sogou.com/weixin?type=2&query={encoded_q}&ie=utf8'
            print(f"  → 搜索...")
            page.goto(search_url, timeout=20000, wait_until='domcontentloaded')
            rdelay(2000, 4000)

            # 检查反爬
            page_text = page.content()
            if 'antispider' in page_url(page) or '验证' in page_text[:800]:
                print("  ⚠️ 反爬！等待重试...")
                rdelay(8000, 12000)
                page.goto(search_url, timeout=20000, wait_until='domcontentloaded')
                rdelay(3000, 5000)

            # Step 3: 提取文章链接
            links = page.query_selector_all('h3 a')
            print(f"  找到 {len(links)} 个链接")

            article_pairs = []  # (sogou_link, title_text)
            for el in links[:max_articles]:
                href = el.get_attribute('href') or ''
                if '/link?' in href or '/weixin?' in href:
                    full = urljoin('https://weixin.sogou.com', href)
                    title = el.inner_text().strip()
                    article_pairs.append((full, title))

            print(f"  候选文章: {len(article_pairs)} 篇")

            # Step 4: 逐篇访问
            processed = 0
            for i, (link, sogou_title) in enumerate(article_pairs):
                if processed >= max_articles:
                    break

                print(f"\n  [{i+1}/{len(article_pairs)}] {sogou_title[:40]}...")
                wechat_url = None
                try:
                    page.goto(link, timeout=15000, wait_until='domcontentloaded')
                    rdelay(2000, 3500)

                    # Playwright 已经自动跟到微信原文页面
                    current_url = page_url(page)

                    if 'mp.weixin.qq.com' in current_url:
                        # 已到微信文章页面，直接使用
                        wechat_url = current_url
                    else:
                        # 搜狗中间页，尝试提取
                        try:
                            page.wait_for_load_state('networkidle', timeout=5000)
                        except:
                            pass
                        rdelay(1000, 2000)

                        # 方法1: 看页面是否自动跳转了
                        current_url = page_url(page)
                        if 'mp.weixin.qq.com' in current_url:
                            wechat_url = current_url
                        else:
                            # 方法2: 从 approve 链接提取
                            try:
                                # 点击确认跳转
                                approve_btn = page.query_selector('a:has-text("继续访问"), a[href*="approve"], a[href*="mp.weixin.qq.com"]')
                                if approve_btn:
                                    approve_btn.click()
                                    rdelay(2000, 4000)
                                    page.wait_for_load_state('domcontentloaded', timeout=10000)
                                    current_url = page_url(page)
                                    if 'mp.weixin.qq.com' in current_url:
                                        wechat_url = current_url
                            except:
                                pass

                    if not wechat_url or 'mp.weixin.qq.com' not in wechat_url:
                        print(f"    ❌ 未到达微信页面: {current_url[:80]}")
                        continue

                    # 清理 URL
                    wechat_url = wechat_url.strip().replace(' ', '')
                    if 'mp.weixin.qq.com' not in wechat_url:
                        continue

                    print(f"    → {wechat_url[:100]}")

                    # 访问微信文章
                    page.goto(wechat_url, timeout=20000, wait_until='domcontentloaded')
                    rdelay(2000, 3500)

                    article = extract_article(page)
                    article['url'] = wechat_url

                    if not article['title']:
                        print(f"    ⚠️ 无标题，跳过")
                        continue

                    # 去重
                    if wechat_url in existing_urls:
                        print(f"    ⏭️ 已入库")
                        continue

                    article['source_account'] = account_name
                    article['fetched_at'] = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

                    new_articles.append(article)
                    existing_urls.add(wechat_url)
                    processed += 1

                    print(f"    ✅ [{article['pub_date']}] {article['title'][:55]}")
                    print(f"       biz={article['__biz']} | 正文={len(article['content'])}字 | 图={len(article['images'])}张")

                except PlaywrightTimeout:
                    print(f"    ⚠️ 超时")
                except Exception as e:
                    print(f"    ⚠️ {type(e).__name__}: {e}")

                rdelay(1500, 3000)  # 访问间隔

        finally:
            browser.close()

    # 更新存储
    if new_articles:
        acct['articles'].extend(new_articles)
        acct['last_check'] = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        store[account_name] = acct
        save_store(store)
        print(f"\n📦 入库 {len(new_articles)} 篇")
    else:
        acct['last_check'] = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        store[account_name] = acct
        save_store(store)
        print(f"\n📦 无新文章")

    return new_articles


def page_url(page):
    """safe url getter"""
    try:
        return page.url
    except:
        return ''


if __name__ == '__main__':
    import sys
    # 用更精确的关键词缩小范围
    if len(sys.argv) > 1:
        query = sys.argv[1]
    else:
        query = '聚胶 轮胎'
    
    result = search_and_collect('聚胶', query, max_articles=10)
    print(f"\n=== 结果: {len(result)} 篇 ===")
    for a in result:
        print(f"  [{a['pub_date']}] {a['title'][:60]}")
