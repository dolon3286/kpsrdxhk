#!/usr/bin/env python3
"""JDownloader-style link grabber for KPSML-X."""
from __future__ import annotations

import asyncio
import re
from html import unescape
from urllib.parse import urljoin, urlparse

import aiohttp

from bot import LOGGER
from bot.helper.ext_utils.bot_utils import sync_to_async, is_magnet
from bot.helper.ext_utils.exceptions import DirectDownloadLinkException
from bot.helper.mirror_utils.download_utils.direct_link_generator import direct_link_generator

URL_RE = re.compile(r'https?://[^\s<>"\']+', re.I)
HTML_URL_RE = re.compile(r'''(?:href|src)\s*=\s*["\']([^"\'#]+)["\']''', re.I)
MAX_LINKS = 100


def normalize_url(url):
    return unescape(url.strip().strip("<>\"'")).rstrip(").,;]")


def extract_urls(text):
    seen, urls = set(), []
    for value in URL_RE.findall(text or ""):
        value = normalize_url(value)
        if value and value not in seen:
            seen.add(value)
            urls.append(value)
        if len(urls) >= MAX_LINKS:
            break
    return urls


def guess_name(url):
    name = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return unescape(name) or urlparse(url).hostname or "download"


def content_item(url, name=None, size=0, header="", source_url=None):
    return {
        "path": "",
        "filename": name or guess_name(url),
        "url": url,
        "size": int(size or 0),
        "header": header or "",
        "source_url": source_url or url,
    }


async def probe(session, url):
    try:
        async with session.head(url, allow_redirects=True) as r:
            ctype = r.headers.get("Content-Type", "")
            length = int(r.headers.get("Content-Length", "0") or 0)
            return {
                "url": str(r.url),
                "status": r.status,
                "content_type": ctype,
                "size": length,
                "name": guess_name(str(r.url)),
                "is_html": "text/html" in ctype.lower(),
            }
    except Exception:
        try:
            async with session.get(url, allow_redirects=True) as r:
                ctype = r.headers.get("Content-Type", "")
                length = int(r.headers.get("Content-Length", "0") or 0)
                return {
                    "url": str(r.url),
                    "status": r.status,
                    "content_type": ctype,
                    "size": length,
                    "name": guess_name(str(r.url)),
                    "is_html": "text/html" in ctype.lower(),
                }
        except Exception as exc:
            return {
                "url": url, "status": 0, "content_type": "", "size": 0,
                "name": guess_name(url), "is_html": False, "error": str(exc)
            }


async def crawl_page(session, url):
    try:
        async with session.get(url, allow_redirects=True) as r:
            body = await r.text(errors="ignore")
            base = str(r.url)
    except Exception as exc:
        LOGGER.debug("LinkGrabber crawl failed for %s: %s", url, exc)
        return []

    found = []
    for raw in HTML_URL_RE.findall(body):
        candidate = normalize_url(urljoin(base, raw))
        if candidate.startswith(("http://", "https://")) and candidate not in found:
            found.append(candidate)
        if len(found) >= MAX_LINKS:
            break
    return found


async def resolve_one(url, session):
    url = normalize_url(url)
    if not url or is_magnet(url):
        return []

    info = await probe(session, url)
    ctype = info.get("content_type", "").lower()

    if (
        info.get("status", 0) in range(200, 400)
        and not info.get("is_html")
        and "text/" not in ctype
    ):
        return [content_item(
            info["url"], info.get("name"), info.get("size", 0), source_url=url
        )]

    try:
        generated = await sync_to_async(direct_link_generator, url)
        if isinstance(generated, dict) and generated.get("contents"):
            result = []
            for item in generated["contents"]:
                result.append(content_item(
                    item["url"],
                    item.get("filename"),
                    item.get("size", 0),
                    item.get("header", generated.get("header", "")),
                    url,
                ))
            return result
        if isinstance(generated, tuple):
            final_url, header = generated
            return [content_item(
                final_url, info.get("name"), info.get("size", 0), header, url
            )]
        if isinstance(generated, str):
            return [content_item(
                generated, info.get("name"), info.get("size", 0), source_url=url
            )]
    except DirectDownloadLinkException:
        pass
    except Exception as exc:
        LOGGER.debug("Link resolver failed for %s: %s", url, exc)

    if info.get("is_html") or "text/html" in ctype:
        result = []
        for child in (await crawl_page(session, info.get("url", url)))[:MAX_LINKS]:
            result.extend(await resolve_one(child, session))
            if len(result) >= MAX_LINKS:
                break
        return result[:MAX_LINKS]

    return []


async def grab_links(urls):
    urls = list(dict.fromkeys(normalize_url(u) for u in urls if u))[:MAX_LINKS]
    if not urls:
        return []

    timeout = aiohttp.ClientTimeout(total=25)
    connector = aiohttp.TCPConnector(ssl=False, limit=20)
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120 Safari/537.36"}

    async with aiohttp.ClientSession(
        timeout=timeout, connector=connector, headers=headers
    ) as session:
        sem = asyncio.Semaphore(10)

        async def worker(url):
            async with sem:
                return await resolve_one(url, session)

        groups = await asyncio.gather(
            *(worker(url) for url in urls), return_exceptions=True
        )

    result, seen = [], set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            link = item.get("url")
            if not link or link in seen:
                continue
            seen.add(link)
            item["name"] = item.get("filename") or guess_name(link)
            item["size"] = int(item.get("size", 0) or 0)
            result.append(item)
            if len(result) >= MAX_LINKS:
                return result
    return result


def build_details(entries):
    contents, total = [], 0
    for item in entries:
        contents.append({
            "path": item.get("path", ""),
            "filename": item["filename"],
            "url": item["url"],
            "size": item.get("size", 0),
            "header": item.get("header", ""),
            "source_url": item.get("source_url", item["url"]),
        })
        total += int(item.get("size", 0) or 0)
    return {
        "contents": contents,
        "title": "LinkGrabber",
        "total_size": total,
    }
