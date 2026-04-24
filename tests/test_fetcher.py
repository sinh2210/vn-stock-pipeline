"""
tests/test_fetcher.py — Unit tests (no real API/DB calls needed)
Run: pytest tests/ -v
"""
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ingestion'))

from fetcher import compute_market_summary, fetch_multiple


# ── Fixtures ──────────────────────────────────────────────────────────────

def make_ohlcv(symbol: str, n: int = 5) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({
        "time":   dates,
        "symbol": symbol,
        "open":   [100.0 + i for i in range(n)],
        "high":   [105.0 + i for i in range(n)],
        "low":    [95.0  + i for i in range(n)],
        "close":  [102.0 + i for i in range(n)],
        "volume": [1_000_000 * (i + 1) for i in range(n)],
    })


# ── compute_market_summary ────────────────────────────────────────────────

class TestComputeMarketSummary:
    def test_returns_one_row_per_symbol(self):
        df = pd.concat([make_ohlcv("VCB"), make_ohlcv("TCB")])
        summary = compute_market_summary(df)
        assert len(summary) == 2
        assert set(summary["symbol"]) == {"VCB", "TCB"}

    def test_pct_change_is_numeric(self):
        df = make_ohlcv("HPG")
        summary = compute_market_summary(df)
        assert pd.api.types.is_float_dtype(summary["pct_change"])

    def test_volume_is_standard_int(self):
        df = make_ohlcv("VCB")
        summary = compute_market_summary(df)
        assert summary["volume"].dtype == "int64" or summary["volume"].dtype == object

    def test_empty_input_returns_empty(self):
        summary = compute_market_summary(pd.DataFrame())
        assert summary.empty

    def test_contains_required_columns(self):
        df = make_ohlcv("VNM")
        summary = compute_market_summary(df)
        for col in ("time", "symbol", "close", "pct_change", "volume"):
            assert col in summary.columns

    def test_last_row_is_used(self):
        df = make_ohlcv("FPT", n=5)
        summary = compute_market_summary(df)
        assert summary.iloc[0]["close"] == pytest.approx(106.0)

    def test_pct_change_uses_db_prev_close_when_engine_provided(self):
        """Khi có engine, pct_change phải dựa vào prev_close từ DB."""
        df = make_ohlcv("VCB", n=1)  # chỉ 1 ngày — batch pct_change sẽ là NaN

        mock_engine = MagicMock()
        mock_conn   = MagicMock()
        mock_conn.execute.return_value.mappings.return_value.all.return_value = [
            {"symbol": "VCB", "close": 100.0}   # prev close = 100
        ]
        mock_engine.connect.return_value.__enter__ = lambda s: mock_conn
        mock_engine.connect.return_value.__exit__  = MagicMock(return_value=False)

        summary = compute_market_summary(df, engine=mock_engine)
        # today close = 102.0, prev = 100.0 → pct = 2.0
        assert summary.iloc[0]["pct_change"] == pytest.approx(2.0)

    def test_pct_change_fallback_when_no_engine(self):
        """Không có engine → fallback batch, 1-row batch = 0.0"""
        df = make_ohlcv("TCB", n=1)
        summary = compute_market_summary(df, engine=None)
        assert summary.iloc[0]["pct_change"] == pytest.approx(0.0)


# ── fetch_multiple ────────────────────────────────────────────────────────

class TestFetchMultiple:
    @patch("fetcher.fetch_ohlcv")
    def test_combines_results(self, mock_fetch):
        mock_fetch.side_effect = lambda sym, start, end: make_ohlcv(sym)
        result = fetch_multiple(["VCB", "TCB"], "2024-01-01", "2024-01-05", delay_seconds=0)
        assert len(result) == 10   # 5 rows × 2 tickers
        assert set(result["symbol"]) == {"VCB", "TCB"}

    @patch("fetcher.fetch_ohlcv")
    def test_skips_empty_results(self, mock_fetch):
        def side_effect(sym, start, end):
            if sym == "INVALID":
                return pd.DataFrame()
            return make_ohlcv(sym)

        mock_fetch.side_effect = side_effect
        result = fetch_multiple(["VCB", "INVALID"], "2024-01-01", "2024-01-05", delay_seconds=0)
        assert set(result["symbol"]) == {"VCB"}

    @patch("fetcher.fetch_ohlcv")
    def test_all_empty_returns_empty_df(self, mock_fetch):
        mock_fetch.return_value = pd.DataFrame()
        result = fetch_multiple(["X", "Y"], "2024-01-01", "2024-01-05", delay_seconds=0)
        assert result.empty
