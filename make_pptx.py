"""Generate BAO-CAO-CUOI-KY.pptx — project presentation deck."""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.oxml.ns import qn

# ── Theme ──────────────────────────────────────────────────────────────
NAVY   = RGBColor(0x1F, 0x38, 0x64)
BLUE   = RGBColor(0x2E, 0x75, 0xB6)
TEAL   = RGBColor(0x0E, 0x8A, 0x8A)
DARK   = RGBColor(0x26, 0x26, 0x26)
GRAY   = RGBColor(0x5A, 0x5A, 0x5A)
LIGHT  = RGBColor(0xF2, 0xF5, 0xFA)
WHITE  = RGBColor(0xFF, 0xFF, 0xFF)
CODE_BG= RGBColor(0x1E, 0x1E, 0x2E)
CODE_FG= RGBColor(0xD7, 0xDC, 0xE8)
FONT   = "Calibri"
MONO   = "Consolas"

# semantic fills for flow boxes
C_BLUE   = RGBColor(0xD4, 0xEA, 0xF8)   # data / retrieve
C_ORANGE = RGBColor(0xFD, 0xEA, 0xE2)   # route / LLM-on-top
C_GREEN  = RGBColor(0xD5, 0xEF, 0xDF)   # pure logic / no LLM
C_GOLD   = RGBColor(0xFD, 0xEB, 0xD0)   # LLM generation
C_RED    = RGBColor(0xFA, 0xDB, 0xD8)   # out-of-scope / decline

prs = Presentation()
prs.slide_width  = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]

def _box(slide, l, t, w, h):
    tb = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tb.text_frame.word_wrap = True
    return tb.text_frame

def _set(tf, text, size, color=DARK, bold=False, align=PP_ALIGN.LEFT, font=FONT):
    tf.text = text
    p = tf.paragraphs[0]
    p.alignment = align
    r = p.runs[0]
    r.font.size = Pt(size); r.font.bold = bold; r.font.color.rgb = color
    r.font.name = font
    return tf

def add_title_slide(title, subtitle):
    s = prs.slides.add_slide(BLANK)
    band = s.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(7.5))
    band.fill.solid(); band.fill.fore_color.rgb = NAVY; band.line.fill.background()
    bar = s.shapes.add_shape(1, Inches(0.9), Inches(2.6), Inches(0.09), Inches(4.5))
    bar.fill.solid(); bar.fill.fore_color.rgb = TEAL; bar.line.fill.background()
    tf = _box(s, 0.9, 3.0, 11.5, 1.6)
    _set(tf, title, 40, WHITE, bold=True)
    tf2 = _box(s, 0.9, 4.8, 11.5, 1.4)
    _set(tf2, subtitle, 20, RGBColor(0xBD, 0xD0, 0xE8))
    return s

def add_header(s, title, kicker=None):
    band = s.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(1.0))
    band.fill.solid(); band.fill.fore_color.rgb = NAVY; band.line.fill.background()
    bar = s.shapes.add_shape(1, Inches(0.22), Inches(0.42), Inches(0.06), Inches(0.62))
    bar.fill.solid(); bar.fill.fore_color.rgb = TEAL; bar.line.fill.background()
    tf = _box(s, 0.55, 0.18, 12.2, 0.7)
    _set(tf, title, 26, WHITE, bold=True)
    if kicker:
        kf = _box(s, 0.55, 0.78, 12.2, 0.3)
        _set(kf, kicker, 12, RGBColor(0xBD, 0xD0, 0xE8))

def add_bullets(s, items, top=1.3, left=0.6, width=12.2, height=5.9, size=17):
    tf = _box(s, left, top, width, height)
    for i, (lvl, text, *rest) in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.level = lvl
        p.space_after = Pt(6)
        bold = rest[0] if rest else False
        color = rest[1] if len(rest) > 1 else (DARK if lvl == 0 else GRAY)
        r = p.add_run()
        r.text = text
        r.font.size = Pt(size - (2 * lvl))
        r.font.bold = bold
        r.font.color.rgb = color
        r.font.name = FONT
        if lvl == 0:
            p.runs[0].text = "▪ " + text
    return tf

