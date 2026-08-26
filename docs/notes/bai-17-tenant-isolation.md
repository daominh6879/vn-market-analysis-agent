# Bài 17 · Nhiều khách hàng dùng chung: tự tấn công chính mình

## Bối cảnh thực tế của project

Project này chỉ có **1 tenant** (HPG). Bài 17 là bài luyện kỹ thuật — giả lập tình huống production multi-tenant để nắm pattern trước khi cần. `TENANT_A` / `TENANT_B` trong test là fixture giả, không phản ánh data thật.

---

## Thiết kế

**Collection:** `test_tenant_isolation_b17` (test) / `chunks` (production — chung 1 collection, phân biệt bằng `tenant_id` payload)

**Nguyên tắc cốt lõi:** filter `tenant_id` **bên trong Qdrant lúc search**, không lọc sau trong Python.

---

## Hai điểm rò rỉ cần vá

### 1. Qdrant — filter tại query time

```python
# ĐÚNG: Qdrant chỉ trả về chunk của tenant này
results = qdrant_client.search(
    collection_name="chunks",
    query_vector=query_embedding,
    query_filter=Filter(must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]),
    limit=20,
)

# SAI: fetch tất cả, lọc sau trong Python
all_results = qdrant_client.search(..., limit=20)  # không có filter
filtered = [r for r in all_results if r.payload["tenant_id"] == tenant_id]
```

**Vấn đề của lọc sau:**
- **Bảo mật:** dữ liệu tenant khác đi vào bộ nhớ ứng dụng trước khi bị loại bỏ.
- **Chất lượng:** nếu 4/5 kết quả top thuộc tenant khác, người dùng nhận được 1 kết quả thay vì 5.

### 2. Redis cache — prefix bắt buộc bằng tenant_id

```python
# SAI: key chỉ là hash của query → tenant B nhận câu trả lời của tenant A
cache_key = hashlib.md5(query.encode()).hexdigest()

# ĐÚNG: prefix bằng tenant_id
cache_key = f"{tenant_id}:{hashlib.md5(query.encode()).hexdigest()}"
```

**Ví dụ rò rỉ thật:** tenant A hỏi "Doanh thu HPG Q1 2024?" → cache lưu key `abc123` → tenant B hỏi đúng câu đó → nhận ngay "165.000 tỷ đồng" của tenant A.

---

## 3 test cases

| Test | Phương pháp | Kết quả kỳ vọng |
|---|---|---|
| `test_a_cannot_see_b_chunks` | `scroll_tenant(TENANT_A)` | Chỉ 3 chunk của A, không có B |
| `test_a_searching_b_content_returns_nothing` | `search(TENANT_A, vec_of_B, top_k=5)` | Kết quả không chứa "2025" hay "280.000" |
| `test_cache_is_isolated` | So sánh `make_cache_key(A, q)` vs `make_cache_key(B, q)` | Key khác nhau; unsafe key bị exploit bằng dict |

**Bonus test:** `test_post_filter_silently_reduces_results` — chứng minh post-filter trả về ít kết quả hơn query-time filter khi top results thuộc tenant khác.

---

## Fixture design

- Tenant A: HPG 2024 — doanh thu 165.000 tỷ, biên 13,3%, ROE 18,5%
- Tenant B: HPG 2025 — doanh thu 280.000 tỷ, biên 11,1%, ROE 21,0%
- Vector: dummy 4-dim (test logic lọc, không cần Ollama)
- Không cần Ollama để chạy — chỉ cần Qdrant tại localhost:6333

---

## Lệnh chạy

```bash
uv run pytest tests/test_tenant_isolation.py -v
```

**Kết quả kỳ vọng:** 4 test xanh (3 bài bắt buộc + 1 demo post-filter).

---

## Câu trả lời — tự kiểm tra

**Lọc sau gây 2 vấn đề:**
1. Bảo mật: chunk của tenant khác vào bộ nhớ app.
2. Chất lượng: top_k thực tế < top_k yêu cầu, không có cảnh báo.

**Chỗ rò rỉ dễ bị bỏ qua nhất:** Redis cache key không có tenant prefix — không ai nghĩ đến cache khi debug data leak.
