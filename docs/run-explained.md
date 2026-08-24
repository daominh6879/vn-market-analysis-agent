# `evals/run.py` — Giải thích từng dòng

## Tổng quan

File này là **eval runner** cho RAG pipeline — chạy câu hỏi golden qua LLM, chấm điểm bằng RAGAS (dùng Ollama làm judge), và so sánh với baseline để phát hiện regression.

---

## Phần 1: Shebang & docstring (dòng 1–10)

```python
#!/usr/bin/env python3
```
Shebang — cho phép chạy trực tiếp `./run.py` trên Unix mà không cần gõ `python3`.

```python
"""
evals/run.py — Eval runner for RAG pipeline.
Usage: ...
"""
```
Docstring module — mô tả mục đích và các cách gọi CLI.

---

## Phần 2: Standard library imports (dòng 11–19)

```python
from __future__ import annotations
```
Cho phép dùng `list[dict]`, `tuple[str, ...]` trong type hint mà không cần `List`, `Tuple` từ `typing` (Python 3.9 backport).

```python
import argparse   # parse CLI arguments (--questions, --skip-ragas, ...)
import json       # đọc/ghi baseline.json, results.json
import os         # đọc env vars (LLM_PROVIDER, OLLAMA_MODEL)
import sys        # sys.path, sys.exit(1) khi regression
import types      # tạo module giả để patch ragas
import time       # đo thời gian mỗi câu hỏi
from pathlib import Path  # xử lý đường dẫn file cross-platform
```

---

## Phần 3: Patch ragas import bug (dòng 21–30)

```python
from langchain_google_vertexai import ChatVertexAI as _ChatVertexAI
from langchain_google_vertexai import VertexAI as _VertexAI
```
Import class thật từ package chính thức của Google — `langchain-google-vertexai`.

```python
_cv = types.ModuleType("langchain_community.chat_models.vertexai")
_cv.ChatVertexAI = _ChatVertexAI
sys.modules.setdefault("langchain_community.chat_models.vertexai", _cv)
```
**Tại sao cần?** `ragas 0.4.x` có bug: hard-import `from langchain_community.chat_models.vertexai import ChatVertexAI` — module này đã bị xóa khỏi `langchain-community` ở các phiên bản mới. Fix: tạo module giả với tên đó, gắn class thật vào, inject vào `sys.modules` **trước khi** ragas import. `setdefault` đảm bảo không ghi đè nếu module đã tồn tại.

```python
_lv = types.ModuleType("langchain_community.llms.vertexai")
_lv.VertexAI = _VertexAI
sys.modules.setdefault("langchain_community.llms.vertexai", _lv)
```
Tương tự cho `VertexAI` (text completion model).

> **Lưu ý:** Project chỉ dùng Ollama làm judge, không dùng VertexAI. Fix này chỉ để ragas import thành công.

---

## Phần 4: YAML & project path (dòng 32–45)

```python
import yaml
```
Đọc file câu hỏi golden (`.yaml`).

```python
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
```
`__file__` = `.../evals/run.py` → `parent.parent` = project root. Insert vào đầu `sys.path` để `import llm.factory` hoạt động dù chạy từ bất kỳ thư mục nào.

```python
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
```
Load biến môi trường từ `.env` (API key, LLM_PROVIDER, ...). `try/except` để không crash nếu `python-dotenv` chưa install.

```python
from llm.factory import create_client
from llm.types import Message
```
Import LLM client factory của project — tạo client phù hợp với `LLM_PROVIDER` env var (anthropic, openai, ...).

---

## Phần 5: Constants (dòng 49–62)

```python
THRESHOLD_DROP = 0.05
```
Ngưỡng regression CI: nếu metric nào drop > 5% so với baseline → fail (`sys.exit(1)`).

```python
REFUSAL_KEYWORDS = [
    "không có", "không tìm thấy", ...
    "i don't know", "cannot find", ...
]
```
Danh sách keyword để detect câu trả lời từ chối. Cả tiếng Việt lẫn tiếng Anh vì model có thể trả lời bằng hai ngôn ngữ.

```python
METRIC_DISPLAY = {
    "faithfulness": "faithfulness         (có bịa không)",
    ...
}
```
Map tên metric kỹ thuật → label dễ đọc khi in bảng kết quả. Phần tiếng Việt trong ngoặc giải thích ý nghĩa ngắn gọn.

---

## Phần 6: Helper functions (dòng 66–83)

### `load_questions(path)`
```python
def load_questions(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]
```
Đọc file YAML, trả về list câu hỏi. `safe_load` thay `load` để tránh arbitrary code execution từ YAML.

### `ask_baseline(client, question)`
```python
def ask_baseline(client, question: str) -> tuple[str, list[str]]:
    resp = client.generate(
        [Message(role="user", content=question)],
        max_tokens=512,
        system="Bạn là trợ lý tài chính...",
    )
    return resp.text, []
```
Gọi LLM **không có RAG context** — chỉ dùng kiến thức của model. Trả về `(answer, contexts)` — contexts rỗng vì không có retrieval. System prompt giới hạn domain tài chính và yêu cầu từ chối nếu không có thông tin.

> Tên "baseline" ở đây nghĩa là "không có RAG", không phải "gọi file baseline.json".

### `is_refusal(answer)`
```python
def is_refusal(answer: str) -> bool:
    a = answer.lower()
    return any(k in a for k in REFUSAL_KEYWORDS)
```
Check xem answer có chứa keyword từ chối không. `.lower()` để match case-insensitive.

---

## Phần 7: RAGAS scoring (dòng 88–141)

