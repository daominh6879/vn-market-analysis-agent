# VN-Stock Agentic Platform — Sổ tay thực hành

**43 bài · phủ đầy đủ từ dữ liệu tới vận hành · mỗi bài có cả thước đo "xong" và thước đo "hiểu".**

> File này thay thế mọi bản practice trước đó. Đi kèm: `interview-kit-v2.md` (51 câu tự kiểm tra).

---

## Cách dùng

Mỗi bài có 4 phần:

| Phần | Nghĩa |
|---|---|
| **Để hiểu gì** | Cái bạn *biết* sau bài này mà trước đó không biết |
| **Làm gì** | Việc cụ thể: file nào, hàm nào, lệnh nào |
| **Xong khi** | Điều kiện kiểm chứng được — chạy được hay không |
| **Tự trả lời được** | Câu hỏi chỉ có nghĩa **sau khi** đã làm. Đây là thước đo *hiểu* |
| **Cái bẫy** | Chỗ mất thời gian nếu không biết trước |

## Ba mức ưu tiên

| Mức | Nghĩa | Số bài |
|---|---|---|
| 🔴 **Lõi** | Dạy một khái niệm không thay thế được. Bỏ là mất thật | 31 |
| 🟡 **Mở rộng** | Giá trị thật, nhưng làm sau cũng được. Nhiều bài trong nhóm này nặng về thao tác hơn về hiểu | 9 |
| ⚪ **Khi cần đi phỏng vấn** | Tồn tại để thuyết phục người khác, không để bạn hiểu thêm | 3 |

**Nếu mục tiêu là học:** làm 31 bài 🔴 (~9–10 tuần), đọc qua 🟡 để biết nó tồn tại, bỏ ⚪.
**Nếu cần portfolio:** làm hết 43 bài (~15 tuần).

## Ba luật

1. **Được phép nhảy bài.** Đang làm bài 7 mà tò mò về memory thì cứ nhảy sang bài 28. Tò mò là lý do chính đáng, động lực quan trọng hơn thứ tự. *Ngoại lệ: bài 1–5 nên làm trước, vì 37 bài sau đều cần đo được.*
2. **Timebox.** Quá 1.5× thời gian gợi ý → làm bản đơn giản nhất cho chạy được, ghi vào `NOTES.md`, đi tiếp.
3. **Ghi số ngay lúc đo.** Không ghi thì tuần sau bạn không còn so được với gì.

## Trạng thái hiện tại

### Tiến độ

| Bài | Trạng thái |
|-----|------------|
| Bài 1–4 | ✅ Xong |
| Bài 5 | ✅ Xong |
| Bài 6 | ✅ Xong |
| Bài 7 | ✅ Xong |
| Bài 8–43 | ⬜ Chưa làm |

### Môi trường đã có

- **LLM local:** Ollama chạy `qwen3:8b` (dùng khi không muốn tốn tiền API)
- **Dữ liệu:** Báo cáo tài chính HPG (Hòa Phát Group) — PDF thật tại `evals/docs/HGP/`
- **Docker Compose:** Qdrant (vector DB) · Postgres · Redis · MinIO đều đang chạy

### Baseline đã đo

| Chỉ số | Giá trị | Ghi chú |
|--------|---------|---------|
| `refusal_pass_rate` | 0.80 | Ghi trong `NOTES.md`, đo trên golden set 25 câu HPG |

### Bước tiếp theo

**Bài 5** — Chạy eval 5 lần liên tiếp để đo ngưỡng nhiễu: xem `refusal_pass_rate` dao động bao nhiêu giữa các lần chạy, từ đó biết khi nào một sự thay đổi là thật, khi nào chỉ là nhiễu thống kê.

## Hai file cần tạo trước

**`NOTES.md`** — sổ ghi số đo + nơi viết lại điều học được bằng lời của mình:
```markdown
| Ngày | Bài | Đo gì | Kết quả | Điều kiện đo |
|------|-----|-------|---------|--------------|
| 15/08 | B7 | Recall@5, chunk 512 vs 1024 | 0.71 vs 0.66 | golden set 25 câu |
```

**`BLOCKED.md`** — chỗ bỏ dở, để không quên là mình đang nợ.
