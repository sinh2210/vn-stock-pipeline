"""
db.py — SQLAlchemy engine + helper functions
"""
import os
from contextlib import contextmanager

import pandas as pd
from dotenv import load_dotenv
from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://vnstock:vnstock123@localhost:5432/vnstock_db",
)

engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,   # drop stale connections automatically
)

Session = sessionmaker(bind=engine)


@contextmanager
def get_session():
    """Context manager that auto-commits or rolls back."""
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def upsert_prices(df: pd.DataFrame) -> int:
    """
    Insert OHLCV rows into stock_prices.
    Skips duplicates via ON CONFLICT DO NOTHING.
    Returns number of rows inserted.
    """
    if df.empty:
        return 0

    rows = df.to_dict(orient="records")
    sql = text("""
        INSERT INTO stock_prices (time, symbol, open, high, low, close, volume)
        VALUES (:time, :symbol, :open, :high, :low, :close, :volume)
        ON CONFLICT (time, symbol) DO NOTHING
    """)

    inserted = 0
    with get_session() as session:
        result = session.execute(sql, rows)
        inserted = result.rowcount

    skipped = len(rows) - inserted
    logger.info(f"stock_prices: {inserted} inserted, {skipped} duplicates skipped")
    return inserted


def upsert_market_summary(df: pd.DataFrame) -> int:
    """Insert/update daily market summary with pct_change."""
    if df.empty:
        return 0

    rows = df.to_dict(orient="records")
    sql = text("""
        INSERT INTO market_summary (time, symbol, close, pct_change, volume)
        VALUES (:time, :symbol, :close, :pct_change, :volume)
        ON CONFLICT (time, symbol)
        DO UPDATE SET
            close      = EXCLUDED.close,
            pct_change = EXCLUDED.pct_change,
            volume     = EXCLUDED.volume
    """)

    with get_session() as session:
        result = session.execute(sql, rows)
        return result.rowcount


def health_check() -> bool:
    """Return True if DB is reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"DB health check failed: {e}")
        return False


def ensure_tickers_exist(tickers: list) -> None:
    """
    Upsert tickers vào stock_info trước khi insert prices.
    Tránh FK violation khi thêm ticker mới vào TICKERS env
    mà chưa có trong stock_info.
    """
    rows = [{"symbol": t.upper()} for t in tickers]
    sql = text("""
        INSERT INTO stock_info (symbol)
        VALUES (:symbol)
        ON CONFLICT (symbol) DO NOTHING
    """)
    with get_session() as session:
        session.execute(sql, rows)
    logger.debug(f"Ensured {len(tickers)} tickers exist in stock_info")