def add_table(s, headers, rows, col_widths, top=1.3, left=0.6, font_size=12, header_size=13):
    n_r = len(rows) + 1
    n_c = len(headers)
    tbl_shape = s.shapes.add_table(n_r, n_c, Inches(left), Inches(top),
                                   Inches(sum(col_widths)), Inches(0.4 * n_r))
    tbl = tbl_shape.table
    for j, w in enumerate(col_widths):
        tbl.columns[j].width = Inches(w)
    for j, h in enumerate(headers):
        c = tbl.cell(0, j)
        c.text = h
        c.fill.solid(); c.fill.fore_color.rgb = NAVY
        for p in c.text_frame.paragraphs:
            p.runs[0].font.size = Pt(header_size); p.runs[0].font.bold = True
            p.runs[0].font.color.rgb = WHITE; p.runs[0].font.name = FONT
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            c = tbl.cell(i + 1, j)
            c.text = str(val)
            c.fill.solid()
            c.fill.fore_color.rgb = LIGHT if i % 2 == 0 else WHITE
            for p in c.text_frame.paragraphs:
                p.runs[0].font.size = Pt(font_size); p.runs[0].font.name = FONT
                p.runs[0].font.color.rgb = DARK
    return tbl

def add_code(s, code, top=1.3, left=0.6, width=12.1, height=5.7, size=13):
    box = s.shapes.add_shape(1, Inches(left), Inches(top), Inches(width), Inches(height))
    box.fill.solid(); box.fill.fore_color.rgb = CODE_BG; box.line.fill.background()
    tf = box.text_frame; tf.word_wrap = True
    tf.margin_left = Inches(0.2); tf.margin_top = Inches(0.15)
    lines = code.split("\n")
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        r = p.add_run(); r.text = line if line else " "
        r.font.size = Pt(size); r.font.name = MONO; r.font.color.rgb = CODE_FG
    return box

# ── Shape-based flow helpers ────────────────────────────────────────────
def add_box(slide, x, y, w, h, text, fill=C_BLUE, stroke=NAVY, fg=DARK, size=12, bold=False, dash=None):
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.fill.solid(); shp.fill.fore_color.rgb = fill
    shp.line.color.rgb = stroke; shp.line.width = Pt(1.25)
    if dash:
        shp.line.dash_style = dash
    shp.shadow.inherit = False
    tf = shp.text_frame; tf.word_wrap = True
    tf.margin_left = Inches(0.05); tf.margin_right = Inches(0.05)
    tf.margin_top = Inches(0.03); tf.margin_bottom = Inches(0.03)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    for i, ln in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run(); r.text = ln
        r.font.size = Pt(size); r.font.bold = bold; r.font.color.rgb = fg; r.font.name = FONT
    return shp

def add_arrow(slide, x1, y1, x2, y2, color=NAVY, width=1.5, dash=False):
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    conn.line.color.rgb = color; conn.line.width = Pt(width)
    if dash:
        conn.line.dash_style = 2  # dash
    try:
        ln = conn._element.spPr.find(qn('a:ln'))
        if ln is None:
            ln = conn._element.spPr.makeelement(qn('a:ln'), {}); conn._element.spPr.append(ln)
        ln.append(ln.makeelement(qn('a:tailEnd'), {'type': 'triangle', 'w': 'med', 'len': 'med'}))
    except Exception:
        pass
    return conn

def add_elabel(slide, x, y, w, text, size=10, color=GRAY, bold=False):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(0.3))
    tf = tb.text_frame; tf.word_wrap = False
    tf.text = text
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    r = p.runs[0]; r.font.size = Pt(size); r.font.bold = bold; r.font.color.rgb = color; r.font.name = FONT
    return tb

# ══════════════════════════════════════════════════════════════════════
# 1. Title
add_title_slide(
    "Hệ thống phân tích chứng khoán Việt Nam",
    "Kiến trúc RAG + Agent · LangGraph · Tools · Router · RAG-Fusion (RRF)\nBáo cáo cuối kỳ"
)

