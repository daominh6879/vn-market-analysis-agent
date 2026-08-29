# Bài 30 · Đo memory + test rò rỉ giữa người dùng

## Mục tiêu

Đo 2 chỉ số memory sau 3 conversation liên tiếp:
- **memory_recall** — nhớ đúng thứ người dùng đã nói
- **memory_precision** — không bịa thứ chưa từng được nói

Cộng thêm 5 test isolation ở tầng data:
1. user_memory cross-user
2. conversation history cross-conversation
3. Hai user có sở thích giống nhau không lẫn
4. Cross-tenant isolation
5. load_history chỉ trả messages của đúng conversation

## Files

| File | Mô tả |
|------|-------|
| `evals/memory_multi_session.yaml` | 10 kịch bản × 3 conversation |
| `evals/eval_memory_b30.py` | Runner tính recall/precision (real LLM) |
| `tests/test_memory_isolation.py` | 5 isolation tests (DB-layer, không cần LLM) |

## Chạy

```bash
# Isolation tests (nhanh, không tốn API)
pytest tests/test_memory_isolation.py -v

# Eval full (real LLM, tốn ~30 LLM calls)
python evals/eval_memory_b30.py

# Eval + cập nhật notes
python evals/eval_memory_b30.py --update-notes
```

## Kết quả eval

*(Append tự động bởi eval_memory_b30.py --update-notes)*

## Tại sao precision nguy hiểm hơn recall khi thấp

- **Recall thấp**: agent không nhớ sở thích → trả lời generic, người dùng nhận ra ngay.
- **Precision thấp**: agent *bịa* sở thích chưa từng nói → đưa khuyến nghị sai mà người dùng không hiểu tại sao. Với sản phẩm tài chính, điều này có thể dẫn đến quyết định đầu tư sai.

## Cách đo "không bịa"

Thiết kế kịch bản sao cho: nếu hệ thống hallucinate sở thích, một cụm từ cụ thể (`expected_NOT_mentioned`) sẽ xuất hiện trong reply. Ví dụ: user chỉ hỏi kỹ thuật, không bày tỏ sở thích → reply không được chứa "sở thích của bạn là..." hay "bạn thích ngành...".

## Eval run 2026-08-29 12:21

| Scenario | Recall | Precision |
|----------|--------|-----------|
| s01 | ✅ | ✅ |
| s02 | ✅ | ✅ |
| s03 | ✅ | ✅ |
| s04 | ✅ | ✅ |
| s05 | ✅ | ✅ |
| s06 | ✅ | ✅ |
| s07 | ✅ | ✅ |
| s08 | ✅ | ✅ |
| s09 | ✅ | ✅ |
| s10 | ✅ | ✅ |

**memory_recall = 100%**
**memory_precision = 100%**
