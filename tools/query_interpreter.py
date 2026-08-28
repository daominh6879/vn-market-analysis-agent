"""
tools/query_interpreter.py — Extract structured intent from a user question.

Outputs QueryIntent:
  tool     — which tool(s) to call
  tickers  — list of stock tickers mentioned (e.g. ["HPG", "VCB"])
  sector   — Vietnamese sector slug matching securities.sector (e.g. "vật liệu")
  year     — fiscal year as string (e.g. "2024") or None
  reason   — one-sentence explanation

Tool routing logic:
  ask_report      — qualitative/context questions from BCTC documents
  sql_query       — structured data: numbers, rankings, aggregations
  both            — needs both document context AND structured data
  news            — recent news, market outlook, near-future analysis
  report_and_news — needs BCTC context AND recent news together
  out_of_scope    — not in any data source
"""
from __future__ import annotations

from dataclasses import dataclass, field

from llm.types import Message

# Vietnamese sector values from securities table
_SECTOR_EXAMPLES = (
    "vật liệu (thép, sắt, kim loại), "
    "ngân hàng (tài chính, tín dụng), "
    "công nghệ (phần mềm, IT), "
    "bất động sản, "
    "tiêu dùng, "
    "bán lẻ, "
    "thực phẩm đồ uống, "
    "năng lượng (dầu khí, điện)"
)

_SYSTEM = f"""\
Bạn là query interpreter cho hệ thống phân tích tài chính Việt Nam.
Phân tích câu hỏi của người dùng và gọi tool interpret_query.

Hệ thống phục vụ TẤT CẢ mã chứng khoán Việt Nam (HOSE, HNX, UPCOM) — không giới hạn công ty cụ thể.

Dữ liệu hiện có:
  - bctc_structural: nội dung báo cáo tài chính (Qdrant RAG) — dữ liệu lịch sử
  - financial_facts: số liệu tài chính cấu trúc (Postgres) — mọi mã CK
  - stock_prices:    giá lịch sử (Postgres) — mọi mã CK
  - news_chunks:     tin tức tài chính gần đây (Qdrant) — realtime, forward-looking

Quy tắc routing:
  ask_report      → câu hỏi định tính: rủi ro, chiến lược, phân tích từ nội dung BCTC (quá khứ)
                    ví dụ: "rủi ro của HPG là gì", "chiến lược mở rộng của Hòa Phát", "HPG đối mặt với thách thức gì"
  sql_query       → số liệu cụ thể, xếp hạng, so sánh theo bảng, giá cổ phiếu
  both            → cần CẢ HAI: số liệu VÀ giải thích ngữ cảnh từ tài liệu
                    ví dụ: "tổng tài sản HPG và chiến lược", "doanh thu VCB 2024 và triển vọng"
  news            → CHỈ hỏi tin tức/headlines, KHÔNG cần phân tích từ BCTC
                    ví dụ: "tin tức HPG tuần này", "thị trường thép hôm nay có gì mới"
  report_and_news → câu hỏi "phân tích" một mã CK cụ thể kết hợp nền tảng tài chính + tin tức
                    dùng khi: câu có từ "phân tích", "đánh giá", "nhận định", "hôm nay" + tên công ty/mã CK
                    ví dụ: "phân tích HPG hôm nay", "đánh giá cổ phiếu VCB", "HPG đang như thế nào"
  out_of_scope    → KHÔNG trong bất kỳ nguồn nào: khuyến nghị đầu tư cụ thể ("nên mua không?"),
                    dự báo giá hàng hóa quốc tế không liên quan công ty VN cụ thể

QUAN TRỌNG: Câu hỏi về rủi ro, thách thức, chiến lược, triển vọng của công ty cụ thể (HPG, VCB, ...)
  → LUÔN là ask_report hoặc report_and_news, KHÔNG BAO GIỜ là out_of_scope.

Ví dụ routing (áp dụng cho BẤT KỲ mã CK VN nào, không chỉ HPG/VCB/FPT):
  "thị trường thép trong thời gian tới"             → news, sector=vật liệu
  "tin tức HPG tuần này"                            → news, tickers=[HPG]
  "tin tức MWG gần đây"                             → news, tickers=[MWG]
  "triển vọng ngành ngân hàng 2025"                 → news, sector=ngân hàng
  "phân tích HPG hôm nay"                           → report_and_news, tickers=[HPG]
  "đánh giá cổ phiếu VCB"                           → report_and_news, tickers=[VCB]
  "TCB đang như thế nào"                            → report_and_news, tickers=[TCB]
  "Hòa Phát có rủi ro gì từ thép Trung Quốc?"      → ask_report, tickers=[HPG]
  "MWG đối mặt với thách thức gì?"                  → ask_report, tickers=[MWG]
  "chiến lược của Vietcombank là gì?"               → ask_report, tickers=[VCB]
  "rủi ro cạnh tranh của ACB?"                      → ask_report, tickers=[ACB]
  "tổng tài sản HPG 2025 và chiến lược?"            → both, tickers=[HPG], year=2025
  "doanh thu VNM 2024 và triển vọng?"               → both, tickers=[VNM], year=2024
  "HPG lợi nhuận 2025 ra sao và rủi ro từ thép TQ?" → both, tickers=[HPG], year=2025
  "so sánh doanh thu SSI và VND năm 2024"           → sql_query, tickers=[SSI,VND], year=2024

QUY TẮC PHÂN BIỆT news vs report_and_news:
  → Có "phân tích"/"đánh giá"/"nhận định" + ticker cụ thể = report_and_news
  → Chỉ hỏi tin tức/diễn biến không cần phân tích sâu = news

Quy tắc tickers:
  - Trích xuất TẤT CẢ mã CK được đề cập — bất kỳ mã nào trên HOSE/HNX/UPCOM
  - Nếu hỏi về công ty bằng tên: "Hòa Phát"→HPG, "Vietcombank"→VCB, "Thế Giới Di Động"→MWG,
    "Vinamilk"→VNM, "Masan"→MSN, "Techcombank"→TCB, "ACB"→ACB, "Sacombank"→STB, v.v.
  - Không giới hạn HPG/VCB/FPT — mọi mã CK Việt Nam đều hợp lệ
  - Nếu không đề cập cụ thể → []

Quy tắc sector (dùng tiếng Việt từ danh sách): {_SECTOR_EXAMPLES}
  - "ngành thép" → "vật liệu"
  - "ngành ngân hàng / tài chính" → "ngân hàng"
  - "ngành công nghệ / IT" → "công nghệ"
  - Nếu hỏi về ticker cụ thể (không phải ngành) → null
  - Nếu không đề cập ngành → null

Quy tắc year:
  - "năm 2024", "2024", "Q3 2025" → year="2024" / "2025"
  - Nếu câu đề cập NHIỀU NĂM (so sánh năm này vs năm kia) → years=["2024","2025"], year=null
  - "gần nhất", "mới nhất", không đề cập → year=null, years=[]

Quy tắc sub_queries (chỉ cho ask_report):
  - Câu hỏi có ≥2 CHỦ ĐỀ ĐỘC LẬP cần retrieve từ section khác nhau → sub_queries=[...] (1 query/chủ đề)
  - "doanh thu và chiến lược mở rộng" → ["doanh thu HPG", "chiến lược mở rộng HPG"]
  - "so sánh mảng thép và mảng bất động sản" → ["mảng thép HPG", "bất động sản HPG"]
  - Câu đơn / so sánh thời gian / hỏi số liệu → sub_queries=[] (không cần decompose)
  - "nhân viên 2025 và 2024" → sub_queries=[] (cùng topic, dùng year filter)
  - "doanh thu HPG 2025" → sub_queries=[]
"""

