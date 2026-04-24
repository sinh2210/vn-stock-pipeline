-- ============================================================
-- VN Stock Pipeline — Database Schema
-- TimescaleDB hypertable for time-series performance
-- ============================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ── Stock metadata ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_info (
    symbol          VARCHAR(10)  PRIMARY KEY,
    company_name    TEXT,
    exchange        VARCHAR(10),   -- HOSE / HNX / UPCOM
    industry        TEXT,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);

-- ── OHLCV daily prices ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_prices (
    time            TIMESTAMPTZ  NOT NULL,
    symbol          VARCHAR(10)  NOT NULL REFERENCES stock_info(symbol),
    open            NUMERIC(18,2),
    high            NUMERIC(18,2),
    low             NUMERIC(18,2),
    close           NUMERIC(18,2),
    volume          BIGINT,
    UNIQUE (time, symbol)
);

-- Convert to TimescaleDB hypertable (partitioned by time, 7-day chunks)
SELECT create_hypertable(
    'stock_prices', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Index for fast symbol lookups
CREATE INDEX IF NOT EXISTS idx_stock_prices_symbol
    ON stock_prices (symbol, time DESC);

-- ── Market summary (daily snapshot) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_summary (
    time            TIMESTAMPTZ  NOT NULL,
    symbol          VARCHAR(10)  NOT NULL,
    close           NUMERIC(18,2),
    pct_change      NUMERIC(8,4),   -- % change vs previous close
    volume          BIGINT,
    PRIMARY KEY (time, symbol)
);

SELECT create_hypertable(
    'market_summary', 'time',
    if_not_exists => TRUE
);

-- ── Seed stock_info with 10 blue-chip tickers ─────────────────────────────
INSERT INTO stock_info (symbol, company_name, exchange, industry) VALUES
    ('VCB',  'Vietcombank',                         'HOSE', 'Banking'),
    ('TCB',  'Techcombank',                          'HOSE', 'Banking'),
    ('BID',  'BIDV',                                 'HOSE', 'Banking'),
    ('HPG',  'Hoa Phat Group',                       'HOSE', 'Steel'),
    ('VNM',  'Vinamilk',                             'HOSE', 'Food & Beverage'),
    ('FPT',  'FPT Corporation',                      'HOSE', 'Technology'),
    ('VHM',  'Vinhomes',                             'HOSE', 'Real Estate'),
    ('MSN',  'Masan Group',                          'HOSE', 'Consumer'),
    ('GAS',  'PetroVietnam Gas',                     'HOSE', 'Energy'),
    ('SSI',  'SSI Securities',                       'HOSE', 'Finance')
ON CONFLICT (symbol) DO NOTHING;
