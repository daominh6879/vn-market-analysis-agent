-- Bài 12: Hai đường dữ liệu — số không vào vector DB
-- Số tài chính đi qua SQL, không qua model lần nào.

CREATE TABLE IF NOT EXISTS financial_facts (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    ky              TEXT NOT NULL,          -- "2024", "2023", "Q3/2024"
    loai_bao_cao    TEXT NOT NULL,          -- "rieng_le" | "hop_nhat"
    ma_chi_tieu     TEXT NOT NULL,
    gia_tri         NUMERIC NOT NULL,
    don_vi          TEXT NOT NULL DEFAULT 'VND',
    nguon_file      TEXT NOT NULL,
    nguon_trang     INT  NOT NULL,
    UNIQUE (ticker, ky, loai_bao_cao, ma_chi_tieu)
);

CREATE TABLE IF NOT EXISTS stock_prices (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT    NOT NULL,
    ngay        DATE    NOT NULL,
    close_adj   NUMERIC NOT NULL,           -- giá đã điều chỉnh — dùng cho bài 19
    volume      BIGINT,
    UNIQUE (ticker, ngay)
);