_TOOL = {
    "name": "interpret_query",
    "description": "Extract intent and entities from a financial question.",
    "parameters": {
        "type": "object",
        "properties": {
            "tool": {
                "type": "string",
                "enum": ["ask_report", "sql_query", "both", "news", "report_and_news", "out_of_scope"],
                "description": "Which tool(s) to invoke",
            },
            "tickers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Stock tickers mentioned, uppercase. Empty if none.",
            },
            "sector": {
                "type": ["string", "null"],
                "description": "Vietnamese sector slug from securities table, or null",
            },
            "year": {
                "type": ["string", "null"],
                "description": "Fiscal year e.g. '2024', or null if multiple/unknown",
            },
            "years": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multiple fiscal years when question compares across years e.g. ['2024','2025']. Empty if single year.",
            },
            "sub_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Sub-queries if question has ≥2 independent topics needing separate retrieval. Empty for simple/compound-time/sql questions.",
            },
            "reason": {
                "type": "string",
                "description": "One sentence explaining the routing decision",
            },
        },
        "required": ["tool", "tickers", "sector", "year", "years", "sub_queries", "reason"],
    },
}


@dataclass
class QueryIntent:
    tool: str                        # ask_report | sql_query | both | news | report_and_news | out_of_scope
    tickers: list[str] = field(default_factory=list)
    sector: str | None = None
    year: str | None = None
    years: list[str] = field(default_factory=list)   # multi-year e.g. ["2024","2025"]
    sub_queries: list[str] = field(default_factory=list)  # decomposed sub-queries if multi-topic
    reason: str = ""


def interpret(question: str, client=None) -> QueryIntent:
    """Extract structured intent from a user question."""
    if client is None:
        from llm.factory import create_client
        client = create_client()

    resp = client.generate(
        messages=[Message(role="user", content=question)],
        system=_SYSTEM,
        tools=[_TOOL],
        max_tokens=512,
    )

    if resp.tool_calls:
        inp = resp.tool_calls[0].input
        return QueryIntent(
            tool=inp.get("tool", "out_of_scope"),
            tickers=[t.upper() for t in inp.get("tickers") or []],
            sector=inp.get("sector") or None,
            year=str(int(float(inp["year"]))) if inp.get("year") else None,
            years=[str(int(float(y))) for y in inp.get("years") or []],
            sub_queries=inp.get("sub_queries") or [],
            reason=inp.get("reason", ""),
        )

    # Fallback: no tool call — default to ask_report with no filter
    return QueryIntent(
        tool="ask_report",
        tickers=[],
        sector=None,
        year=None,
        reason=f"interpreter fallback (no tool call): {resp.text[:100]}",
    )
