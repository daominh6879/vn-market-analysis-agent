"""
agents/intents/macro_sector.py — Nhóm 4: Vĩ mô & Đặc thù ngành.

Market-brief pattern:
  - Python fetches FX, commodities, sector performance.
  - LLM writes only 5 prose slots (VI_MO, HANG_HOA, CHU_KY, VI_THE, KET_LUAN).
  - Python assembles final Markdown from fixed structure + LLM slots.
"""

from __future__ import annotations

from langfuse import observe

from llm.factory import create_client
from llm.types import Message
from tools.global_market import get_fx_rates, get_commodities
from agents.intents import strip_preamble, strip_thinking, extract_slot


_SYSTEM = (
    "Bạn là chuyên gia phân tích vĩ mô và ngành chứng khoán Việt Nam. "
    "KHÔNG tự bịa số liệu — dùng đúng các số đã cung cấp. "
    "TUYỆT ĐỐI không viết quá trình suy nghĩ, không ghi chú nội bộ, "
    "không giải thích bước phân tích. "
    "Viết HOÀN TOÀN bằng tiếng Việt. "
    "Output chỉ gồm 5 phần được đánh dấu, không có text nào khác."
)


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


def _assemble_report(
    ticker: str | None,
    fx_text: str,
    comm_text: str,
    sector_text: str,
    vi_mo: str,
    hang_hoa: str,
    chu_ky: str,
    vi_the: str,
    ket_luan: str,
) -> str:
    subject = ticker if ticker else "Doanh nghiệp"
    return (
        f"# Vĩ mô & Ngành\n\n"
        f"**Tỷ giá:** {fx_text}\n\n"
        f"**Hàng hóa:** {comm_text}\n\n"
        f"**Ngành:** {sector_text}\n\n"
        f"## Tác động Vĩ mô\n{vi_mo}\n\n"
        f"## Hàng hóa & Crack Spread\n{hang_hoa}\n\n"
        f"## Chu kỳ Ngành\n{chu_ky}\n\n"
        f"## Vị thế {subject}\n{vi_the}\n\n"
        f"## Kết luận\n{ket_luan}\n\n"
        f"[Nguồn: yfinance / VCI]"
    )


@observe(name="intent.macro_sector")
def run(ticker: str | None, query: str) -> str:
    fx_r        = get_fx_rates()
    comm_r      = get_commodities()
    sector_text = _sector_performance_text()

    fx_text   = fx_r.message   if fx_r.status   == "ok" else "Không có dữ liệu tỷ giá."
    comm_text = comm_r.message if comm_r.status  == "ok" else "Không có dữ liệu hàng hóa."

    ticker_context = ""
    if ticker:
        ticker_context = f"\nMã quan tâm: {ticker} — phân tích tác động vĩ mô lên cổ phiếu này."

    user_prompt = f"""Câu hỏi: {query}{ticker_context}

Dữ liệu vĩ mô:

Tỷ giá:
{fx_text}

Hàng hóa thế giới (Dầu, Vàng, Kim loại):
{comm_text}

Hiệu suất ngành:
{sector_text}

Logic:
- USD/VND tăng → cộng điểm xuất khẩu (VHC, FPT), trừ điểm nợ USD (HVN, PC1)
- Crack spread thép: HRC tăng + quặng giảm → biên HPG phình to
- Dầu Brent tăng → chi phí logistics tăng; hưởng lợi PVD, PVS
- Lãi suất tăng → bất lợi BĐS, bảo hiểm; ngân hàng hưởng lợi NIM

Viết đúng 5 phần sau. Bắt đầu thẳng bằng VI_MO: (không có text nào trước).

VI_MO: [2-3 câu về lãi suất, lạm phát, tỷ giá và tác động lên thị trường]
HANG_HOA: [2-3 câu về dầu, kim loại, crack spread và tác động ngành]
CHU_KY: [1-2 câu về giai đoạn chu kỳ ngành hiện tại và catalyst kích hoạt]
VI_THE: [1-2 câu về {ticker if ticker else "doanh nghiệp"} là Leader hay Laggard trong ngành]
KET_LUAN: [1-2 câu: bối cảnh vĩ mô có ủng hộ cổ phiếu không?]"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=user_prompt)],
        max_tokens=1000,
        temperature=0,
        system=_SYSTEM,
    )

    raw = resp.text.strip()
    vi_mo    = strip_thinking(extract_slot(raw, "VI_MO",    "HANG_HOA"))
    hang_hoa = strip_thinking(extract_slot(raw, "HANG_HOA", "CHU_KY"))
    chu_ky   = strip_thinking(extract_slot(raw, "CHU_KY",   "VI_THE"))
    vi_the   = strip_thinking(extract_slot(raw, "VI_THE",   "KET_LUAN"))
    ket_luan = strip_thinking(extract_slot(raw, "KET_LUAN", None))

    if not vi_mo and not hang_hoa:
        return strip_thinking(strip_preamble(raw))

    return _assemble_report(ticker, fx_text, comm_text, sector_text, vi_mo, hang_hoa, chu_ky, vi_the, ket_luan)
