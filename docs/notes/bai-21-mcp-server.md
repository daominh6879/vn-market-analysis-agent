# Bài 21 · Đóng gói thành MCP server

## Kết quả

- **5 tool** exposed qua MCP: `get_price`, `get_ohlcv`, `get_indicators`, `search_news`, `get_market_sentiment`.
- Server khởi động không lỗi. MCP Inspector gọi được từng tool.
- `get_indicators` là tool tổng hợp (OHLCV + indicators nội bộ) — không cần client truyền DataFrame.

## Thay đổi

| File | Thay đổi |
|------|----------|
| `tools/mcp_server.py` | Mới — FastMCP server expose 5 tool |
| `requirements.txt` | Thêm `mcp>=1.0.0,<2` |

## Quyết định kỹ thuật

**Tại sao pin `mcp<2`?** — mcp 2.x đổi tên `FastMCP` → `MCPServer` và thay đổi import path. Pin v1 để dùng `from mcp.server.fastmcp import FastMCP` như tài liệu bài học.

**`get_indicators` nhận ticker+days thay vì DataFrame** — MCP không thể truyền DataFrame qua wire. Tool tự gọi `get_historical_ohlcv` nội bộ rồi tính indicators. Client không cần biết bước trung gian.

**Tại sao tag đơn vị tiền tệ?** — `_detect_provider` chọn `YFinanceProvider` cho mã quốc tế → tag `USD`; `VnstockProvider` → tag `VND`. Tránh model so sánh sai đơn vị.

## Cách chạy

```bash
# Khởi động MCP server (stdio transport, dùng cho Claude Desktop / MCP Inspector)
python tools/mcp_server.py

# Mở MCP Inspector để test từng tool qua UI
npx @modelcontextprotocol/inspector python tools/mcp_server.py
```

## Checklist

- [x] MCP Inspector gọi được cả 5 tool, thấy đúng schema
- [ ] Agent gọi tool qua MCP thay vì import trực tiếp (bài 22)
- [ ] Chạy lại eval sau khi chuyển agent sang MCP client — kiểm tra điểm không tụt

## Ghi chú

- Bài 22 sẽ cấu hình agent làm MCP client. Sau đó phải chạy lại eval để đảm bảo pipeline không tụt điểm.
- `_result_to_text` serialize `ToolResult.data` → string: DataFrame dùng `.to_string()`, các type khác dùng `str()`. Non-ok status trả `[status] message` để agent biết lỗi và không gọi lại ngay.
