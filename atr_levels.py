"""
atr_levels.py - daily ATR(14) support/resistance helpers for the Kotak app.

Same maths as dryarapureddy-live (Wilder ATR(14) of DAILY candles that ended before the
charted session; lines at previous close +/- 1 ATR), but the Kotak app has no daily-candle
feed at runtime. So the ATR is computed once in the precompute (which already downloads the
daily candles from Kotak) and stored in the zones cache next to prev_close / ema_200. The app
then draws the lines with no extra API calls. No Streamlit imports: usable from the
precompute scripts too.

Cache fields written by atr_cache_fields():
  atr14, atr_pc, atr_asof  - ATR and last close using every completed daily candle up to atr_asof
  atr14_prev, atr_pc_prev  - the same, but excluding the atr_asof candle (for viewing that very session)
"""
from datetime import date, datetime, time as dtime, timedelta, timezone

import pandas as pd

ATR_PERIOD = 14
IST = timezone(timedelta(hours=5, minutes=30))
MAX_STALE_DAYS = 5   # do not draw lines from a cache older than this many calendar days


def compute_daily_atr(daily_df, before_date, period=ATR_PERIOD):
    """Wilder ATR(period) from daily candles dated before `before_date`, plus the last completed close.
    Returns (atr, last_close) or None when there are not enough completed days."""
    if daily_df is None or daily_df.empty:
        return None
    d = daily_df[daily_df["date"] < before_date].reset_index(drop=True)
    if len(d) < period + 1:
        return None
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    atr = float(tr.iloc[1:period + 1].mean())          # seed: simple average of the first `period` true ranges
    for v in tr.iloc[period + 1:]:
        atr = (atr * (period - 1) + float(v)) / period  # Wilder smoothing
    return atr, float(d["close"].iloc[-1])


def atr_cache_fields(daily_df, now=None):
    """Fields to merge into a symbol's zones-cache entry; {} if there is not enough daily history.
    Today's daily candle is ignored until after the close (it is still forming before ~15:35 IST)."""
    if daily_df is None or daily_df.empty or "date" not in daily_df.columns:
        return {}
    now = now or datetime.now(IST)
    d = daily_df
    if now.time() < dtime(15, 35):
        d = d[d["date"] < now.date()]
    if d.empty:
        return {}
    last = d["date"].max()
    out = {}
    latest = compute_daily_atr(d, last + timedelta(days=1))
    if latest:
        out.update(atr14=round(latest[0], 4), atr_pc=round(latest[1], 4), atr_asof=last.isoformat())
    prev = compute_daily_atr(d, last)
    if prev:
        out.update(atr14_prev=round(prev[0], 4), atr_pc_prev=round(prev[1], 4))
    return out


def atr_for_session(entry, session_date):
    """(atr, previous_close) for the session being charted, from a cache entry; None when unavailable."""
    try:
        asof = date.fromisoformat(entry.get("atr_asof"))
    except Exception:
        return None
    if session_date > asof:
        if (session_date - asof).days > MAX_STALE_DAYS or entry.get("atr14") is None:
            return None
        return float(entry["atr14"]), float(entry["atr_pc"])
    if entry.get("atr14_prev") is not None:      # charting the asof session itself (or older)
        return float(entry["atr14_prev"]), float(entry["atr_pc_prev"])
    return None
