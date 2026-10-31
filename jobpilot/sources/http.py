from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36 JobPilot/0.1")

_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": UA})
    return _client


def request(method: str, url: str, retries: int = 2, **kw: Any) -> httpx.Response | None:
    """HTTP with polite retries. Returns None on 404 or persistent failure."""
    for attempt in range(retries + 1):
        try:
            r = client().request(method, url, **kw)
            if r.status_code == 404:
                return None
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except httpx.HTTPError as e:
            if attempt >= retries:
                log.warning("%s %s failed: %s", method, url, e)
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def get_json(url: str, **kw: Any) -> Any:
    r = request("GET", url, **kw)
    if r is None:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def html_to_text(raw: str) -> str:
    """Strip HTML (including double-escaped HTML, as Greenhouse returns) to readable text."""
    if not raw:
        return ""
    if "&lt;" in raw:
        raw = html.unescape(raw)
    soup = BeautifulSoup(raw, "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for tag in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"]):
        tag.append("\n\n")
    for tag in soup.find_all(["br", "div", "tr"]):
        tag.append("\n")
    text = soup.get_text("")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):  # epoch seconds or millis
        v = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
