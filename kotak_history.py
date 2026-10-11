"""Kotak Neo historical candles (replaces Upstox REST history). 5-min bars, last ~8 days,
cached in kotak_history.json and refreshed once per completed session. Paced to stay under
Kotak's rate limit (~1 call/sec is fine; bursts get HTTP 429)."""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
HIST_FILE = "kotak_history.json"
LOOKBACK_DAYS = 8
PACE_SECONDS = 1.2
STATUS = {"msg": "history: not started"}
_LOCK = threading.Lock()
_MEM = {"mtime": 0, "data": {}}


def _expected_session(now=None):
    """Date of the most recent weekday whose 15:15 close has passed (holidays ignored)."""
    now = now or datetime.now(IST)
    d = now.date()
    if now.weekday() >= 5 or (now.hour * 60 + now.minute) < 15 * 60 + 20:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def _load():
    try:
        m = os.path.getmtime(HIST_FILE)
    except OSError:
        return {}
    with _LOCK:
        if m != _MEM["mtime"]:
            try:
                _MEM["data"] = json.load(open(HIST_FILE))
                _MEM["mtime"] = m
            except Exception:
                pass
        return _MEM["data"]


def _candles_from(resp):
    if isinstance(resp, dict) and isinstance(resp.get("data"), dict):
        return resp["data"].get("candles") or []
    return []


def _refresh(client, tokens):
    today = datetime.now(IST).date()
    frm = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    to = today.isoformat()
    out = {"_fetched_for": _expected_session(), "_updated": time.time(), "data": {}}
    syms = list(tokens.items())
    for i, (sym, tok) in enumerate(syms):
        rows = []
        for attempt in range(3):
            try:
                rows = _candles_from(client.historical_data(
                    neosymbol=f"nse_cm|{tok}", interval="5min", from_date=frm, to_date=to))
                if rows:
                    break
                time.sleep(15 if attempt else PACE_SECONDS)
            except Exception:
                time.sleep(15)
        if rows:
            out["data"][sym] = rows
        STATUS["msg"] = f"history: fetching {i + 1}/{len(syms)}"
        time.sleep(PACE_SECONDS)
    tmp = HIST_FILE + ".tmp"
    json.dump(out, open(tmp, "w"))
    os.replace(tmp, HIST_FILE)
    STATUS["msg"] = f"history: {len(out['data'])}/{len(syms)} symbols for {out['_fetched_for']}"


def run_forever(login_fn):
    """Background loop: refetch when the cache doesn't cover the latest completed session."""
    tokens = json.load(open("kotak_tokens.json"))
    while True:
        try:
            have = _load()
            if have.get("_fetched_for") != _expected_session() or len(have.get("data", {})) < 0.8 * len(tokens):
                STATUS["msg"] = "history: logging in"
                _refresh(login_fn(), tokens)
        except Exception as e:
            STATUS["msg"] = "history error: " + str(e)[:120]
        time.sleep(600)


def get_candles(symbol, interval=5):
    """Last session's bars (09:15-15:10 for 5-min) as a frame matching the app's candle format.
    interval 15 is resampled from 5-min; interval 1 is unavailable and returns 5-min bars."""
    rows = _load().get("data", {}).get((symbol or "").strip().upper())
    cols = ["timestamp", "open", "high", "low", "close", "volume", "oi", "date"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["date"] = df["timestamp"].dt.date
    df = df[df["date"] == df["date"].max()].reset_index(drop=True)
    if int(interval) == 15:
        r = df.set_index("timestamp").resample("15min", origin="start").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
        r["date"] = r["timestamp"].dt.date
        df = r
    df["oi"] = 0
    return df[cols]


def get_history(symbol, days=7):
    """All cached 5-min bars (several sessions) for a symbol."""
    rows = _load().get("data", {}).get((symbol or "").strip().upper())
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["oi"] = 0
    df["date"] = df["timestamp"].dt.date
    return df.sort_values("timestamp").reset_index(drop=True)