# 2. Agenda
s = prs.slides.add_slide(BLANK)
add_header(s, "Nội dung trình bày")
add_bullets(s, [
    (0, "Tổng quan kiến trúc — 3 tầng xử lý"),
    (0, "Stack công nghệ & mô hình (LLM / Embedding)"),
    (0, "Nguồn dữ liệu & lưu trữ (Postgres, MinIO, Qdrant)"),
    (0, "Dagster — pipeline điều phối dữ liệu"),
    (0, "Router — phân loại & điều hướng câu hỏi"),
    (0, "Tool Layer — hợp đồng ToolResult + registry"),
    (0, "LangGraph — luồng agent (graph trạng thái)"),
    (0, "RAG-Fusion + Reciprocal Rank Fusion (RRF)"),
    (0, "Eval — đánh giá offline (ngoài luồng chính)"),
    (0, "Kết luận — 3 quyết định thiết kế"),
], size=20)

# 3. Architecture — 3 tầng (shape)
s = prs.slides.add_slide(BLANK)
add_header(s, "Tổng quan luồng — 3 tầng", "LangGraph là tầng giữa — nơi điều phối toàn bộ trạng thái")
add_box(s, 1.5, 1.35, 10.3, 1.25,
        "TẦNG 1 — conversation_router\nLLM-on-top · 1 lần gọi LLM · ép chọn tool\nneeds_agent_run / direct_reply / out_of_scope / decompose",
        fill=C_ORANGE, stroke=RGBColor(0xD3, 0x54, 0x00), bold=True, size=13)
add_box(s, 1.5, 3.15, 10.3, 1.25,
        "TẦNG 2 — agents/graph.py  (LANGGRAPH — trọng tâm)\nclassify → clarify → decompose/fast-path → run_subqueries\n→ synthesize → critique → finalize",
        fill=C_GOLD, stroke=RGBColor(0xF3, 0x9C, 0x12), bold=True, size=13)
add_box(s, 1.5, 4.95, 10.3, 1.25,
        "TẦNG 3 — intent modules (gather_data / run) → tools/*\nLấy dữ liệu thực: giá · chỉ báo · định giá · tin · vĩ mô · RAG",
        fill=C_BLUE, stroke=BLUE, bold=True, size=13)
add_arrow(s, 6.65, 2.60, 6.65, 3.15, color=TEAL, width=2.0)
add_arrow(s, 6.65, 4.40, 6.65, 4.95, color=TEAL, width=2.0)
_ = _box(s, 0.3, 0.9, 1.2, 0.3); _set(_, "user query", 11, GRAY)
_ = _box(s, 11.5, 6.25, 1.6, 0.3); _set(_, "report →", 11, GRAY)

# 4. Stack
s = prs.slides.add_slide(BLANK)
add_header(s, "Stack công nghệ")
add_table(s,
    ["Layer", "Công nghệ"],
    [
        ["LLM", "DeepSeek (default) · Anthropic · OpenAI · Ollama · Gemini — qua factory"],
        ["Vector store", "Qdrant"],
        ["Relational DB", "Postgres (financial_facts, stock_prices, news_articles, …)"],
        ["Object storage", "MinIO (PDF BCTC)"],
        ["Orchestration", "Dagster (schedules + sensor)"],
        ["RAG / Agent graph", "LangGraph"],
        ["Embedding", "bge-m3 (BCTC) · nomic-embed-text (news)"],
        ["Reranker", "CrossEncoder (ms-marco-MiniLM-L-6-v2)"],
        ["Eval (offline)", "RAGAS + golden YAML"],
        ["Web / MCP / API", "Tavily · FastMCP · vnstock · VCI · SSI iBoard"],
        ["Observability", "Langfuse (tracing)"],
    ],
    [2.2, 9.9], font_size=12.5)

