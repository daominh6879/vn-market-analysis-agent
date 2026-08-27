"""
ingest/extract_broker_views.py — Extract broker price targets / support / resistance
from news_articles via LLM structured output → broker_views table.

Pattern mirrors ingest/extract_facts.py:
  LLM tool call → Pydantic validation → upsert.

Numbers are never fabricated — if LLM finds no numeric target/support/resistance
for a mention it leaves the field None.

Usage:
    python ingest/extract_broker_views.py                     # last 1 day
    python ingest/extract_broker_views.py --days 3            # last 3 days
    python ingest/extract_broker_views.py --article-id 42     # single article
    python ingest/extract_broker_views.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, field_validator

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from data.db import get_conn
from llm import create_client
from llm.types import Message


# ── Schema ────────────────────────────────────────────────────────────────────

class BrokerView(BaseModel):
    broker: str
    ticker_or_index: str
    stance: Optional[str] = None          # buy | sell | neutral | accumulate | reduce
    target: Optional[float] = None        # giá mục tiêu / điểm index
    support: Optional[float] = None       # vùng hỗ trợ
    resistance: Optional[float] = None    # vùng kháng cự

    @field_validator("stance")
    @classmethod
    def normalize_stance(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        mapping = {
            "mua": "buy", "buy": "buy",
            "bán": "sell", "sell": "sell",
            "trung lập": "neutral", "neutral": "neutral",
            "tích lũy": "accumulate", "accumulate": "accumulate",
            "giảm": "reduce", "reduce": "reduce",
        }
        return mapping.get(v.lower().strip(), v.lower().strip())

    @field_validator("ticker_or_index")
    @classmethod
    def normalize_subject(cls, v: str) -> str:
        return v.upper().strip()


# ── LLM tool schema ───────────────────────────────────────────────────────────

_TOOL_SCHEMA = {
    "name": "save_broker_views",
    "description": "Save broker/CTCK price targets and technical levels extracted from a news article",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "views": {
                "type": "array",
                "description": "List of broker views found in the article. Empty array if none.",
                "items": {
                    "type": "object",
                    "properties": {
                        "broker": {
                            "type": "string",
                            "description": "Tên CTCK viết tắt: TPS, VCBS, VNDirect, Yuanta, SSI, HSC, VDSC, MBS, KIS ...",
                        },
                        "ticker_or_index": {
                            "type": "string",
                            "description": "Mã cổ phiếu (HPG, VIC) hoặc chỉ số (VNINDEX, VN30). Viết hoa.",
                        },
                        "stance": {
                            "type": ["string", "null"],
                            "description": "Khuyến nghị: buy/sell/neutral/accumulate/reduce. null nếu không rõ.",
                        },
                        "target": {
                            "type": ["number", "null"],
                            "description": "Giá mục tiêu (VND cho cổ phiếu, điểm cho index). null nếu không có.",
                        },
                        "support": {
                            "type": ["number", "null"],
                            "description": "Vùng hỗ trợ số cụ thể. null nếu không có.",
                        },
                        "resistance": {
                            "type": ["number", "null"],
                            "description": "Vùng kháng cự số cụ thể. null nếu không có.",
                        },
                    },
                    "required": ["broker", "ticker_or_index", "stance", "target", "support", "resistance"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["views"],
        "additionalProperties": False,
    },
}

_SYSTEM = """Bạn trích xuất nhận định của công ty chứng khoán (CTCK) từ bài báo tài chính Việt Nam.

