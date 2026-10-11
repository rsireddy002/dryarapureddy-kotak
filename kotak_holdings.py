"""Read-only: logs in with kotak_creds.txt (auto TOTP) and prints your holdings. Places no orders."""
import json
import kotak_feed

client = kotak_feed.login()
print("login ok")
r = client.holdings()
if isinstance(r, dict) and (r.get("error") or r.get("errors") or r.get("Error Message")):
    print("holdings call failed:", str(r)[:400])
else:
    data = r.get("data") if isinstance(r, dict) else r
    if not data:
        print("no holdings returned. raw:", str(r)[:400])
    else:
        for h in data:
            if not isinstance(h, dict):
                print(h); continue
            keep = {k: h[k] for k in h if k.lower() in (
                "displaysymbol", "symbol", "tradingsymbol", "quantity", "holdingqty", "averageprice",
                "avgprice", "closingprice", "ltp", "mktvalue", "marketvalue", "pnl", "unrealisedpnl")}
            print(keep or h)
