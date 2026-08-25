# Bài 15 — Ghép hai loại kết quả (Hybrid Fusion)

**Pipeline:** hpg_structural + BM25 vn + vector bge-m3  
**Script:** `evals/eval_fusion.py` + `evals/run.py --retriever hybrid_rrf`

---

## Vấn đề cộng thẳng hai loại điểm

```python
bm25_score   = 12.4   # BM25 magnitude — phụ thuộc corpus, không bounded
cosine_score = 0.72   # cosine similarity — bounded [0, 1]
sum          = 13.12  # BM25 chi phối hoàn toàn — cosine đóng góp <6%
```

**Giải pháp:**
- **Weighted-sum:** chuẩn hoá cả hai về [0,1] trước khi cộng: `alpha * bm25_norm + (1-alpha) * vec_norm`
- **RRF:** bỏ điểm hoàn toàn, chỉ dùng thứ hạng: `score = Σ 1/(k + rank_i)`, k=60 (Cormack 2009)

---

## Kết quả eval_fusion.py

### Run 1 — candidate_k=20 (collection=hpg_structural)

| Cấu hình | hit@5 | hit@10 | hit@20 |
|---|---|---|---|
| BM25 vn | 9/21 | 11/21 | 12/21 |
| Vector | 11/21 | 14/21 | **17/21** |
| Weighted-sum (alpha=0.5) | 13/21 | 15/21 | 16/21 |
| RRF (k=60) | **13/21** | **15/21** | 16/21 |

fusion@20 < vector@20 (16 vs 17) — regression. Nguyên nhân: BM25 top-ranked sai đẩy chunk vector đúng ra khỏi top-20.

### Run 2 — candidate_k=30 (collection=hpg_structural) ← **final**

| Cấu hình | hit@5 | hit@10 | hit@20 |
|---|---|---|---|
| BM25 vn | 9/21 | 11/21 | 12/21 |
| Vector | 11/21 | 14/21 | 17/21 |
| **Weighted-sum (alpha=0.5)** | **13/21** | **15/21** | **17/21** |
| RRF (k=60) | 11/21 | 14/21 | 17/21 |

**Fusion@20 = vector@20 = 17/21** — không còn regression.

### Per-group hit@5 (candidate_k=30)

| Group | bm25_vn | vector | weighted_sum | rrf |
|---|---|---|---|---|
| table_lookup (10 câu) | 3/10 | 5/10 | **6/10** | 5/10 |
| text_interpretation (11 câu) | 6/11 | 6/11 | **7/11** | 6/11 |

---

## Phân tích

**Tại sao RRF tụt từ 13→11 khi candidate_k=30?**  
RRF score rank 25-30 = `1/(60+27) ≈ 0.011` — nhỏ nhưng đủ đẩy chunk vector tốt khỏi top-5. RRF nhạy với candidate_k vì không normalize. Weighted-sum normalize toàn bộ 30 candidates về [0,1] — chunk BM25 yếu (rank 25-30) bị collapse về ~0, không gây nhiễu.

**Tại sao fusion@20 (k=20) < vector@20?**  
Với candidate_k=20: gộp 40 unique docs, lấy top-20 → BM25 sai ở rank cao đẩy chunk vector rank 17-20 ra ngoài. Với candidate_k=30: vector đóng góp nhiều chunk hơn vào pool, fusion@20 recover đủ để bằng vector@20.

**Giá trị thực của fusion:** hit@5 +2 so với vector đơn lẻ (13 vs 11). Đây là level LLM nhận context — quan trọng hơn @20.

---

## Lựa chọn: **weighted_sum, candidate_k=30, alpha=0.5**

**Lý do:**
- hit@5: 13/21 vs RRF 11/21 tại k=30 — weighted-sum ổn định hơn khi pool lớn
- table_lookup: 6/10 (tốt hơn RRF 5/10) — nhóm câu tra số tài chính
- text_interpretation: 7/11 (tốt hơn RRF 6/11)
- fusion@20 không regression (bằng vector@20 = 17/21)

---

## Lệnh chạy

```bash
# Eval fusion (context_hit@k, không gọi LLM)
uv run python evals/eval_fusion.py

# Eval RAGAS đầy đủ với hybrid_rrf
uv run python evals/run.py \
  --retriever hybrid_rrf \
  --collection hpg_structural \
  --vn-tokenize \
  --skip-ragas \
  --out evals/hybrid_rrf.json

# Eval RAGAS đầy đủ với hybrid_weighted
uv run python evals/run.py \
  --retriever hybrid_weighted \
  --collection hpg_structural \
  --vn-tokenize \
  --skip-ragas \
  --out evals/hybrid_weighted.json
```

---

## Tự trả lời được

**Nếu cộng thẳng thì bên nào chi phối và vì sao?**  
BM25 chi phối — scores BM25 có magnitude ~12 (unbounded), cosine ~0.7 (bounded [0,1]). Khi cộng thẳng, BM25 chiếm 94%+ tổng điểm. Cosine gần như không ảnh hưởng đến ranking.

**Ghép bằng thứ hạng (RRF) có ưu điểm gì mà số liệu không thể hiện?**  
RRF robust với outliers: một chunk BM25 score cực cao không kéo lệch toàn bộ ranking vì score bị bỏ. Chỉ rank mới đóng góp — top-1 BM25 và top-1 vector đều được RRF score ~1/61, không phụ thuộc magnitude. Thêm nữa, RRF không cần tune alpha — một hyperparameter ít hơn cần validate.
