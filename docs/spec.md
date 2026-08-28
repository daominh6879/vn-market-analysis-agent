# SYSTEM SPECIFICATION: VIETNAM STOCK ANALYSIS AI AGENT

## 1. Tổng quan dự án (Project Overview)

Hệ thống AI Agent tự động phân tích đa chiều cho bất kỳ mã cổ phiếu nào trên thị trường chứng khoán Việt Nam (HOSE, HNX, UPCOM). Agent có khả năng tự động định tuyến luồng dữ liệu, thu thập thông tin theo thời gian thực từ nhiều nguồn, và tổng hợp thông qua LLM (Large Language Model) để đưa ra báo cáo phân tích bao gồm: Kỹ thuật, Cơ bản, Dòng tiền, Vĩ mô và Tâm lý thị trường.

## 2. Kiến trúc Hệ thống (System Architecture)

Hệ thống hoạt động dựa trên mô hình **Tool-Calling Agent** kết hợp với **RAG (Retrieval-Augmented Generation)**:

1. **Query Parsing:** LLM trích xuất `ticker` (mã cổ phiếu) và `intent` (mục đích truy vấn) từ prompt của người dùng.
2. **Sector Mapping:** Truy vấn Database nội bộ để xác định nhóm ngành của `ticker` và các chỉ số vĩ mô tương ứng cần theo dõi.
3. **Parallel Tool Execution:** Agent gọi đồng thời các Tool (API) để thu thập dữ liệu giá, kỹ thuật, cơ bản và tin tức.
4. **Data Aggregation & Caching:** Dữ liệu trả về được chuẩn hóa. Các request trùng lặp được cache tại Redis để tối ưu API rate-limit.
5. **Synthesis & Response:** LLM tổng hợp các luồng dữ liệu thô thành bài phân tích logic, nhận diện rủi ro và cơ hội.

---

## 3. Đặc tả Tools (Function Calling Specifications)

Dưới đây là schema JSON đặc tả các công cụ được cung cấp cho LLM Agent.

### 3.1. Tool: `get_market_data`

Thu thập dữ liệu giao dịch realtime và lịch sử dòng tiền.

```json
{
  "name": "get_market_data",
  "description": "Lấy dữ liệu giá OHLCV và dòng tiền hiện tại của một mã cổ phiếu.",
  "parameters": {
    "type": "object",
    "properties": {
      "ticker": {
        "type": "string",
        "description": "Mã cổ phiếu (VD: HPG, FPT)"
      },
      "timeframe": {
        "type": "string",
        "enum": ["15m", "1H", "1D"],
        "description": "Khung thời gian cho nến giá"
      }
    },
    "required": ["ticker", "timeframe"]
  }
}

```

### 3.2. Tool: `get_technical_indicators`

Tính toán các chỉ báo kỹ thuật cốt lõi.

```json
{
  "name": "get_technical_indicators",
  "description": "Tính toán RSI, MACD, MA, Bollinger Bands cho cổ phiếu.",
  "parameters": {
    "type": "object",
    "properties": {
      "ticker": { "type": "string" }
    },
    "required": ["ticker"]
  }
}

```

### 3.3. Tool: `get_macro_drivers` (Dynamic Mapping)

Lấy dữ liệu vĩ mô hoặc giá hàng hóa thiết yếu dựa trên đặc thù ngành của doanh nghiệp.

```json
{
  "name": "get_macro_drivers",
  "description": "Lấy các chỉ số vĩ mô/hàng hóa ảnh hưởng trực tiếp đến doanh nghiệp dựa trên mapping ngành.",
  "parameters": {
    "type": "object",
    "properties": {
      "ticker": { "type": "string" }
    },
    "required": ["ticker"]
  }
}

```

### 3.4. Tool: `get_fundamentals`

Truy xuất hồ sơ doanh nghiệp và các chỉ số tài chính.

```json
{
  "name": "get_fundamentals",
  "description": "Lấy P/E, P/B, EPS, Doanh thu, Lợi nhuận và Tồn kho.",
  "parameters": {
    "type": "object",
    "properties": {
      "ticker": { "type": "string" }
    },
    "required": ["ticker"]
  }
}

```

### 3.5. Tool: `get_news_and_sentiment`

Thu thập tin tức mới nhất và điểm cảm xúc.

```json
{
  "name": "get_news_and_sentiment",
  "description": "Lấy 5 tin tức gần nhất về mã cổ phiếu và phân tích Sentiment (Bearish/Neutral/Bullish).",
  "parameters": {
    "type": "object",
    "properties": {
      "ticker": { "type": "string" }
    },
    "required": ["ticker"]
  }
}

```

---

## 4. Tích hợp Nguồn dữ liệu (Data Integrations)

| Lớp Dữ liệu | Nguồn cấp (API/Library) | Cơ chế lấy dữ liệu | Format Trả về |
| --- | --- | --- | --- |
| **Thị trường & Kỹ thuật** | vnstock, TCBS Endpoint | REST API, WebSocket | JSON, Pandas DataFrame |
| **Cơ bản & BCTC** | FiinTrade API, SSI FastConnect | Database / REST API | JSON |
| **Vĩ mô Toàn cầu** | yfinance, TradingEconomics | SDK, Web Scraper | JSON, Float |
| **Tin tức & Cộng đồng** | RSS CafeF, FireAnt API, F247 | XML Parsing, HTML Scrape | Text, Sentiment Score (-1 to 1) |

---

## 5. Chiến lược Lưu trữ & Tối ưu (Storage & Optimization)

Để đảm bảo hiệu năng (Low Latency) và tránh bị block IP từ các nhà cung cấp dữ liệu nội địa:

* **Hot Cache (Redis):**
* Dữ liệu `get_market_data` (Giá realtime): TTL = 60s.
* Dữ liệu `get_technical_indicators`: TTL = 5m - 15m.
* BCTC và Vĩ mô: TTL = 24h.


* **Vector Database (Qdrant / ChromaDB):** Lưu trữ embedding của các báo cáo phân tích chuyên sâu PDF (SSI Research, HSC) và tin tức vĩ mô dài hạn để Agent thực hiện query RAG lấy context.
* **Rate Limiting:** Triển khai hàng đợi (Queue) bằng Celery/RabbitMQ cho các tác vụ Web Scraping nặng để tránh gây ngẽn mạng hoặc kích hoạt cơ chế Anti-DDoS của website đích.