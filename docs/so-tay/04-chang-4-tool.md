# CHẶNG 4 · Tool (tuần 6–7)

> Ngắn, nhưng bài 20 là một trong ba bài dạy nhiều nhất trong cả sổ tay.

---

### Bài 19 · Ba tool và dùng thử **không cần agent** 🔴
**~1.5 ngày**

**Bối cảnh.** Agent sẽ tự động gọi các tool này ở bài sau. Nhưng nếu tool sai (trả NaN im lặng, sai giá chưa điều chỉnh), debug qua agent rất khó. Bài này xây và kiểm tra 3 tool độc lập, hoàn toàn trước khi cho agent chạm vào.

**Để hiểu gì.** Tool phải chạy đúng trước khi để model gọi. Debug một tool sai *thông qua* agent là cơn ác mộng.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo cấu trúc thư mục và cài dependencies:
   ```bash
   mkdir -p tools tests
   uv add vnstock pandas-ta pytest
   ```
2. Viết hàm đầu tiên `get_realtime_price` trong `tools/price.py` — chưa có decorator agent, chỉ là Python thuần. Chạy thử ngay:
   ```bash
   python -c "from tools.price import get_realtime_price; print(get_realtime_price('FPT'))"
   ```
3. Kiểm tra CLI hoạt động trước khi viết tiếp:
   ```bash
   python -m tools.cli price FPT
   ```

**Chi tiết từng việc:**

- **Viết `get_realtime_price(ticker)`** — dùng `vnstock`. Bọc sau `PriceProvider` interface để có thể swap source.

- **Viết `get_historical_ohlcv(ticker, days)`** — trả `pd.DataFrame` với cột O, H, L, C, V. Đảm bảo không có ngày trùng.

- **Viết `calculate_indicators(df)`** — dùng `pandas-ta` tính RSI(14), MACD(12,26,9), MA(20), MA(50). **Không trả số thô**, trả text mô tả để model hiểu ngữ cảnh:
  ```python
  def calculate_indicators(df: pd.DataFrame) -> str:
      rsi = df.ta.rsi(length=14).iloc[-1]
      if pd.isna(rsi):
          return "RSI: không đủ dữ liệu (cần ít nhất 14 phiên)"
      label = "quá mua" if rsi > 70 else "quá bán" if rsi < 30 else "trung tính"
      return f"RSI(14) = {rsi:.1f} → vùng {label}"
  ```
  Đối chiếu RSI tính được với TradingView/CafeF cho cùng mã, cùng ngày — sai lệch dưới 0.5 là chấp nhận được.

- **Viết `tools/cli.py`:**
  ```bash
  python -m tools.cli price FPT
  python -m tools.cli ohlcv HPG 30
  python -m tools.cli indicators VNM
  ```

- **Viết unit test trong `tests/test_tools.py`** — mock hoàn toàn, không gọi mạng.

**Xong khi.**
- [ ] CLI chạy cho 5 mã (gồm 2 mã thanh khoản thấp) → kết quả hợp lý
- [ ] RSI của bạn đối chiếu với TradingView/CafeF → sai lệch nhỏ
- [ ] Test xanh, không cần mạng

**Tự trả lời được.**
- Thử với **mã mới lên sàn** (dưới 14 phiên) — hàm trả về gì?
- Vì sao trả về text mô tả tốt hơn trả về số thô cho model đọc?

**Cái bẫy.** `pandas-ta` trả `NaN` im lặng khi không đủ dữ liệu. `NaN` đi vào prompt sẽ khiến model bịa — và bạn sẽ tìm lỗi ở prompt trong khi lỗi ở dữ liệu.

---

### Bài 20 · Hợp đồng lỗi — chữa 90% ca agent lặp vô hạn 🔴
**~1.5 ngày**

**Bối cảnh.** Khi tool trả về `[]` hoặc raise exception, agent không biết phải làm gì — nên nó gọi lại đúng tool đó với đúng tham số đó, mãi mãi. Bài này định nghĩa một "hợp đồng lỗi" chuẩn: mọi tool đều trả `ToolResult` với `status` và `message` hướng dẫn agent bước tiếp theo.

