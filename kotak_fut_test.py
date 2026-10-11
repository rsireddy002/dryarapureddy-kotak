"""Read-only: can Kotak give NIFTY / BANKNIFTY futures history (with volume)? No orders."""
import io, json, time
from datetime import date, timedelta
import pandas as pd, requests
import kotak_feed

client = kotak_feed.login()
print("login ok")
url = client.scrip_master("nse_fo")
url = url if isinstance(url, str) else (url.get("filesPaths") or [None])[0] if isinstance(url, dict) else None
df = pd.read_csv(io.StringIO(requests.get(url, timeout=90).text), low_memory=False)
print("columns:", list(df.columns)[:30])
nm = df["pSymbolName"].astype(str).str.strip()
fut = df[nm.isin(["NIFTY", "BANKNIFTY"]) & df["pTrdSymbol"].astype(str).str.contains("FUT")]
cols = [c for c in ("pSymbol", "pSymbolName", "pTrdSymbol", "pInstType", "lExpiryDate") if c in df.columns]
print(fut[cols].to_string(index=False))
to_d = date.today(); frm = to_d - timedelta(days=3)
for _, r in fut.sort_values("lExpiryDate").groupby("pSymbolName").head(1).iterrows():
    sym = f"nse_fo|{r['pSymbol']}"
    try:
        res = client.historical_data(neosymbol=sym, interval="5min", from_date=frm.isoformat(), to_date=to_d.isoformat())
        rows = (res.get("data") or {}).get("candles") if isinstance(res, dict) else None
        print(f"\n{r['pTrdSymbol']} {sym}: {len(rows or [])} rows first={rows[0] if rows else None} last={rows[-1] if rows else None}")
        if not rows:
            print(" reply:", json.dumps(res, default=str)[:300])
    except Exception as e:
        print(sym, "FAILED:", repr(e)[:200])
    time.sleep(3)
