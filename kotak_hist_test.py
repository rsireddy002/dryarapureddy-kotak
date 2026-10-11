"""Read-only test: does Kotak Neo return historical candles? Prints a short summary, no orders."""
import json
from datetime import date, timedelta
import kotak_feed

client = kotak_feed.login()
print("login ok")
to_d = date.today()


def probe(symbol, interval, days):
    frm = to_d - timedelta(days=days)
    r = client.historical_data(neosymbol=symbol, interval=interval,
                               from_date=frm.isoformat(), to_date=to_d.isoformat())
    text = json.dumps(r, default=str)
    print(f"\n{symbol} {interval} last {days}d -> reply starts: {text[:300]}")
    rows = None
    if isinstance(r, dict):
        for k in ("data", "candles", "Data"):
            if isinstance(r.get(k), list):
                rows = r[k]
                break
        if rows is None and isinstance(r.get("data"), dict):
            for v in r["data"].values():
                if isinstance(v, list):
                    rows = v
                    break
    elif isinstance(r, list):
        rows = r
    if rows:
        print("  rows:", len(rows), "first:", rows[0], "last:", rows[-1])


for sym, iv, d in (("nse_cm|2885", "5min", 3), ("nse_cm|2885", "5min", 30),
                   ("nse_cm|2885", "1min", 3), ("nse_cm|2885", "D", 400)):
    try:
        probe(sym, iv, d)
    except Exception as e:
        print(f"\n{sym} {iv} {d}d FAILED:", repr(e)[:300])
