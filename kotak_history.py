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
# Index "tokens" are their names on nse_cm (history works; live index ticks are untested)
INDEX_TOKENS = {"NIFTY": "Nifty 50", "BANKNIFTY": "Nifty Bank"}   # kept for reference; charts/zones use futures
FUT_SYMBOLS = ("NIFTY", "BANKNIFTY")
_FUT_CACHE = {"day": None, "map": {}}


def resolve_index_futures(client):
    """{'NIFTY': '48704', ...}: nearest-expiry index futures tokens from Kotak's nse_fo scrip master.
    Cached per day. Futures carry volume (index candles do not) and trade until 15:40."""
    import io, re
    import requests
    today = datetime.now(IST).date().isoformat()
    if _FUT_CACHE["day"] == today and _FUT_CACHE["map"]:
        return _FUT_CACHE["map"]
    url = client.scrip_master("nse_fo")
    if not isinstance(url, str):
        url = (url.get("filesPaths") or [None])[0] if isinstance(url, dict) else None
    df = pd.read_csv(io.StringIO(requests.get(url, timeout=90).text), low_memory=False)
    df.columns = [str(c).strip().rstrip(";") for c in df.columns]
    df["pSymbolName"] = df["pSymbolName"].astype(str).str.strip()
    df["pTrdSymbol"] = df["pTrdSymbol"].astype(str).str.strip()
    out = {}
    for sym in FUT_SYMBOLS:
        best = None
        for _, r in df[(df["pSymbolName"] == sym) & df["pTrdSymbol"].str.endswith("FUT")].iterrows():
            m = re.match(rf"^{sym}(\d{{2}})([A-Z]{{3}})FUT$", r["pTrdSymbol"])
            if not m:
                continue
            mon = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"].index(m.group(2))
            key = (int(m.group(1)), mon)
            if best is None or key < best[0]:
                best = (key, str(int(r["pSymbol"])))
        if best:
            out[sym] = best[1]
    _FUT_CACHE.update(day=today, map=out)
    return out


def _resolve_targets(client, tokens):
    """tokens (symbol -> nse_cm token) -> {symbol: (segment, token)}; index symbols map to futures."""
    tg = {s: ("nse_cm", t) for s, t in tokens.items() if s not in FUT_SYMBOLS}
    try:
        for s, t in resolve_index_futures(client).items():
            tg[s] = ("nse_fo", t)
    except Exception as e:
        STATUS["msg"] = "history: futures lookup failed " + str(e)[:80]
    return tg


def _session_only(df, fut=False):
    """Keep continuous-session bars: stocks 09:15-15:14, index futures 09:15-15:39 (F&O trades to 15:40)."""
    if df.empty:
        return df
    m = df["timestamp"].dt.hour * 60 + df["timestamp"].dt.minute
    return df[m < (15 * 60 + 40 if fut else 15 * 60 + 15)].reset_index(drop=True)
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
    out = {"_fetched_for": _expected_session(), "_updated": time.time(), "_tokens": sorted(tokens), "_fut": 1, "data": {}}
    syms = list(_resolve_targets(client, tokens).items())
    for i, (sym, (seg, tok)) in enumerate(syms):
        rows = []
        for attempt in range(3):
            try:
                rows = _candles_from(client.historical_data(
                    neosymbol=f"{seg}|{tok}", interval="5min", from_date=frm, to_date=to))
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
    tokens = {**json.load(open("kotak_tokens.json")), **INDEX_TOKENS}
    while True:
        try:
            have = _load()
            if (have.get("_fetched_for") != _expected_session() or have.get("_tokens") != sorted(tokens) or not have.get("_fut")
                    or len(have.get("data", {})) < 0.8 * len(tokens)):
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
    df = _session_only(df.sort_values("timestamp").reset_index(drop=True), (symbol or "").strip().upper() in FUT_SYMBOLS)
    if df.empty:
        return pd.DataFrame(columns=cols)
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
    return _session_only(df.sort_values("timestamp").reset_index(drop=True), (symbol or "").strip().upper() in FUT_SYMBOLS)


def diag(sample="HDFCBANK"):
    """One-line status for the sidebar: file present, symbols cached, sample rows."""
    try:
        d = _load()
        n = len(d.get("data", {}))
        rows = len(get_candles(sample))
        return f"file={'yes' if os.path.exists(HIST_FILE) else 'no'} symbols={n} {sample}_rows={rows} cwd={os.getcwd()}"
    except Exception as e:
        return "diag error: " + repr(e)[:150]


def fetch_frame(client, token, interval, days, retries=3, seg="nse_cm"):
    """Kotak historical candles for one stock as a DataFrame shaped like the Upstox REST frame
    (timestamp tz-aware, open/high/low/close/volume/oi/date). interval: '5min' (<=30 days) or 'D' (<=180).
    Paced: sleeps PACE_SECONDS after the call. Returns an empty frame if nothing came back."""
    today = datetime.now(IST).date()
    frm = (today - timedelta(days=days)).isoformat()
    rows = []
    for attempt in range(retries):
        try:
            rows = _candles_from(client.historical_data(
                neosymbol=f"{seg}|{token}", interval=interval, from_date=frm, to_date=today.isoformat()))
            if rows:
                break
        except Exception:
            pass
        time.sleep(15)
    time.sleep(PACE_SECONDS)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([r[:6] for r in rows], columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    if interval != "D":
        df = _session_only(df, seg == "nse_fo")
    df["oi"] = 0
    df["date"] = df["timestamp"].dt.date
    return df