# 5. Data sources
s = prs.slides.add_slide(BLANK)
add_header(s, "Nguồn dữ liệu & nơi lưu trữ")
add_table(s,
    ["Nguồn", "Nội dung", "Lưu ở đâu", "Tag context"],
    [
        ["PDF BCTC (HPG, VCB…)", "Báo cáo tài chính kiểm toán", "Qdrant bctc_structural", "RAG corpus"],
        ["CafeF / VnExpress RSS", "Tin tài chính, thép, TTCK", "Postgres news_articles + Qdrant news_chunks", "TIN TỨC"],
        ["vnstock Finance API", "KQKD, CDKT, LCTT", "Postgres financial_facts", "GIÁ LỊCH SỬ"],
        ["vnstock Prices", "Giá đóng cửa điều chỉnh", "Postgres stock_prices", "GIÁ LỊCH SỬ"],
        ["LLM extract PDF", "Số liệu tài chính (fallback)", "Postgres financial_facts", "GIÁ LỊCH SỬ"],
        ["Tavily web search", "Kết quả real-time", "Không lưu", "WEB"],
        ["VCI price board", "OHLCV + khối ngoại", "Postgres ohlcv_daily / foreign_flows", "—"],
        ["SSI iBoard", "VN-Index/HNX/UPCOM/VN30", "Postgres market_index_daily", "—"],
        ["yfinance / CoinGecko", "World, commodities, crypto, FX", "Không lưu (real-time)", "—"],
        ["CafeF events + broker", "Sự kiện quyền, nhận định CTCK", "Postgres corporate_events / broker_views", "—"],
    ],
    [2.3, 3.6, 4.6, 1.6], font_size=10.5, top=1.25)

# 6. LLM Factory + models
s = prs.slides.add_slide(BLANK)
add_header(s, "LLM Factory & mô hình", "Luôn dùng create_client() — không import provider trực tiếp")
add_bullets(s, [
    (0, "create_client() đọc LLM_PROVIDER từ .env → trả đúng LLMClient", True, NAVY),
    (0, "Provider:"),
    (1, "deepseek (default) → OpenAIClient → deepseek-chat"),
    (1, "anthropic → claude-opus-5   ·   openai → gpt-4o"),
    (1, "ollama → llama3 (local)   ·   gemini → gemini-2.0-flash"),
    (0, "Embedding (RAG): bge-m3 dim=1024 (bctc_structural) · nomic-embed-text dim=768 (news_chunks)"),
    (0, "Reranker: CrossEncoder ms-marco-MiniLM-L-6-v2"),
    (0, "Langfuse: tự bọc tracing khi có LANGFUSE_PUBLIC_KEY + SECRET_KEY"),
], size=17)

# 7. Postgres schema
s = prs.slides.add_slide(BLANK)
add_header(s, "Postgres — schema quan trọng")
add_table(s,
    ["Table", "Cột chính", "Unique key"],
    [
        ["financial_facts", "ticker, period, report_type, metric_code, value, unit, source", "(ticker, period, report_type, metric_code)"],
        ["stock_prices", "ticker, ngay, close_adj, volume", "(ticker, ngay)"],
        ["news_articles", "url, title, body, source, published_at, tickers[]", "(url)"],
        ["ohlcv_daily", "ticker, date, open/high/low/close, volume", "(ticker, date)"],
        ["foreign_flows", "ticker, date, buy/sell/net_value, market", "(ticker, date)"],
        ["market_index_daily", "index_code, date, close, change_pct, foreign_net", "(index_code, date)"],
        ["corporate_events", "ticker, ex_date, event_type", "(ticker, ex_date, event_type)"],
        ["broker_views", "ticker_or_index, published_date, headline, target_price", "—"],
        ["securities", "ticker, exchange, name_aliases, tags", "(ticker)"],
    ],
    [2.6, 7.3, 2.2], font_size=10.5, top=1.25)

# 8. MinIO
s = prs.slides.add_slide(BLANK)
add_header(s, "MinIO — Object Storage (PDF)")
add_bullets(s, [
    (0, "Lưu trữ file PDF BCTC gốc (HPG, VCB…) — object storage S3-compatible"),
    (0, "Sensor: minio_new_pdf_sensor phát hiện PDF mới / sửa / xóa mỗi 5 phút", True),
    (0, "Flow ingestion:"),
], size=17)
add_code(s,
"""raw_pdf → parsed_doc → embeddings      → Qdrant bctc_structural (structural chunking)
                       → financial_facts  → Postgres (source='pdf')""",
    top=3.2, height=1.7, size=14)

