-- 054_index_membership.sql — daily constituents of the HOSE indices (SSI iBoard groups), collected from 2026-10-29 on so
-- vn-portfolio gets a point-in-time membership history (khung 2.4 "thành phần chỉ số theo thời gian"). No older history.
CREATE TABLE IF NOT EXISTS index_membership (
    date        DATE        NOT NULL,
    index_code  TEXT        NOT NULL,
    ticker      TEXT        NOT NULL,
    PRIMARY KEY (date, index_code, ticker)
);
