"""
run_ingestion.py — Entrypoint for the ingestion service.

Usage:
    python run_ingestion.py                   # uses .env / env vars
    python run_ingestion.py --backfill 365    # backfill last N days
"""
import argparse
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv
from loguru import logger

from db import engine, ensure_tickers_exist, health_check, upsert_market_summary, upsert_prices
from fetcher import compute_market_summary, fetch_multiple

load_dotenv()

# ── Configure loguru ──────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    level="INFO",
)
logger.add(
    "logs/ingestion.log",
    rotation="10 MB",
    retention="30 days",
    level="DEBUG",
)


def parse_args():
    parser = argparse.ArgumentParser(description="VN Stock Data Ingestion")
    parser.add_argument(
        "--backfill",
        type=int,
        default=None,
        help="Number of days to backfill from today (overrides START_DATE env var)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # ── DB health check ───────────────────────────────────────────────────
    logger.info("Checking database connectivity...")
    if not health_check():
        logger.critical("Cannot connect to database. Exiting.")
        sys.exit(1)
    logger.success("Database is reachable.")

    # ── Resolve date range ────────────────────────────────────────────────
    today = date.today()

    if args.backfill:
        start_date = (today - timedelta(days=args.backfill)).isoformat()
    else:
        start_date = os.getenv("START_DATE", (today - timedelta(days=30)).isoformat())

    end_date = today.isoformat()

    # ── Resolve ticker list ───────────────────────────────────────────────
    tickers_env = os.getenv("TICKERS", "VCB,TCB,HPG,VNM,FPT")
    tickers = [t.strip().upper() for t in tickers_env.split(",") if t.strip()]

    logger.info(f"Tickers : {tickers}")
    logger.info(f"Period  : {start_date} → {end_date}")

    # ── Ensure all tickers exist in stock_info (prevent FK violation) ─────
    ensure_tickers_exist(tickers)

    # ── Fetch ─────────────────────────────────────────────────────────────
    prices_df = fetch_multiple(tickers, start=start_date, end=end_date)

    if prices_df.empty:
        logger.error("Ingestion completed with 0 rows. Check API connectivity.")
        sys.exit(1)

    # ── Persist prices ────────────────────────────────────────────────────
    inserted_prices = upsert_prices(prices_df)

    # ── Compute & persist market summary (query prev close from DB) ───────
    summary_df = compute_market_summary(prices_df, engine=engine)
    inserted_summary = upsert_market_summary(summary_df)

    logger.success(
        f"Ingestion done. "
        f"stock_prices: {inserted_prices} rows | "
        f"market_summary: {inserted_summary} rows"
    )


if __name__ == "__main__":
    main()