# 9. Qdrant — chunking & collections (accurate)
s = prs.slides.add_slide(BLANK)
add_header(s, "Qdrant — collection & chunking", "Production dùng structural; fixed_512 chỉ là baseline so sánh")
add_table(s,
    ["Collection", "Dữ liệu", "Chunking", "Embed model", "Dùng ở đâu"],
    [
        ["bctc_structural", "BCTC (mọi ticker)", "structural — tách theo heading MD, max 800", "bge-m3 (1024)", "Production · RAG"],
        ["news_chunks", "Tin tức", "1 bài = 1 chunk (title + body)", "nomic-embed-text (768)", "Production · news"],
        ["hpg_fixed_512", "BCTC HPG", "fixed 512 · overlap 64", "bge-m3", "Baseline (Bài 7)"],
        ["hpg_structural", "BCTC HPG", "structural · không metadata", "bge-m3", "Baseline (Bài 14)"],
        ["hpg_b7_structural_meta", "BCTC HPG", "structural + metadata", "bge-m3", "Eval (Bài 14)"],
    ],
    [2.5, 2.2, 3.6, 1.9, 1.9], font_size=10, top=1.3)
add_bullets(s, [
    (0, "bctc_structural: 1 collection chung mọi ticker — cách ly bằng payload filter ticker", True),
    (0, "Hybrid retrieval: BM25 (underthesea VN tokenize) + vector → RRF"),
    (0, "Cross-collection fusion an toàn: RRF dùng rank, không cộng score (dim 768 vs 1024 khác nhau)"),
], size=14, top=4.6, height=2.6)

# 10. Dagster
s = prs.slides.add_slide(BLANK)
add_header(s, "Dagster — điều phối pipeline")
add_code(s,
"""Group: ingestion    raw_pdf → parsed_doc → embeddings → Qdrant + facts
Group: news          (mỗi 6h) news_raw → news_indexed → Qdrant news_chunks
Group: vnstock       financials (mùng 1) · prices (18:00 weekdays)
Group: market_data   index / ohlcv / global_quotes (18:00 weekdays)
Group: market_brief  07:00 events · 07:15 daily_brief · 17:30 foreign_flows

Schedules:  05:30 news · 01:00 vnstock_financials · 18:00 prices/ohlcv
            07:15 daily_brief · 17:30 foreign_flows""", size=14, height=4.4)
add_bullets(s, [
    (0, "Sensor minio_new_pdf_sensor chạy mỗi 5 phút phát hiện PDF mới", True),
], size=15, top=5.9)

# 11. Router (shape)
s = prs.slides.add_slide(BLANK)
add_header(s, "Router — phân loại & điều hướng")
add_box(s, 4.7, 1.25, 3.9, 0.8, "câu hỏi → classify (LLM)\n→ label", fill=C_ORANGE, stroke=RGBColor(0xD3, 0x54, 0x00), bold=True, size=12)
add_box(s, 0.5, 2.9, 2.9, 1.0, "số_liệu\n→ SQL agent", fill=C_BLUE, size=12)
add_box(s, 3.9, 2.9, 2.9, 1.0, "diễn_giải\n→ hybrid_rerank → RAG", fill=C_GREEN, size=12)
add_box(s, 7.3, 2.9, 2.9, 1.0, "cả_hai\n→ SQL + RAG → combine", fill=C_GOLD, size=12)
add_box(s, 10.6, 2.9, 2.3, 1.0, "ngoài_phạm_vi\n→ từ chối", fill=C_RED, stroke=RGBColor(0xC0, 0x39, 0x2B), size=12)
add_box(s, 0.5, 4.7, 3.0, 0.8, "financial_facts\nstock_prices", fill=WHITE, stroke=GRAY, size=11)
add_box(s, 3.9, 4.7, 3.0, 0.8, "RAG corpus\n(bctc_structural)", fill=WHITE, stroke=GRAY, size=11)
add_box(s, 7.3, 4.7, 3.0, 0.8, "LLM combine\ncả hai nguồn", fill=WHITE, stroke=GRAY, size=11)
add_arrow(s, 6.7, 2.05, 6.7, 2.9)
add_arrow(s, 3.4, 2.05, 1.95, 2.9)
add_arrow(s, 6.7, 2.05, 5.35, 2.9)
add_arrow(s, 6.7, 2.05, 8.75, 2.9)
add_arrow(s, 6.7, 2.05, 11.75, 2.9)
add_arrow(s, 1.95, 3.9, 1.95, 4.7)
add_arrow(s, 5.35, 3.9, 5.35, 4.7)
add_arrow(s, 8.75, 3.9, 8.75, 4.7)
add_box(s, 0.5, 6.0, 12.3, 0.9,
        "conversation_router (Tầng 1): ép chọn tool — needs_agent_run / direct_reply / out_of_scope / decompose",
        fill=C_ORANGE, stroke=RGBColor(0xD3, 0x54, 0x00), size=12)

