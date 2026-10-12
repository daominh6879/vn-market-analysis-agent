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
    "Output chỉ gồm 5 phần được đánh dấu, không có text nào khác. "
    "BẮT BUỘC bọc toàn bộ output trong thẻ <report>...</report>. Output chỉ gồm: <report>[nội dung]</report>, không có text nào khác."
)


def _sector_performance_text() -> str:
    try:
        from tools.price import get_sector_performance
        r = get_sector_performance()
        if r.status == "ok":
            return r.message
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


def gather_data(ticker: str | None, query: str, time_context: dict | None = None,
                topics: list[str] | None = None) -> str:
    """Fetch FX, commodities, VN gold, sector performance — no LLM call.

    time_context accepted for a uniform gather signature; FX/commodities/sector are
    current-snapshot (no historical axis) — pass-through.

    topics (router-chosen subset of fx, commodities, gold, sector) limits which blocks are
    fetched; empty/None fetches all (broad macro/industry questions).
    """
    from tools.global_market import get_vn_gold
    from concurrent.futures import ThreadPoolExecutor
    all_topics = {"fx", "commodities", "gold", "sector"}
    want = (set(topics or ()) & all_topics) or all_topics
    fetchers = {
        "fx": get_fx_rates, "commodities": get_commodities,
        "gold": get_vn_gold, "sector": _sector_performance_text,
    }
    with ThreadPoolExecutor(max_workers=4) as pool:  # independent network/DB reads
        futs = {k: pool.submit(fn) for k, fn in fetchers.items() if k in want}
        res = {k: f.result() for k, f in futs.items()}

    def _msg(key: str, empty: str) -> str:
        r = res[key]
        return r.message if r.status == "ok" else empty

    parts = ["[VĨ MÔ & NGÀNH]"]
    if "fx" in want:
        parts.append(f"Tỷ giá: {_msg('fx', 'Không có dữ liệu tỷ giá.')}")
    if "commodities" in want:
        parts.append(f"Hàng hóa (thế giới): {_msg('commodities', 'Không có dữ liệu hàng hóa.')}")
    if "gold" in want and (gold := _msg("gold", "")):
        parts.append(f"Vàng SJC (trong nước): {gold}")
    if "sector" in want:
        parts.append(f"Ngành: {res['sector']}")
    return "\n\n".join(parts)


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

Bọc TOÀN BỘ output trong <report>...</report>.

<report>
VI_MO: [2-3 câu về lãi suất, lạm phát, tỷ giá và tác động lên thị trường]
HANG_HOA: [2-3 câu về dầu, kim loại, crack spread và tác động ngành]
CHU_KY: [1-2 câu về giai đoạn chu kỳ ngành hiện tại và catalyst kích hoạt]
VI_THE: [1-2 câu về {ticker if ticker else "doanh nghiệp"} là Leader hay Laggard trong ngành]
KET_LUAN: [1-2 câu: bối cảnh vĩ mô có ủng hộ cổ phiếu không?]
</report>"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=user_prompt)],
        max_tokens=1000,
        temperature=0,
        system=_SYSTEM,
    )

    from agents.intents import extract_report
    raw = extract_report(resp.text.strip())
    vi_mo    = extract_slot(raw, "VI_MO",    "HANG_HOA")
    hang_hoa = strip_thinking(extract_slot(raw, "HANG_HOA", "CHU_KY"))
    chu_ky   = strip_thinking(extract_slot(raw, "CHU_KY",   "VI_THE"))
    vi_the   = strip_thinking(extract_slot(raw, "VI_THE",   "KET_LUAN"))
    ket_luan = strip_thinking(extract_slot(raw, "KET_LUAN", None))

    if not vi_mo and not hang_hoa:
        return "Không thể tổng hợp vĩ mô & ngành — vui lòng thử lại."

    return _assemble_report(ticker, fx_text, comm_text, sector_text, vi_mo, hang_hoa, chu_ky, vi_the, ket_luan)
