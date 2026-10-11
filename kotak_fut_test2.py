"""Read-only: Kotak history for NIFTY/BANKNIFTY Oct futures (token from scrip master). No orders."""
import json, time
from collections import defaultdict
from datetime import date, timedelta
import kotak_feed

client = kotak_feed.login()
print("login ok")
to_d = date.today(); frm = to_d - timedelta(days=18)
for name, tok in (("NIFTY26OCTFUT", "48704"), ("BANKNIFTY26OCTFUT", "48699")):
    sym = f"nse_fo|{tok}"
    try:
        res = client.historical_data(neosymbol=sym, interval="5min", from_date=frm.isoformat(), to_date=to_d.isoformat())
        rows = (res.get("data") or {}).get("candles") if isinstance(res, dict) else None
        rows = rows or []
        print(f"\n{name} {sym}: {len(rows)} rows")
        if rows:
            per = defaultdict(list)
            for c in rows:
                per[c[0][:10]].append(c)
            for d in sorted(per)[-4:]:
                v = sum(c[5] for c in per[d])
                print(f"  {d}: {len(per[d])} bars {per[d][0][0][11:16]}..{per[d][-1][0][11:16]} volume={v}")
            print("  last row:", rows[-1])
        else:
            print(" reply:", json.dumps(res, default=str)[:300])
    except Exception as e:
        print(sym, "FAILED:", repr(e)[:200])
    time.sleep(3)
