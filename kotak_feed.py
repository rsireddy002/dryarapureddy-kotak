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

STATUS = {"msg": "not started"}
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
    manual = os.environ.get("KOTAK_MANUAL_TOTP") == "1" or bool(os.environ.get("KOTAK_TOTP_NOW"))
    need = ["KOTAK_CONSUMER_KEY", "KOTAK_MOBILE", "KOTAK_UCC", "KOTAK_MPIN"] + ([] if manual else ["KOTAK_TOTP_SECRET"])
    for k in need:
        if not c.get(k):
            raise RuntimeError("Missing " + k + " (add it to Streamlit secrets / kotak_creds.txt, or enter a TOTP in the sidebar)")
    client = NeoAPI(consumer_key=c["KOTAK_CONSUMER_KEY"], environment="prod")
    if os.environ.get("KOTAK_TOTP_NOW"):
        totp = os.environ.pop("KOTAK_TOTP_NOW").strip()
    elif manual:
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


def _futures_tokens(client, tok2sym, cm_tokens):
    """NIFTY/BANKNIFTY near-month futures (nse_fo). Index spot ticks carry no volume, and the
    zones are built from the futures contract, so live candles/quotes use it too. Never raises:
    a failure here must not stop the stock feed."""
    out = []
    try:
        import kotak_history
        for sym, tok in kotak_history.resolve_index_futures(client).items():
            tok = str(tok)
            if tok in cm_tokens:
                print("futures token", tok, "collides with a stock token; skipping", sym, flush=True)
                continue
            tok2sym[tok] = sym
            out.append(WsToken("nse_fo", tok))
    except Exception as e:
        print("futures resolve failed:", repr(e), flush=True)
    return out


def _seed_today(client, tok2sym, cm_tokens, fut_map):
    """After a restart during the session, rebuild today's finished 5-min (and 15-min) candles from
    Kotak history so charts keep the whole day. Runs in its own thread next to the live stream,
    never overwrites bars the stream already built, never raises. NIFTY/BANKNIFTY futures first."""
    try:
        import kotak_history
        from candle_aggregator import bucket_start
        now = datetime.now(IST)
        mins = now.hour * 60 + now.minute
        if now.weekday() >= 5 or mins < 9 * 60 + 25 or mins > 15 * 60 + 45:
            return
        cutoff = bucket_start(now, 300)
        targets = [(s, "nse_fo", str(t)) for s, t in fut_map.items()]
        targets += [(s, "nse_cm", str(t)) for s, t in cm_tokens.items() if s not in fut_map]
        done = bars_total = 0
        # Several passes: a symbol whose history call failed or came back empty (rate limit, slow reply) is retried
        # instead of being left to show only the candles built since this app started.
        remaining = list(targets)
        for _pass in range(4):
            failed = []
            for sym, seg, tok in remaining:
                try:
                    df = kotak_history.fetch_frame(client, tok, "5min", 1, retries=1 if _pass == 0 else 2, seg=seg)
                    if df.empty:
                        failed.append((sym, seg, tok))
                        continue
                    df = df[df["date"] == now.date()]
                    bars = [{"timestamp": r.timestamp.to_pydatetime().replace(tzinfo=IST), "open": float(r.open),
                             "high": float(r.high), "low": float(r.low), "close": float(r.close),
                             "volume": float(r.volume)} for r in df.itertuples()]
                    bars_total += agg.seed_bars(sym, bars, before=cutoff)
                    done += 1
                except Exception as e:
                    print("seed", sym, "failed:", repr(e)[:80], flush=True)
                    failed.append((sym, seg, tok))
            print(f"seed pass {_pass + 1}: {len(remaining) - len(failed)} ok, {len(failed)} to retry", flush=True)
            remaining = failed
            if not remaining:
                break
            time.sleep(20)
        print(f"seeded today's candles: {done} symbols, {bars_total} bars", flush=True)
    except Exception as e:
        print("seed failed:", repr(e)[:120], flush=True)


async def stream(client, tok2sym, cache, stocks, indices, futures=()):
    async with client.create_websocket() as ws:
        await ws.subscribe_scrips(stocks)
        if futures:
            try:
                await ws.subscribe_scrips(list(futures))
            except Exception as e:
                print("futures subscribe failed:", repr(e), flush=True)
        if indices:
            try:
                await ws.subscribe_index(indices)
            except Exception as e:
                print("index subscribe failed:", e, flush=True)
        print("subscribed", len(stocks), "stocks", len(indices), "indices", len(futures), "futures", flush=True)
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


def main(embedded=False):
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
            STATUS["msg"] = "waiting for market hours (Mon-Fri 08:50-15:45 IST)"
            time.sleep(30); continue
        try:
            client = login()
            print("logged in", now.isoformat(), flush=True)
            STATUS["msg"] = "connected " + now.strftime("%H:%M:%S")
            futures = _futures_tokens(client, tok2sym, {str(t) for t in tokens.values()})
            try:
                import kotak_history as _kh
                _fm = _kh.resolve_index_futures(client)
            except Exception:
                _fm = {}
            threading.Thread(target=_seed_today, args=(client, tok2sym, tokens, _fm), daemon=True, name="seed-today").start()
            asyncio.run(stream(client, tok2sym, cache, stocks, indices, futures))
        except Exception as e:
            print("feed error, retrying in 15s:", repr(e), flush=True)
            STATUS["msg"] = "error: " + str(e)[:150]
        time.sleep(15)


if __name__ == "__main__":
    main()
