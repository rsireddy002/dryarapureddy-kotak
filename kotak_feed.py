"""Kotak Neo live-feed daemon for dryarapureddy-live (stocks + NIFTY/BANKNIFTY).

Logs in automatically (pyotp TOTP from KOTAK_TOTP_SECRET), subscribes to all
symbols in kotak_tokens.json, builds 1/5/15-min candles and writes:
  fno_live_candles.json   (same format the app already reads)
  kotak_quotes.json       (Upstox-quote-shaped snapshot, keyed by instrument_key)
Credentials come from kotak_creds.txt / environment on THIS machine only.
"""
import asyncio, json, os, sys, threading, time
from datetime import datetime, timedelta, timezone

import pyotp
from neo_api_client import NeoAPI
from neo_api_client.websocket.feed import WsToken

sys.path.insert(0, os.environ.get("APP_DIR", os.path.dirname(os.path.abspath(__file__))))
from candle_aggregator import CandleAggregator, INTERVALS_SECONDS

IST = timezone(timedelta(hours=5, minutes=30))
OUT_CANDLES = os.environ.get("FNO_LIVE_CANDLES_OUTPUT", "fno_live_candles.json")
OUT_QUOTES = os.environ.get("KOTAK_QUOTES_OUTPUT", "kotak_quotes.json")
CRED_FILE = os.environ.get("KOTAK_CRED_FILE", "kotak_creds.txt")
CACHE_FILE = os.environ.get("ZONES_CACHE", "sahi_zones_cache.json")
LABEL = {v: k for k, v in INTERVALS_SECONDS.items()}
INDEX_NAMES = {"NIFTY": "Nifty 50", "BANKNIFTY": "Nifty Bank"}

agg = CandleAggregator(bar_seconds_list=list(INTERVALS_SECONDS.values()))
quotes = {}          # symbol -> dict
lock = threading.Lock()


def load_creds():
    c = {}
    if os.path.exists(CRED_FILE):
        for line in open(CRED_FILE, encoding="utf-8-sig"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                c[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("KOTAK_CONSUMER_KEY", "KOTAK_MOBILE", "KOTAK_UCC", "KOTAK_MPIN", "KOTAK_TOTP_SECRET"):
        c.setdefault(k, os.environ.get(k, ""))
    return c


def login():
    c = load_creds()
    manual = os.environ.get("KOTAK_MANUAL_TOTP") == "1"
    need = ["KOTAK_CONSUMER_KEY", "KOTAK_MOBILE", "KOTAK_UCC", "KOTAK_MPIN"] + ([] if manual else ["KOTAK_TOTP_SECRET"])
    for k in need:
        if not c.get(k):
            sys.exit("Missing " + k)
    client = NeoAPI(consumer_key=c["KOTAK_CONSUMER_KEY"], environment="prod")
    if manual:
        totp = input("Enter the current 6-digit TOTP from your authenticator app: ").strip()
    else:
        totp = pyotp.TOTP(c["KOTAK_TOTP_SECRET"].replace(" ", "")).now()
    r1 = client.totp_login(mobile_number=c["KOTAK_MOBILE"], ucc=c["KOTAK_UCC"], totp=totp)
    if not r1 or (isinstance(r1, dict) and (r1.get("error") or r1.get("errors"))):
        raise RuntimeError("login step 1 failed: " + str(r1)[:200])
    r2 = client.totp_validate(mpin=c["KOTAK_MPIN"])
    if not r2 or (isinstance(r2, dict) and (r2.get("error") or r2.get("errors"))):
        raise RuntimeError("login step 2 failed: " + str(r2)[:200])
    return client


def dumper():
    while True:
        try:
            snap = agg.snapshot()
            ser = {s: {LABEL[bs]: [{**b, "timestamp": b["timestamp"].isoformat()} for b in bars]
                       for bs, bars in d.items()} for s, d in snap.items()}
            json.dump(ser, open(OUT_CANDLES + ".tmp", "w")); os.replace(OUT_CANDLES + ".tmp", OUT_CANDLES)
            with lock:
                q = dict(quotes)
            out = {"_updated": time.time(), "data": q}
            json.dump(out, open(OUT_QUOTES + ".tmp", "w")); os.replace(OUT_QUOTES + ".tmp", OUT_QUOTES)
        except Exception as e:
            print("dump error:", e, flush=True)
        time.sleep(2)


async def stream(client, tok2sym, cache, stocks, indices):
    async with client.create_websocket() as ws:
        await ws.subscribe_scrips(stocks)
        if indices:
            try:
                await ws.subscribe_index(indices)
            except Exception as e:
                print("index subscribe failed:", e, flush=True)
        print("subscribed", len(stocks), "stocks", len(indices), "indices", flush=True)
        async for msg in ws:
            d = getattr(msg, "model_dump", lambda: {})()
            ltp = d.get("last_traded_price")
            sym = tok2sym.get(str(d.get("instrument_token")))
            if ltp is None or not sym:
                continue
            vol = d.get("volume_traded_today")
            now = datetime.now(IST)
            agg.on_tick(sym, float(ltp), float(vol) if vol is not None else None, timestamp=now)
            ik = cache.get(sym, {}).get("instrument_key")
            if ik:
                with lock:
                    old = quotes.get(ik, {})
                    quotes[ik] = {
                        "instrument_token": ik, "last_price": float(ltp),
                        "average_price": float(d.get("average_trade_price") or old.get("average_price") or ltp),
                        "volume": float(vol) if vol is not None else old.get("volume", 0),
                        "ohlc": {"open": float(d.get("open_price") or old.get("ohlc", {}).get("open") or ltp),
                                 "high": d.get("high_price"), "low": d.get("low_price"),
                                 "close": d.get("close_price")},
                    }


def main():
    cache = json.load(open(CACHE_FILE))
    tokens = json.load(open("kotak_tokens.json"))
    tok2sym = {str(t): s for s, t in tokens.items()}
    stocks = [WsToken("nse_cm", str(t)) for t in tokens.values()]
    idx_path = "kotak_index_tokens.json"
    indices = []
    if os.path.exists(idx_path):
        for s, t in json.load(open(idx_path)).items():
            tok2sym[str(t)] = s
            indices.append(WsToken("nse_cm", str(t)))
    threading.Thread(target=dumper, daemon=True).start()
    while True:
        # outside market hours (weekday 08:50-15:45 IST) just wait; fresh login each session
        now = datetime.now(IST)
        live = os.environ.get("KOTAK_ALWAYS") == "1" or now.weekday() < 5 and (8 * 60 + 50) <= now.hour * 60 + now.minute <= (15 * 60 + 45)
        if not live:
            time.sleep(30); continue
        try:
            client = login()
            print("logged in", now.isoformat(), flush=True)
            asyncio.run(stream(client, tok2sym, cache, stocks, indices))
        except Exception as e:
            print("feed error, retrying in 15s:", repr(e), flush=True)
        time.sleep(15)


if __name__ == "__main__":
    main()
