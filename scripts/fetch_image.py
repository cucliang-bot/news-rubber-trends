#!/usr/bin/env python3
"""
Article image downloader.

Fetches the main image from an article page (og:image or first large image)
and downloads it to a local path.

Usage:
    python scripts/fetch_image.py --url https://example.com/article --source-id erj --date 2026-09-28
"""

import hashlib
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TIMEOUT = 15
MIN_IMAGE_SIZE = 10_000  # Minimum bytes to consider a valid image


def _fetch_html(url: str) -> str | None:
    """Fetch article page HTML."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning("Failed to fetch HTML from %s: %s", url, e)
        return None


def _extract_og_image(html: str, base_url: str) -> str | None:
    """Extract og:image meta tag URL."""
    soup = BeautifulSoup(html, "lxml")
    og_tag = soup.find("meta", property="og:image")
    if og_tag and og_tag.get("content"):
        img_url = og_tag["content"].strip()
        return urljoin(base_url, img_url)

    # Also check og:image:url
    og_tag = soup.find("meta", property="og:image:url")
    if og_tag and og_tag.get("content"):
        img_url = og_tag["content"].strip()
        return urljoin(base_url, img_url)

    return None


def _extract_first_large_image(html: str, base_url: str) -> str | None:
    """Extract the first reasonably large image from article content."""
    soup = BeautifulSoup(html, "lxml")

    # Look in common article containers
    article_containers = soup.find_all(
        ["article", "div"],
        class_=re.compile(r"(article|content|post|entry|main)", re.I),
    )
    search_scope = article_containers if article_containers else [soup]

    for container in search_scope:
        for img in container.find_all("img", src=True):
            src = img["src"].strip()
            if not src or src.startswith("data:"):
                continue
            # Skip tiny images (icons, trackers)
            width = img.get("width", "")
            height = img.get("height", "")
            if width and str(width).isdigit() and int(width) < 100:
                continue
            if height and str(height).isdigit() and int(height) < 100:
                continue
            # Skip common non-content patterns
            if any(skip in src.lower() for skip in ["logo", "icon", "avatar", "sprite", "pixel", "blank"]):
                continue
            return urljoin(base_url, src)

    return None


def _get_image_extension(url: str, content_type: str = "") -> str:
    """Determine image file extension from URL or content type."""
    # From URL path
    path = urlparse(url).path.lower()
    for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"]:
        if path.endswith(ext):
            return ext.lstrip(".")
    # From content type
    ct = content_type.lower()
    if "jpeg" in ct or "jpg" in ct:
        return "jpg"
    if "png" in ct:
        return "png"
    if "gif" in ct:
        return "gif"
    if "webp" in ct:
        return "webp"
    return "jpg"  # default


def _download_image(url: str, dest_path: Path) -> bool:
    """Download an image file. Falls back to curl for Cloudflare-protected sites."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        data = resp.content

        if len(data) < MIN_IMAGE_SIZE:
            logger.warning("Image too small (%d bytes), skipping: %s", len(data), url)
            return False

        ext = _get_image_extension(url, content_type)
        final_path = dest_path.with_suffix(f".{ext}")
        final_path.parent.mkdir(parents=True, exist_ok=True)

        with open(final_path, "wb") as f:
            f.write(data)

        logger.info("Downloaded image: %s (%d bytes)", final_path.name, len(data))
        return True

    except Exception as e:
        logger.warning("requests download failed for %s: %s, trying curl fallback", url, e)
        return _download_image_curl(url, dest_path)


def _download_image_curl(url: str, dest_path: Path) -> bool:
    """Fallback image download using curl subprocess."""
    try:
        # Determine extension from URL
        ext = _get_image_extension(url)
        final_path = dest_path.with_suffix(f".{ext}")
        final_path.parent.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            [
                "curl", "-L", "-s", "-o", str(final_path),
                "-H", f"User-Agent: {HEADERS['User-Agent']}",
                "--connect-timeout", "10",
                "--max-time", "30",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=35,
        )

        if result.returncode == 0 and final_path.exists() and final_path.stat().st_size >= MIN_IMAGE_SIZE:
            logger.info("curl downloaded image: %s (%d bytes)", final_path.name, final_path.stat().st_size)
            return True
        else:
            logger.warning("curl download failed or image too small for %s", url)
            if final_path.exists():
                final_path.unlink()
            return False

    except Exception as e:
        logger.error("curl fallback also failed for %s: %s", url, e)
        return False


def fetch_article_image(url: str, source_id: str, date_str: str = "") -> str:
    """
    Download the main image from an article page.

    Steps:
    1. Fetch the article HTML
    2. Extract og:image meta tag
    3. Fall back to first large image in content
    4. Download image to local path: data/{date}/images/{id}.{ext}

    Args:
        url: Article URL
        source_id: Source identifier (e.g., 'erj', 'tanhei')
        date_str: Date string (YYYY-MM-DD), defaults to today

    Returns:
        Local file path of downloaded image, or the remote image URL if download failed.
        Empty string if no image found.
    """
    if not date_str:
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")

    html = _fetch_html(url)
    if not html:
        logger.warning("Could not fetch article page: %s", url)
        return ""

    # Try og:image first
    image_url = _extract_og_image(html, url)
    if not image_url:
        # Fall back to first large image
        image_url = _extract_first_large_image(html, url)

    if not image_url:
        logger.info("No image found for article: %s", url)
        return ""

    # Generate local filename from URL hash
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    image_dir = PROJECT_DIR / "data" / date_str / "images"
    dest_path = image_dir / f"{source_id}_{url_hash}"

    success = _download_image(image_url, dest_path)
    if success:
        # Return the actual path with extension
        ext = _get_image_extension(image_url)
        actual_path = dest_path.with_suffix(f".{ext}")
        return str(actual_path)
    else:
        # Return remote URL as fallback
        logger.info("Returning remote image URL as fallback: %s", image_url)
        return image_url


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Download article image")
    parser.add_argument("--url", type=str, required=True, help="Article URL")
    parser.add_argument("--source-id", type=str, required=True, help="Source ID")
    parser.add_argument("--date", type=str, default="", help="Date (YYYY-MM-DD)")
    args = parser.parse_args()

    result = fetch_article_image(args.url, args.source_id, args.date)
    print(result)


if __name__ == "__main__":
    main()