# 12. Tool layer (clearer)
s = prs.slides.add_slide(BLANK)
add_header(s, "Tool Layer — hợp đồng ToolResult + registry")
add_code(s,
"""ToolResult(status, data, message)
   status ∈ { ok, no_data, invalid_input, upstream_error, rate_limited }
   → không raise · không trả list rỗng trần → _safe_gather chỉ cần try/except""",
    top=1.3, height=1.9, size=13)
add_bullets(s, [
    (0, "TOOL_REGISTRY: version, timeout, cost_hint, side_effect cho mọi tool", True),
    (0, "Cache tool-level in-memory TTL (instrument_tool): side_effect / free / zero-arg / error → không cache"),
    (0, "PriceProvider (ABC) + fallback chuỗi:"),
    (1, "cổ phiếu VN: Fireant → VCI → TCBS"),
    (1, "chỉ số VN: SsiIndexProvider · mã ngoại: YFinanceProvider"),
    (0, "Intent module tách 2 hàm:"),
    (1, "gather_data — thuần Python, trả text (graph dùng) · không LLM"),
    (1, "run — có LLM (market-brief pattern: Python tính số → LLM viết slot → Python lắp MD)"),
], size=15, top=3.4)

# 13. LangGraph main graph (shape)
s = prs.slides.add_slide(BLANK)
add_header(s, "LangGraph — luồng agent graph", "Mọi vòng lặp có trần · điều hướng là if/else")
# main chain (left)
add_box(s, 2.0, 1.2, 2.6, 0.62, "classify_node", fill=C_ORANGE, stroke=RGBColor(0xD3,0x54,0x00), bold=True, size=12)
add_box(s, 2.0, 2.15, 2.6, 0.62, "clarify_node\ninterrupt()", fill=C_ORANGE, stroke=RGBColor(0xD3,0x54,0x00), size=11)
add_box(s, 2.0, 3.1, 2.6, 0.62, "decompose_node\nn=4 sub-task", fill=C_BLUE, size=11)
add_box(s, 2.0, 4.05, 2.6, 0.62, "run_subqueries_node\ngather_data", fill=C_GREEN, stroke=RGBColor(0x27,0xAE,0x60), size=11)
add_box(s, 2.0, 5.0, 2.6, 0.62, "synthesize_final\nLLM stream", fill=C_GOLD, stroke=RGBColor(0xF3,0x9C,0x12), size=11)
add_box(s, 2.0, 5.95, 2.6, 0.62, "critique_report_node\nLLM check", fill=C_GOLD, stroke=RGBColor(0xF3,0x9C,0x12), size=11)
add_box(s, 2.0, 6.9, 2.6, 0.5, "finalize_node → END", fill=C_GREEN, stroke=RGBColor(0x27,0xAE,0x60), bold=True, size=11)
# side branches (right)
add_box(s, 6.4, 2.15, 2.8, 0.62, "node_market_brief\ngraph con", fill=C_BLUE, size=11)
add_box(s, 6.4, 3.1, 2.8, 0.62, "build_single_subtask_node\nfast-path", fill=C_BLUE, size=11)
add_box(s, 6.4, 4.05, 2.8, 0.62, "node_out_of_scope\ncâu cố định", fill=C_RED, stroke=RGBColor(0xC0,0x39,0x2B), size=11)
# edges main chain
add_arrow(s, 3.3, 1.82, 3.3, 2.15)
add_arrow(s, 3.3, 2.77, 3.3, 3.1)
add_arrow(s, 3.3, 3.72, 3.3, 4.05)
add_arrow(s, 3.3, 4.67, 3.3, 5.0)
add_arrow(s, 3.3, 5.62, 3.3, 5.95)
add_arrow(s, 3.3, 6.57, 3.3, 6.9)
# clarify → side branches
add_arrow(s, 4.6, 2.46, 6.4, 2.46)
add_elabel(s, 4.95, 2.1, 1.3, "market_brief")
add_arrow(s, 4.6, 2.46, 6.4, 3.41)
add_elabel(s, 4.9, 2.75, 1.2, "simple")
# clarify → out_of_scope
add_arrow(s, 4.6, 2.46, 6.4, 4.36)
add_elabel(s, 4.9, 3.35, 1.2, "out_of_scope")
# side → run
add_arrow(s, 7.8, 3.41, 4.6, 4.36)  # fast-path → run_subqueries
add_arrow(s, 7.8, 4.36, 4.6, 4.36)  # out_of_scope → END (skip)
add_arrow(s, 7.8, 2.77, 4.6, 6.9)   # market_brief → finalize
# replan back edge
add_arrow(s, 2.0, 4.36, 1.55, 3.41, color=RGBColor(0xC0,0x39,0x2B), dash=True)
add_elabel(s, 0.15, 3.7, 1.35, "replan ≤1\nempty>50%", size=9, color=RGBColor(0xC0,0x39,0x2B))
# retry back edge
add_arrow(s, 4.6, 6.26, 4.6, 5.31, color=RGBColor(0xC0,0x39,0x2B), dash=True)
add_elabel(s, 4.7, 5.55, 1.5, "retry ≤ MAX", size=9, color=RGBColor(0xC0,0x39,0x2B))
# legend
add_box(s, 9.6, 5.5, 3.3, 1.8,
        "color = vai trò\norange = route/LLM-on-top\nblue = dữ liệu/truy hồi\ngreen = thuần logic (no LLM)\ngold = sinh nội dung (LLM)\nred = từ chối / loop",
        fill=WHITE, stroke=GRAY, size=10)

