"""Headless Precompute from Kotak Neo history, for GitHub Actions (no Streamlit, no Upstox).

Same zone logic as app.py's run_precompute(); reads the Kotak login from environment variables
(GitHub Actions secrets KOTAK_CONSUMER_KEY, KOTAK_MOBILE, KOTAK_UCC, KOTAK_MPIN, KOTAK_TOTP_SECRET),
keeps the previous entry for any symbol Kotak returns nothing for, and refuses to overwrite
sahi_zones_cache.json unless at least 90% of symbols were computed fresh.
"""
import json
import os
import sys
import time

import kotak_feed
import kotak_history
from atr_levels import atr_cache_fields
from github_precompute import (
    CACHE_PATH, COMPOSITE_LOOKBACK_DAYS, DAILY_LOOKBACK_DAYS, RVOL_BASELINE_DAYS,
    EQUITY_SYMBOLS, FUTURES_SYMBOLS, _atomic_json_dump, compute_composite_zones,
    compute_ema_200, compute_intraday_zones, now_ist,
)


def main():
    client = kotak_feed.login()
    try:
        fut_map = kotak_history.resolve_index_futures(client)
    except Exception as e:
        print("index futures lookup failed:", e)
        fut_map = {}
    k_tokens = json.load(open("kotak_tokens.json"))
    try:
        prev = json.load(open(CACHE_PATH))
    except Exception:
        prev = {}
    symbols = list(FUTURES_SYMBOLS) + list(EQUITY_SYMBOLS)
    cache, fresh = {}, 0
    for i, symbol in enumerate(symbols):
        seg = "nse_fo" if symbol in kotak_history.FUT_SYMBOLS else "nse_cm"
        tok = fut_map.get(symbol) if seg == "nse_fo" else k_tokens.get(symbol)
        old = prev.get(symbol) or {}
        try:
            if not tok or not old.get("instrument_key"):
                if old:
                    cache[symbol] = old
                continue
            daily = kotak_history.fetch_frame(client, tok, "D", DAILY_LOOKBACK_DAYS, seg=seg)
            intra = kotak_history.fetch_frame(client, tok, "5min", COMPOSITE_LOOKBACK_DAYS, seg=seg)
            if intra.empty:
                if old:
                    cache[symbol] = old
                print(f"[{i + 1}/{len(symbols)}] {symbol}: no Kotak data, kept previous")
                continue
            prev_close = float(daily["close"].iloc[-1]) if not daily.empty else None
            if daily.empty or intra["timestamp"].iloc[-1].date() > daily["timestamp"].iloc[-1].date():
                prev_close = float(intra["close"].iloc[-1])
            avg_vol = (float(daily["volume"].tail(RVOL_BASELINE_DAYS).mean())
                       if len(daily) >= RVOL_BASELINE_DAYS else None) or None
            stamp = now_ist().strftime("%Y-%m-%d %H:%M:%S")
            cache[symbol] = {
                "instrument_key": old["instrument_key"], "lot_size": old.get("lot_size"),
                "prev_close": prev_close, "avg_daily_volume": avg_vol,
                "composite_zones": compute_composite_zones(intra),
                "intraday_zones": compute_intraday_zones(intra),
                "ema_200": compute_ema_200(intra["close"].tolist()),
                "last_signal": "-", "zones_updated_at": stamp, "composite_updated_at": stamp,
            }
            cache[symbol].update(atr_cache_fields(daily))   # daily ATR(14) for the chart's dotted lines
            fresh += 1
            print(f"[{i + 1}/{len(symbols)}] {symbol}: ok")
        except Exception as e:
            if old:
                cache[symbol] = old
            print(f"[{i + 1}/{len(symbols)}] {symbol}: failed ({e}), kept previous")
    print(f"fresh {fresh}/{len(symbols)}")
    if fresh < 0.9 * len(symbols):
        print("Too few symbols computed; leaving the existing cache untouched.")
        sys.exit(1)
    _atomic_json_dump(CACHE_PATH, cache)
    print(f"Precompute done. {len(cache)} symbols -> {CACHE_PATH}")


if __name__ == "__main__":
    main()
