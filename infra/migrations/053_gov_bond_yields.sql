-- 053_gov_bond_yields.sql — HNX government bond yield curve per day (percent), for vn-portfolio's khung 6.5-C dynamic
-- cost of equity (10-year yield + ERP) and 3.2 rates context. Source: www.hnx.vn Bond_YieldCurve through vnmarket.
CREATE TABLE IF NOT EXISTS gov_bond_yields (
    date             DATE        NOT NULL,
    tenor_years      NUMERIC     NOT NULL,
    par_yield        NUMERIC,
    spot_annual      NUMERIC,
    spot_continuous  NUMERIC,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (date, tenor_years)
);