# 14. RAG-Fusion + RRF (shape)
s = prs.slides.add_slide(BLANK)
add_header(s, "RAG-Fusion + Reciprocal Rank Fusion (RRF)")
add_box(s, 0.5, 2.5, 2.3, 1.3, "decompose\nLLM sinh N sub-query\n(N=4)", fill=C_BLUE, size=12)
add_box(s, 3.1, 2.5, 2.5, 1.3, "multi_retrieve\nBM25+vector + news\n+ Postgres + Tavily", fill=C_GREEN, size=11)
add_box(s, 5.9, 2.5, 2.4, 1.3, "rrf_fuse\nRRF fold mọi list\n→ top-K", fill=C_GOLD, stroke=RGBColor(0xF3,0x9C,0x12), size=12)
add_box(s, 8.6, 2.5, 2.2, 1.3, "analyze\nLLM · guard\nkhông mua/bán", fill=C_GOLD, stroke=RGBColor(0xF3,0x9C,0x12), size=11)
add_box(s, 11.0, 2.5, 1.9, 1.3, "report\nformat +\ntrích nguồn", fill=C_GREEN, stroke=RGBColor(0x27,0xAE,0x60), size=11)
add_arrow(s, 2.8, 3.15, 3.1, 3.15)
add_arrow(s, 5.6, 3.15, 5.9, 3.15)
add_arrow(s, 8.3, 3.15, 8.6, 3.15)
add_arrow(s, 10.8, 3.15, 11.0, 3.15)
add_bullets(s, [
    (0, "Hai cấp độ fusion: (1) BM25+vector cho 1 query · (2) N result list cho N sub-query", True),
    (0, "analyze gắn tag nguồn: [BCTC] · [TIN TỨC] · [WEB chưa kiểm chứng] · [GIÁ LỊCH SỬ]"),
], size=14, top=4.2)

