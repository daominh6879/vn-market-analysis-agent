"""
agents/intents/macro_sector.py — Nhóm 4: Vĩ mô & Đặc thù ngành.

Collects: FX rates (USD/VND), commodities (oil, steel HRC, agri), sector performance.
LLM synthesizes: crack spread, FX impact on exporters/importers, sector driver.
"""

from __future__ import annotations

import time

from langfuse import observe

from llm.factory import create_client
from llm.types import Message
from tools.global_market import get_fx_rates, get_commodities
from tools.result import ToolResult
from agents.intents import strip_preamble, strip_thinking, extract_report


def _sector_performance_text() -> str:
    try:
        from tools.providers import get_sector_performance
        r = get_sector_performance()
        return r.message if r.status == "ok" else "Không có dữ liệu ngành."
    except Exception:
        pass
    try:
        from tools.index_db import query_sector_performance
        rows = query_sector_performance()
        if rows:
            lines = [f"  {r['sector']}: {r['change_pct']:+.2f}%" for r in rows[:8]]
            return "Hiệu suất ngành:\n" + "\n".join(lines)
    except Exception:
        pass
    return "Không có dữ liệu ngành."


@observe(name="intent.macro_sector")
def run(ticker: str | None, query: str) -> str:
    fx_r = get_fx_rates()
    comm_r = get_commodities()
    sector_text = _sector_performance_text()

    fx_text = fx_r.message if fx_r.status == "ok" else "Không có dữ liệu tỷ giá."
    comm_text = comm_r.message if comm_r.status == "ok" else "Không có dữ liệu hàng hóa."

    ticker_context = ""
    if ticker:
        ticker_context = f"\nMã quan tâm: {ticker} — phân tích tác động vĩ mô lên cổ phiếu này."

    prompt = f"""Câu hỏi: {query}{ticker_context}

Dữ liệu vĩ mô:
### Tỷ giá
{fx_text}

### Hàng hóa thế giới (Dầu, Vàng, Kim loại)
{comm_text}

### Hiệu suất ngành
{sector_text}

Logic phân tích:
- Tỷ giá USD/VND tăng → cộng điểm xuất khẩu (VHC, FPT), trừ điểm nợ USD (HVN, PC1)
- Crack spread thép: HRC thế giới tăng + quặng sắt giảm → biên HPG quý tới phình to
- Dầu Brent tăng → chi phí vận tải tăng (logistics), hưởng lợi (PVD, PVS)
- Lãi suất tăng → bất lợi cho BĐS, bảo hiểm; ngân hàng hưởng lợi NIM
- Chu kỳ ngành: Phục hồi → Tăng trưởng → Bão hòa → Suy thoái

Viết báo cáo Markdown (không văn bản trước báo cáo):
# Vĩ mô & Ngành
## Tác động Vĩ mô (Lãi suất, Lạm phát, Tỷ giá, Chính sách)
## Hàng hóa & Crack Spread
## Chu kỳ Ngành (Giai đoạn hiện tại + Catalyst kích hoạt)
## Vị thế {ticker if ticker else 'Doanh nghiệp'} (Leader / Laggard — căn cứ hiệu suất ngành)
## Kết luận — Bối cảnh có ủng hộ cổ phiếu không?
[Nguồn: yfinance / VCI]"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=prompt)],
        max_tokens=2000,
        temperature=0,
        system=(
            "Bạn là chuyên gia phân tích vĩ mô và ngành chứng khoán Việt Nam. "
            "Bọc toàn bộ báo cáo Markdown trong <report> và </report>. "
            "KHÔNG có text nào ngoài hai thẻ đó."
        ),
    )
    return strip_thinking(strip_preamble(extract_report(resp.text.strip())))
