"""
data/fireant.py — FireAnt REST API v2 news client.

Auth: POST /authentication/login → accessToken (JWT, cached until expiry).
News: GET /symbols/{ticker}/posts?type=1 → ticker-specific news articles.

Returns list[dict] compatible with cafef_rss output format:
  title, url, source, published_at, description
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

_token_cache: dict = {}  # {"token": str, "exp": float}
_token_lock = threading.Lock()


def _load_token() -> Optional[str]:
    """Return cached token if still valid (>60s margin). Thread-safe."""
    with _token_lock:
        cached = _token_cache
        if cached.get("token") and cached.get("exp", 0) > time.time() + 60:
            return cached["token"]
    return None


def _parse_jwt_exp(token: str) -> float:
    """Decode JWT payload (no verification) and return exp unix timestamp."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return 0.0
        # Add padding
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.b64decode(padded))
        return float(payload.get("exp", 0))
    except Exception:
        return 0.0  # force re-login on next call


def _login() -> Optional[str]:
    """Login to FireAnt and cache token. Returns accessToken or None."""
    try:
        import httpx
    except ImportError:
        sys.stderr.write("[fireant] httpx not installed\n")
        return None

    base = os.environ.get("FIREANT_BASE", "https://restv2.fireant.vn")
    email = os.environ.get("FIREANT_EMAIL", "")
    password = os.environ.get("FIREANT_PASSWORD", "")

    if not email or not password:
        sys.stderr.write("[fireant] FIREANT_EMAIL / FIREANT_PASSWORD not set\n")
        return None

    try:
        resp = httpx.post(
            f"{base}/authentication/login",
            json={"email": email, "password": password},
            timeout=15,
        )
        if resp.status_code != 200:
            sys.stderr.write(f"[fireant] login → HTTP {resp.status_code}\n")
            return None
        data = resp.json()
        if not data.get("succeeded"):
            sys.stderr.write(f"[fireant] login failed: {data.get('errorMessage','')}\n")
            return None
        token = data.get("accessToken")
        if not token:
            sys.stderr.write("[fireant] no accessToken in response\n")
            return None
        exp = _parse_jwt_exp(token)
        _token_cache["token"] = token
        _token_cache["exp"] = exp
        return token
    except Exception as e:
        sys.stderr.write(f"[fireant] login error: {e}\n")
        return None


def _get_token() -> Optional[str]:
    """Return valid token, re-logging in if expired."""
    token = _load_token()
    if token:
        return token
    return _login()


def fetch_ticker_news(ticker: str, max_articles: int = 10) -> list[dict]:
    """
    Fetch news articles for a ticker from FireAnt.

    Uses GET /symbols/{ticker}/posts?type=1 (news only, not social posts).
    Returns list[dict] with keys: title, url, source, published_at, description.
    Compatible with cafef_rss.fetch_ticker_news() output format.
    """
    try:
        import httpx
    except ImportError:
        return []

    token = _get_token()
    if not token:
        return []

    base = os.environ.get("FIREANT_BASE", "https://restv2.fireant.vn")
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = httpx.get(
            f"{base}/symbols/{ticker.upper()}/posts",
            params={"type": 1, "limit": min(max_articles, 50)},
            headers=headers,
            timeout=15,
        )
    except Exception as e:
        sys.stderr.write(f"[fireant] {ticker} news request failed: {e}\n")
        return []

    if resp.status_code == 401:
        # Token expired — force re-login once
        _token_cache.clear()
        token = _login()
        if not token:
            return []
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = httpx.get(
                f"{base}/symbols/{ticker.upper()}/posts",
                params={"type": 1, "limit": min(max_articles, 50)},
                headers=headers,
                timeout=15,
            )
        except Exception as e:
            sys.stderr.write(f"[fireant] {ticker} retry failed: {e}\n")
            return []

    if resp.status_code != 200:
        sys.stderr.write(f"[fireant] {ticker} → HTTP {resp.status_code}\n")
        return []

    try:
        posts = resp.json()
    except Exception as e:
        sys.stderr.write(f"[fireant] {ticker} JSON parse error: {e}\n")
        return []

    articles: list[dict] = []
    for post in posts:
        title = post.get("title") or ""
        if not title:
            continue  # skip social posts without a title

        src = post.get("postSource") or {}
        source_name = src.get("name", "FireAnt") if isinstance(src, dict) else "FireAnt"
        source_url = src.get("url", "") if isinstance(src, dict) else ""

        date_raw = post.get("date", "")
        pub_iso = date_raw if date_raw else datetime.now(timezone.utc).isoformat()

        description = post.get("description") or ""

        articles.append({
            "title":        title,
            "url":          source_url,  # source homepage (article URL not exposed by API)
            "source":       source_name,
            "published_at": pub_iso,
            "description":  description,
        })

    return articles[:max_articles]


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    # Load .env
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"'))

    parser = argparse.ArgumentParser()
    parser.add_argument("ticker", nargs="?", default="HPG")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    results = fetch_ticker_news(args.ticker, max_articles=args.limit)
    print(f"\n=== FireAnt news for {args.ticker}: {len(results)} articles ===")
    for i, a in enumerate(results, 1):
        pub = a["published_at"][:16]
        print(f"{i:2}. [{a['source']} | {pub}] {a['title']}")
        if a.get("description"):
            print(f"    {a['description'][:100]}")