### `_make_ragas_llm(ollama_model)`
```python
def _make_ragas_llm(ollama_model: str):
    try:
        from langchain_ollama import ChatOllama, OllamaEmbeddings
        ...
    except ImportError:
        from langchain_community.chat_models import ChatOllama
        ...
    return chat, emb
```
Tạo LangChain Ollama client cho RAGAS. `try/except` để support cả `langchain-ollama` (package mới) lẫn `langchain-community` (package cũ).

### `compute_ragas(samples, ollama_model)`

```python
chat, emb = _make_ragas_llm(ollama_model)
llm = LangchainLLMWrapper(chat)
embeddings = LangchainEmbeddingsWrapper(emb)
```
Wrap LangChain client vào RAGAS wrapper — RAGAS có interface riêng, cần wrap để dùng.

```python
try:
    from ragas.metrics.collections import (Faithfulness, ...)
except ImportError:
    from ragas.metrics import (Faithfulness, ...)  # ragas < 0.4
```
Compat import — RAGAS thay đổi vị trí module giữa các version.

```python
metrics = [
    Faithfulness(llm=llm),
    ResponseRelevancy(llm=llm, embeddings=embeddings),
    ContextPrecision(llm=llm),
    ContextRecall(llm=llm),
]
```
4 metrics RAGAS tiêu chuẩn:
- **Faithfulness**: answer có bịa ngoài context không?
- **ResponseRelevancy**: answer có đúng câu hỏi không?
- **ContextPrecision**: chunk xếp hạng cao có thực sự liên quan không?
- **ContextRecall**: context có đủ thông tin để trả lời không?

```python
dataset = Dataset.from_dict({
    ...
    "contexts": [s["contexts"] or ["[no context — baseline mode]"] for s in samples],
    ...
})
```
RAGAS yêu cầu `contexts` không rỗng. Baseline mode không có retrieval → dùng placeholder string.

```python
run_config = RunConfig(timeout=600, max_workers=2)
```
`timeout=600`: Ollama local chậm, cho 10 phút mỗi request. `max_workers=2`: giới hạn concurrent calls để không overwhelm Ollama.

```python
df = result.to_pandas()
metric_cols = [c for c in df.columns if c not in ("question", "answer", "contexts", "ground_truth")]
return {c: float(df[c].mean()) for c in metric_cols if df[c].dtype.kind in "fi"}
```
`EvaluationResult` của RAGAS không có `.items()` → convert sang pandas DataFrame, lấy mean của từng metric column. `dtype.kind in "fi"` = chỉ lấy column float hoặc int (bỏ string columns).

---

## Phần 8: Output & regression check (dòng 146–171)

### `print_markdown_table(scores)`
```python
print(f"| {'Metric':<40} | {'Score':>6} |")
```
In bảng markdown. `:<40` = left-align, pad đến 40 chars. `:>6` = right-align, pad đến 6 chars. Dùng `METRIC_DISPLAY` để hiển thị tên dễ đọc.

### `regression_check(current, baseline, threshold)`
```python
drop = float(base) - curr
if drop > threshold:
    failures.append(...)
```
So sánh từng metric. `drop = base - curr` — positive drop = metric giảm. Chỉ fail nếu giảm quá ngưỡng, không fail nếu tăng.

---

## Phần 9: `main()` (dòng 176–285)

### CLI args (dòng 177–190)
```python
--questions   # file YAML chứa câu hỏi golden (default: evals/golden_hpg.yaml)
--out         # file JSON output kết quả (default: evals/results.json)
--baseline    # file JSON baseline để so sánh (default: evals/baseline.json)
--save-baseline   # flag: lưu kết quả hiện tại thành baseline mới
--ollama-model    # model Ollama dùng làm judge (default: llama3, hoặc env OLLAMA_MODEL)
--skip-ragas      # flag: bỏ qua RAGAS scoring, chỉ gọi model
--only-refusal    # flag: chỉ chạy câu hỏi out_of_scope (CI nhanh)
```

### Phân loại câu hỏi (dòng 200–201)
```python
eval_qs    = [q for q in questions if q["group"] not in ("no_answer", "out_of_scope")]
refusal_qs = [q for q in questions if q["group"] in ("no_answer", "out_of_scope")]
```
Tách 2 nhóm: câu hỏi bình thường (chấm RAGAS) và câu hỏi model phải từ chối (chấm pass/fail).

### Regression check
```python
failures = regression_check(all_scores, baseline, THRESHOLD_DROP)
if failures:
    sys.exit(1)
else:
    print("\n✅  No regression (all metrics within threshold)")
```
So sánh với baseline. `sys.exit(1)` để CI pipeline fail khi có regression.

---

## Luồng thực thi tóm tắt

```
golden_hpg.yaml
       │
       ├── eval_qs ──────► ask_baseline() ──► LLM answer ──► samples[]
       │                                                          │
       │                                                    compute_ragas()
       │                                                    (Ollama judge)
       │                                                          │
       └── refusal_qs ──► ask_baseline() ──► is_refusal() ──► refusal_rate
                                                                  │
                                              all_scores = ragas + refusal_rate
                                                                  │
                                              regression_check vs baseline.json
                                                                  │
                                              ✅ pass / ❌ sys.exit(1)
```

---

## Cách dùng nhanh

```bash
# Lần đầu: tạo baseline
python evals/run.py --save-baseline

# CI: so sánh vs baseline
python evals/run.py

# Nhanh: chỉ check refusal, không cần Ollama
python evals/run.py --only-refusal --skip-ragas

# Dùng model khác làm judge
python evals/run.py --ollama-model mistral
```
