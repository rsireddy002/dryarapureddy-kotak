"""Paced read-only probe of Kotak historical data. Prints bars/day and gaps."""
import time
from collections import defaultdict
from datetime import date, timedelta
import kotak_feed

client = kotak_feed.login()
print("login ok")
to_d = date.today()


def rows_of(r):
    if isinstance(r, dict) and isinstance(r.get("data"), dict):
        return r["data"].get("candles") or []
    return []


def probe(sym, iv, days):
    frm = to_d - timedelta(days=days)
    r = client.historical_data(neosymbol=sym, interval=iv,
                               from_date=frm.isoformat(), to_date=to_d.isoformat())
    rows = rows_of(r)
    msg = r.get("message") if isinstance(r, dict) else ""
    print(f"\n{sym} {iv} {days}d: {len(rows)} rows | {msg}")
    per = defaultdict(list)
    for c in rows:
        per[c[0][:10]].append(c[0][11:16])
    for d in sorted(per):
        print(f"  {d}: {len(per[d])} bars {per[d][0]}..{per[d][-1]}")
    return len(rows)


for sym, iv, d in (("nse_cm|2885", "5min", 3), ("nse_cm|2885", "5min", 10),
                   ("nse_cm|2885", "5min", 30), ("nse_cm|2885", "1min", 3),
                   ("nse_cm|2885", "D", 400), ("nse_cm|1333", "5min", 3)):
    try:
        probe(sym, iv, d)
    except Exception as e:
        print(f"{sym} {iv} {d}d FAILED:", repr(e)[:200])
    time.sleep(3)

print("\nburst test: 8 calls, 1s apart")
ok = 0
for i in range(8):
    try:
        n = probe("nse_cm|2885", "5min", 3)
        ok += n > 0
    except Exception as e:
        print("fail", repr(e)[:100])
    time.sleep(1)
print("successful:", ok, "of 8")
