# Bài 20 · Hợp đồng lỗi — ToolResult

## Kết quả

- **93 tests xanh** (25 chaos + 68 tool tests), 0 fail.
- Mọi tool đều trả `ToolResult` — không raise ra ngoài, không trả empty list trần.

## Thay đổi

| File | Thay đổi |
|------|----------|
| `tools/result.py` | Mới. `ToolResult(status, data, message)` — Pydantic BaseModel |
| `tools/price.py` | 7 public function → trả `ToolResult`. Thêm `_map_upstream_error` helper |
| `tools/cli.py` | Unpack `result.data`, in `result.message` khi lỗi |
| `tools/registry.py` | Mới. Metadata (version, timeout, cost_hint, side_effect) cho 7 tool |
| `tests/test_tool_chaos.py` | Mới. 5 tình huống lỗi thực tế |
| `tests/test_tools.py` | Cập nhật toàn bộ assertions → check `result.status` và `result.data` |

## 5 Status trong ToolResult

```
ok            — thành công, data có giá trị
no_data       — request hợp lệ, nhưng không có dữ liệu (mã không tồn tại, ngày lễ)
invalid_input — tham số đầu vào sai (ticker rỗng, days < 1)
upstream_error — server lỗi (HTTP 500, timeout, connection refused)
rate_limited  — vượt giới hạn request (HTTP 429)
```

## Logic map exception → status

```python
ValueError từ provider → no_data
"429" / "rate" / "too many" trong message → rate_limited
"timeout" / "timed out" → upstream_error (message: thử lại sau X phút)
"500" / "server error" → upstream_error (message: lỗi tạm thời phía server)
Exception khác → upstream_error
```

## Nguyên nhân agent lặp vô hạn

Tool trả `[]` hoặc raise exception → agent không biết làm gì → gọi lại cùng tool, cùng tham số → loop mãi mãi.

Fix: trả `ToolResult(status="no_data", message="Không có tin tức về HPG trong 7 ngày. **Tăng khoảng thời gian** hoặc thử mã khác.")` → agent đọc message và biết phải đổi chiến lược.

## Quy tắc viết message

Viết như hướng dẫn đồng nghiệp mới:
- ❌ "có lỗi xảy ra"
- ✓ "Timeout khi kết nối. Thử lại sau 1–2 phút. Không cần đổi tham số."
- ✓ "Không có tin tức về HPG trong 7 ngày. Tăng khoảng thời gian (days) hoặc thử mã khác."

## Chaos scenarios (test_tool_chaos.py)

1. **Ticker không tồn tại** → `ValueError("mã không tồn tại")` → `no_data`
2. **Ngày lễ** → `ValueError("ngày lễ — không có phiên")` → `no_data`
3. **Server HTTP 500** → `ConnectionError("HTTP 500")` → `upstream_error`
4. **Timeout** → `TimeoutError("timed out")` → `upstream_error`
5. **Rate limited** → `Exception("429 Too Many Requests")` → `rate_limited`

## Lệnh chạy lại

```bash
python -m pytest tests/test_tool_chaos.py tests/test_tools.py -v
```