Quy tắc:
- Chỉ lấy thông tin từ bài báo, KHÔNG bịa số.
- Mỗi cặp (CTCK, mã/chỉ số) = một item.
- Nếu bài báo không đề cập CTCK nào, trả về mảng rỗng.
- Giá mục tiêu cổ phiếu tính bằng VND (ví dụ 75.000), điểm index là số nguyên (ví dụ 1.900).
- Số trong bài dạng "1.900 điểm" → target=1900, "75.000 đ" → target=75000.
- Dấu chấm là phân cách nghìn trong tiếng Việt."""


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_views_from_text(text: str, article_id: int) -> list[BrokerView]:
    """Run LLM extraction on one article body. Returns validated BrokerView list."""
    client = create_client()

    # Truncate to 6000 chars — broker views are usually in first paragraphs
    snippet = text[:6_000]

    response = client.generate(
        messages=[Message(role="user", content=snippet)],
        system=_SYSTEM,
        tools=[_TOOL_SCHEMA],
        max_tokens=2048,
    )

    if not response.tool_calls:
        return []

    raw_views = response.tool_calls[0].input.get("views", [])
    results: list[BrokerView] = []
    for rv in raw_views:
        try:
            results.append(BrokerView(**rv))
        except Exception as e:
            sys.stderr.write(f"[extract_broker_views] Validation error article {article_id}: {e}\n")
    return results


# ── DB helpers ────────────────────────────────────────────────────────────────

def fetch_candidate_articles(days: int = 1) -> list[dict]:
    """
    Return news_articles likely to contain broker views.
    Filter on keywords present in body to avoid LLM calls on irrelevant articles.
    """
    since = datetime.now(tz=timezone.utc) - timedelta(days=days)
    keywords = [
        "TPS", "VCBS", "VNDirect", "Yuanta", "SSI", "HSC", "VDSC", "MBS", "KIS",
        "mục tiêu", "hỗ trợ", "kháng cự", "khuyến nghị", "tích lũy",
    ]
    ilike_clauses = " OR ".join(f"body ILIKE '%{kw}%'" for kw in keywords)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, url, title, body, published_at
                FROM news_articles
                WHERE published_at >= %s
                  AND ({ilike_clauses})
                ORDER BY published_at DESC
                LIMIT 200
                """,
                (since,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_article_by_id(article_id: int) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, url, title, body, published_at FROM news_articles WHERE id = %s",
                (article_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))


def upsert_views(views: list[BrokerView], article: dict) -> int:
    if not views:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO broker_views
                    (broker, ticker_or_index, published_at, stance, target,
                     support, resistance, source_url, news_article_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (broker, ticker_or_index, published_at)
                DO UPDATE SET
                    stance          = EXCLUDED.stance,
                    target          = EXCLUDED.target,
                    support         = EXCLUDED.support,
                    resistance      = EXCLUDED.resistance,
                    source_url      = EXCLUDED.source_url,
                    news_article_id = EXCLUDED.news_article_id,
                    extracted_at    = NOW()
                """,
                [
                    (
                        v.broker, v.ticker_or_index, article["published_at"],
                        v.stance, v.target, v.support, v.resistance,
                        article.get("url", ""), article["id"],
                    )
                    for v in views
                ],
            )
    return len(views)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(days: int = 1, article_id: Optional[int] = None, dry_run: bool = False) -> int:
    if article_id is not None:
        article = fetch_article_by_id(article_id)
        if not article:
            print(f"Article {article_id} not found")
            return 0
        articles = [article]
    else:
        articles = fetch_candidate_articles(days)

    total = 0
    for article in articles:
        views = extract_views_from_text(article["body"], article["id"])
        if not views:
            continue
        print(f"  [{article['id']}] {article['title'][:60]} → {len(views)} view(s)")
        for v in views:
            print(f"    {v.broker:10s} {v.ticker_or_index:10s} target={v.target} "
                  f"sup={v.support} res={v.resistance} stance={v.stance}")
        if not dry_run:
            total += upsert_views(views, article)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract broker views from news_articles")
    parser.add_argument("--days",       type=int,  default=1,    help="How many days back to scan")
    parser.add_argument("--article-id", type=int,  default=None, help="Process single article by id")
    parser.add_argument("--dry-run",    action="store_true",     help="Print only, no DB write")
    args = parser.parse_args()

    print(f"Scanning {'last ' + str(args.days) + ' day(s)' if not args.article_id else 'article ' + str(args.article_id)} ...")
    n = run(days=args.days, article_id=args.article_id, dry_run=args.dry_run)
    if args.dry_run:
        print("--dry-run: no rows inserted")
    else:
        print(f"Upserted {n} broker views into broker_views")


if __name__ == "__main__":
    main()
