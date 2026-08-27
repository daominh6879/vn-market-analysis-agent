# Bài 23 · Kế hoạch là dữ liệu có schema, không phải văn xuôi

## Kết quả

- `agents/planner.py`: `Step` + `Plan` Pydantic models, `validate_plan`, `default_plan`, `generate_plan`.
- 21 test xanh (`tests/test_planner.py`): validator, cycle detection, default plan, schema parse.
- Validator bắt 5 loại lỗi trước khi chạy bất kỳ bước nào.

## Thay đổi

| File | Thay đổi |
|------|----------|
| `agents/planner.py` | Mới. Schema `Step`/`Plan`, `validate_plan`, `_detect_cycles`, `default_plan`, `generate_plan` |
| `tests/test_planner.py` | Mới. 21 test: validate_plan (9), default_plan (4), _detect_cycles (6), schema parse (2) |

## Schema kế hoạch

```python
class Step(BaseModel):
    id: str
    intent: str
    executor: str        # tên tool hoặc agent
    depends_on: list[str]
    expected_output: str

class Plan(BaseModel):
    steps: list[Step]
    budget_tokens: int
```

Kế hoạch là JSON, không phải văn xuôi — code `validate_plan` được.

## 5 điều kiện validator kiểm tra

1. `len(steps) <= MAX_STEPS` (10)
2. `budget_tokens <= MAX_BUDGET_TOKENS` (20 000)
3. Mọi `depends_on` là id hợp lệ trong plan
4. Mọi `executor` có trong registry
5. Không có vòng lặp phụ thuộc (DFS cycle detection)

Nếu vi phạm bất kỳ điều kiện nào → validator trả danh sách lỗi cụ thể, không crash.

## Cái bẫy đã xác nhận

Model hay sinh `depends_on` trỏ vào id không tồn tại — đây là lỗi phổ biến nhất.
Điều kiện 3 bắt chính xác trường hợp này.

Validator cũng trả **tất cả lỗi** trong một lần kiểm tra (không dừng ở lỗi đầu tiên) để retry có đủ thông tin sửa.

## Retry logic

```
attempt 1: generate plan → validate
  nếu hợp lệ → dùng
attempt 2 (nếu lần 1 sai): generate với error list → validate
  nếu hợp lệ → dùng
fallback: dùng default_plan (sequential bài 22)
```

`default_plan` luôn pass validator vì dùng đúng executor từ REGISTRY.

## Test case bẫy vòng lặp

```
s1 depends_on [s2], s2 depends_on [s1] → "Circular dependency: 's1' -> 's2'"
s1 depends_on [s1]                      → "Circular dependency: 's1' -> 's1'" (self-loop)
a→c, b→a, c→b (3 node)                 → cycle detected
```

DFS dùng white/gray/black coloring — gray node gặp lại trong đường đi = cycle.
