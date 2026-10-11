"""Read-only: find Kotak index/HFCL tokens and test index history. No orders."""
import io, json, time
from datetime import date, timedelta
import pandas as pd, requests
import kotak_feed

client = kotak_feed.login()
print("login ok")

# 1) scrip master lookups (HFCL token + index rows)
try:
    url = client.scrip_master("nse_cm")
    url = url if isinstance(url, str) else (url.get("filesPaths") or [None])[0] if isinstance(url, dict) else None
    df = pd.read_csv(io.StringIO(requests.get(url, timeout=60).text), low_memory=False)
    name = df["pSymbolName"].astype(str).str.strip()
    print("\nHFCL rows:\n", df[name.str.upper() == "HFCL"][["pSymbol", "pSymbolName", "pTrdSymbol", "pGroup"]].to_string(index=False))
    idx = df[name.str.contains("nifty 50|nifty bank", case=False, regex=True)]
    print("\nindex rows:\n", idx[["pSymbol", "pSymbolName", "pTrdSymbol", "pGroup"]].to_string(index=False))
except Exception as e:
    print("scrip master lookup failed:", repr(e)[:200])

# 2) index history, paced
to_d = date.today(); frm = to_d - timedelta(days=3)
for nm in ("Nifty 50", "Nifty Bank"):
    try:
        r = client.historical_data(neosymbol=f"nse_cm|{nm}", interval="5min",
                                   from_date=frm.isoformat(), to_date=to_d.isoformat())
        rows = (r.get("data") or {}).get("candles") if isinstance(r, dict) else None
        print(f"\n{nm}: {len(rows or [])} rows | first={rows[0] if rows else None} last={rows[-1] if rows else None}")
        if not rows:
            print("  reply:", json.dumps(r, default=str)[:300])
    except Exception as e:
        print(nm, "FAILED:", repr(e)[:200])
    time.sleep(3)
