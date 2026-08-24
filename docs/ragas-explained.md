# RAGAS — Retrieval-Augmented Generation Assessment

## RAGAS là gì?

RAGAS (Retrieval-Augmented Generation Assessment) là framework đánh giá RAG pipeline **không cần ground-truth labels** (reference-free evaluation). Thay vì cần người chấm, RAGAS dùng LLM làm judge để tính các metric tự động.

GitHub: https://github.com/explodinggradients/ragas

---

## Tại sao cần RAGAS?

### Vấn đề khi không có RAGAS

Đánh giá RAG pipeline bằng tay:
- Tốn thời gian label dataset
- Không scale được khi thay đổi pipeline (chunk size, retriever, prompt…)
- Kết quả chủ quan, khó reproduce

### RAGAS giải quyết gì

Cho phép đo **tự động, lặp lại được** trên toàn pipeline — từ retrieval đến generation.

---

## 4 Metric cốt lõi

| Metric | Đo gì | Input cần |
|---|---|---|
| **Faithfulness** | Answer có bịa đặt không? (grounded in context) | question, answer, contexts |
| **Answer Relevancy** | Answer có trả lời đúng question không? | question, answer |
| **Context Precision** | Các chunk retrieved có liên quan không? | question, contexts, ground_truth |
| **Context Recall** | Retriever có lấy đủ context cần thiết không? | contexts, ground_truth |

> **Faithfulness** và **Answer Relevancy** không cần ground_truth → dễ dùng nhất.

---

## Tại sao cần trong hệ thống RAG của chúng ta?

### 1. Đo baseline trước khi thay đổi

Trước khi thay chunk strategy, retriever, hay prompt — đo RAGAS score làm baseline. Sau khi thay → đo lại → có số cụ thể để so sánh.

### 2. Phát hiện regression

Pipeline thay đổi nhỏ có thể làm faithfulness giảm mà mắt thường không nhận ra. RAGAS chạy tự động phát hiện được.

### 3. Đo nhiễu của evaluator (Bài 5)

Chạy cùng test set nhiều lần → tính `std(score)` → biết cái cân (RAGAS) nhiễu bao nhiêu. Chỉ tin kết quả A/B khi diff > noise floor.

```
Ví dụ:
- RAGAS faithfulness std = ±0.03 trên cùng input
- A/B test: v1=0.72, v2=0.74 → diff=0.02 < noise → KHÔNG có nghĩa
- A/B test: v1=0.72, v2=0.81 → diff=0.09 > noise → CÓ nghĩa
```

### 4. CI/CD cho RAG

Tích hợp RAGAS vào pipeline test: mỗi lần thay đổi code/config → tự động chạy eval → gate nếu score drop.

---

## Cách dùng cơ bản

```python
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
from datasets import Dataset

# Chuẩn bị data
data = {
    "question": ["Tyme Bank có bao nhiêu chi nhánh?"],
    "answer": ["Tyme Bank có 500 điểm giao dịch."],
    "contexts": [["Tyme Bank vận hành hơn 500 điểm giao dịch tại các siêu thị Pick n Pay..."]],
    "ground_truth": ["Tyme Bank có hơn 500 điểm giao dịch."]
}

dataset = Dataset.from_dict(data)

result = evaluate(
    dataset,
    metrics=[faithfulness, answer_relevancy, context_precision, context_recall]
)

print(result)
# {'faithfulness': 0.95, 'answer_relevancy': 0.88, ...}
```

---

## Giới hạn cần biết

| Giới hạn | Giải thích |
|---|---|
| LLM-as-judge bias | RAGAS dùng LLM để chấm → kế thừa bias của LLM đó |
| Chi phí | Mỗi lần evaluate = nhiều LLM calls → tốn tiền |
| Nhiễu (Bài 5) | Score không deterministic → cần đo variance trước khi tin |
| Context length | Chunk quá dài → LLM judge có thể miss |

---

## Tóm tắt

```
RAGAS = công cụ đo lường RAG pipeline tự động
       + không cần human labels
       + 4 metric bao phủ retrieval & generation
       + cần đo noise của chính RAGAS trước khi dùng để quyết định
```