# 15. RRF formula (more explanation)
s = prs.slides.add_slide(BLANK)
add_header(s, "RRF — công thức, lý do, ví dụ")
add_bullets(s, [
    (0, "score(doc) = Σ 1/(k + rank_i)  ·  k = 60 (Cormack 2009)", True, NAVY),
    (0, "rank_i = vị trí (1-based) của doc trong danh sách truy hồi thứ i"),
], size=16, top=1.25, height=1.2)
add_table(s,
    ["Tài liệu", "rank trong q₁", "rank trong q₂", "Điểm RRF"],
    [
        ["doc A", "1", "2", "1/61 + 1/62 = 0.0325"],
        ["doc B", "3", "1", "1/63 + 1/61 = 0.0323"],
        ["doc C", "1", "—", "1/61 = 0.0164"],
    ],
    [1.9, 1.9, 1.9, 3.6], font_size=13, top=2.7)
add_bullets(s, [
    (0, "Tại sao bỏ score, chỉ dùng rank?", True, NAVY),
    (1, "BM25 (unbounded, phụ thuộc corpus) + cosine (0–1) không cộng trực tiếp được — BM25 luôn áp đảo"),
    (1, "rank là đại lượng cộng được xuyên danh sách, không cần chuẩn hoá, không cần huấn luyện"),
    (0, "Hai vị trí áp dụng:"),
    (1, "1 — _hybrid_retrieve: BM25 + Qdrant vector → RRF → top-k (trong 1 sub-query)"),
    (1, "2 — rrf_fuse_node: N result list của N sub-query → RRF fold → top-k (xuyên query)"),
], size=14, top=4.9)

# 16. Eval — offline (clarified)
s = prs.slides.add_slide(BLANK)
add_header(s, "Eval — đánh giá offline", "KHÔNG nằm trong luồng trả lời — chỉ đo chất lượng truy hồi")
add_box(s, 1.0, 1.4, 11.3, 1.0,
        "evals/run.py chạy riêng (offline), tách khỏi graph runtime.\nGraph trả lời KHÔNG gọi eval — eval chỉ để chọn phương án truy hồi tốt nhất.",
        fill=C_RED, stroke=RGBColor(0xC0,0x39,0x2B), size=13)
add_bullets(s, [
    (0, "Golden YAML groups:", True),
    (1, "table_lookup / text_interpretation → RAGAS (faithfulness, relevancy, precision, recall)"),
    (1, "no_answer / out_of_scope → refusal pass rate"),
    (1, "news → pass/fail theo sources_used chứa TIN TỨC"),
    (0, "Retrieval methods so sánh: vector · bm25 · hybrid_weighted · hybrid_rrf · hybrid_rerank · rag_fusion · router_sql"),
    (0, "Kết quả: Multi-query (N=4) + RRF → recall 0.952 (20/21) vs single 0.857", True, NAVY),
], size=15, top=2.7)

# 17. Conclusion
s = prs.slides.add_slide(BLANK)
add_header(s, "Kết luận — 3 quyết định thiết kế")
add_bullets(s, [
    (0, "Điều hướng bằng code thuần — routing/replan/critique là if/else; chỉ node sinh nội dung gọi LLM", True, NAVY),
    (1, "Chi phí và độ trễ dự đoán được"),
    (0, "State chỉ chứa text/đường dẫn — checkpoint tuần tự hoá được, không phình", True, NAVY),
    (1, "run_subqueries gather dữ liệu dạng text, không cần LLM"),
    (0, "Mọi vòng lặp có trần — replan ≤1 · critique ≤MAX · ngân sách 8 call / 180s", True, NAVY),
    (1, "Turn 'bệnh hoạn' không đốt tiền vô hạn"),
    (0, "Interrupt/checkpoint + streaming là 2 điểm nối graph với người dùng & SSE"),
], size=18)

prs.save("BAO-CAO-CUOI-KY.pptx")
print("Saved BAO-CAO-CUOI-KY.pptx ·", len(prs.slides._sldIdLst), "slides")
