"""
fetcher.py — Pull OHLCV data from vnstock (source: KBS)
and normalize into a clean DataFrame for DB insertion.
"""
import time
from datetime import date, timedelta

import pandas as pd
from loguru import logger
from sqlalchemy import text
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from vnstock import Vnstock


# ── vnstock v3 uses KBS as the most stable free source ───────────────────
SOURCE = "KBS"

COLUMN_MAP = {
    # vnstock v3 column names → our DB column names
    "time":   "time",
    "open":   "open",
    "high":   "high",
    "low":    "low",
    "close":  "close",
    "volume": "volume",
}


@retry(
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
)
def _fetch_raw(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Low-level fetch with automatic retry on network errors."""
    stock = Vnstock().stock(symbol=symbol, source=SOURCE)
    df = stock.quote.history(start=start, end=end)
    return df


def fetch_ohlcv(
    symbol: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    Fetch OHLCV for a single ticker and return a clean DataFrame.

    Columns returned: time, symbol, open, high, low, close, volume
    """
    logger.info(f"Fetching {symbol} [{start} → {end}]")

    try:
        df = _fetch_raw(symbol, start, end)
    except Exception as e:
        logger.error(f"Failed to fetch {symbol}: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        logger.warning(f"No data returned for {symbol}")
        return pd.DataFrame()

    # Rename columns to match DB schema
    df = df.rename(columns=COLUMN_MAP)

    # Keep only needed columns (vnstock may return extra cols)
    keep_cols = list(COLUMN_MAP.values())
    df = df[[c for c in keep_cols if c in df.columns]].copy()

    # Ensure time is timezone-aware UTC (TimescaleDB expects TIMESTAMPTZ)
    df["time"] = pd.to_datetime(df["time"], utc=True)

    # Add symbol column
    df["symbol"] = symbol

    # Cast numeric types explicitly
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)

    # Drop rows with null close (bad data)
    df = df.dropna(subset=["close"])

    logger.success(f"{symbol}: {len(df)} rows fetched")
    return df


def compute_market_summary(prices_df: pd.DataFrame, engine=None) -> pd.DataFrame:
    """
    Compute pct_change cho từng symbol bằng cách so sánh close hôm nay
    với close ngày giao dịch gần nhất trong DB.

    Nếu engine=None hoặc không tìm được previous close trong DB,
    fallback về tính pct_change trong batch (dùng cho backfill / test).
    """
    if prices_df.empty:
        return pd.DataFrame()

    summary_rows = []

    # Lấy previous close từ DB nếu có engine
    prev_close_map: dict = {}
    if engine is not None:
        try:
            symbols = prices_df["symbol"].unique().tolist()
            sql = text("""
                SELECT DISTINCT ON (symbol)
                    symbol, close
                FROM stock_prices
                WHERE symbol = ANY(:syms)
                ORDER BY symbol, time DESC
            """)
            with engine.connect() as conn:
                rows = conn.execute(sql, {"syms": symbols}).mappings().all()
            prev_close_map = {r["symbol"]: float(r["close"]) for r in rows}
        except Exception as e:
            from loguru import logger
            logger.warning(f"Could not fetch previous close from DB: {e}. Falling back to batch pct_change.")

    for symbol, group in prices_df.groupby("symbol"):
        group = group.sort_values("time").copy()
        last_row = group.iloc[-1]
        today_close = float(last_row["close"])

        if symbol in prev_close_map:
            prev_close = prev_close_map[symbol]
            pct = round((today_close - prev_close) / prev_close * 100, 4) if prev_close else 0.0
        else:
            # Fallback: tính trong batch (đúng cho backfill nhiều ngày)
            group["pct_change"] = group["close"].pct_change() * 100
            last_pct = group.iloc[-1]["pct_change"]
            pct = round(float(last_pct), 4) if pd.notna(last_pct) else 0.0

        summary_rows.append({
            "time":       last_row["time"],
            "symbol":     symbol,
            "close":      today_close,
            "pct_change": pct,
            "volume":     int(last_row["volume"]),
        })

    return pd.DataFrame(summary_rows)


def fetch_multiple(
    tickers: list[str],
    start: str,
    end: str,
    delay_seconds: float = 1.0,
) -> pd.DataFrame:
    """
    Fetch OHLCV for multiple tickers and return combined DataFrame.
    delay_seconds: polite pause between API calls to avoid rate-limiting.
    """
    all_dfs = []

    for ticker in tickers:
        df = fetch_ohlcv(ticker, start, end)
        if not df.empty:
            all_dfs.append(df)
        time.sleep(delay_seconds)

    if not all_dfs:
        logger.error("No data fetched for any ticker.")
        return pd.DataFrame()

    combined = pd.concat(all_dfs, ignore_index=True)
    logger.info(f"Total rows fetched: {len(combined)} across {len(all_dfs)} tickers")
    return combined
