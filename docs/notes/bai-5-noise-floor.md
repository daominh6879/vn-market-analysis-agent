# Bài 5 — Ngưỡng nhiễu (Noise Floor)

5 lần chạy `refusal_pass_rate`:

| Lần | Score |
|---|---|
| 1 | 0.800 |
| 2 | 0.800 |
| 3 | 0.800 |
| 4 | 0.800 |
| 5 | 1.000 |
| **std** | **0.0894** |
| **2×std (ngưỡng CI)** | **0.1789** |

Dùng làm regression threshold trong `evals/run.py`: drop > 0.1789 → fail CI.