**Để hiểu gì.** Nguyên nhân thật của agent lặp vô hạn hầu như **không phải prompt kém**. Đây là bài học phản trực giác nhất trong sổ tay.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo `tools/result.py` với class `ToolResult`.
2. Kiểm tra import không lỗi:
   ```bash
   python -c "from tools.result import ToolResult; print(ToolResult(status='ok', data=42, message='oke'))"
   ```
3. Sửa `get_realtime_price` để trả `ToolResult` thay vì `float`.

**Chi tiết từng việc:**

- **Định nghĩa `ToolResult` trong `tools/result.py`:**
  ```python
  from typing import Any, Literal
  from pydantic import BaseModel

  class ToolResult(BaseModel):
      status: Literal["ok", "no_data", "invalid_input", "upstream_error", "rate_limited"]
      data: Any | None
      message: str  # câu hướng dẫn agent làm gì tiếp — viết như đang nói với người
  ```

- **Sửa cả 3 tool từ bài 19** để luôn trả `ToolResult`, không bao giờ raise ra ngoài, không bao giờ trả list rỗng trần. **Quy tắc viết `message`:** viết như đang hướng dẫn đồng nghiệp mới — nói rõ *đừng làm gì* và *nên làm gì thay thế*. "Có lỗi xảy ra" là message vô dụng.

- **Ép 5 tình huống lỗi thực tế** trong `tests/test_tool_chaos.py`: ticker không tồn tại, ngày lễ không có dữ liệu, server HTTP 500, timeout, rate limited.

- **Viết `tools/registry.py`** — khai báo metadata (version, timeout, cost_hint, side_effect) cho mỗi tool.

**Xong khi.**
- [ ] 5 tình huống → **5 status khác nhau**, không tình huống nào raise ra ngoài
- [ ] `tests/test_tool_chaos.py` xanh
- [ ] Không tool nào trả list rỗng mà không kèm `status` và `message`

**Tự trả lời được.**
- Vì sao trả `[]` khiến agent lặp, còn trả `no_data` kèm hướng dẫn thì không?
- Nếu `message` viết là "có lỗi xảy ra" thì model làm gì tiếp?

**Cái bẫy.** Rất muốn viết `message` chung chung. Nhưng nó là thứ **duy nhất** model đọc để quyết định bước tiếp — viết như đang hướng dẫn một đồng nghiệp mới, không như đang log lỗi.

---

### Bài 21 · Đóng gói thành MCP server 🟡
**~1.5 ngày**

**Bối cảnh.** Tool hiện tại được import trực tiếp vào agent. MCP (Model Context Protocol) cho phép expose tool qua một server riêng: agent chỉ biết interface, không import code. Bài này là bước tách biên giới tool/agent.

**Để hiểu gì.** Ý nghĩa của việc tách biên giới tool/agent — tool dùng lại được cho client khác. Và một bề mặt bảo mật mới.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Cài MCP Python SDK: `uv add mcp`.
2. Chạy server thử — nếu thấy server khởi động được là đúng hướng.
3. Mở MCP Inspector để gọi tool bằng UI:
   ```bash
   npx @modelcontextprotocol/inspector python tools/mcp_server.py
   ```

**Chi tiết từng việc:**

- **Viết `tools/mcp_server.py`** — expose 3 tool qua MCP. Mô tả tool phải rõ ràng vì đây là thứ model đọc để quyết định có gọi không.

- **Dùng MCP Inspector kiểm tra từng tool** với ticker hợp lệ, ticker không tồn tại, ticker rỗng.

- **Cấu hình agent (bài 22 trở đi) làm MCP client** thay vì import trực tiếp. Sau khi chuyển, chạy lại eval để đảm bảo không tụt điểm.

**Xong khi.**
- [ ] MCP Inspector gọi được cả 3 tool, thấy đúng schema
- [ ] Agent gọi tool qua MCP thay vì import trực tiếp, eval **không tụt**

**Tự trả lời được.**
- Bạn được gì và mất gì so với gọi hàm trực tiếp?
- Vì sao mô tả tool của một MCP server bên thứ ba là một bề mặt prompt-injection?

**Cái bẫy.** Nếu đang trễ, bỏ bài này. Bài 20 quan trọng hơn bài 21 rất nhiều.
