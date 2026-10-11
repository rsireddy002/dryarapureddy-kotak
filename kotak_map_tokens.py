import csv
import io
import json
import os
import sys

import httpx
from neo_api_client import NeoAPI

CRED_FILE = "kotak_creds.txt"
SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "AXISBANK", "KOTAKBANK", "BAJFINANCE", "BHARTIARTL", "ITC", "LT", "HINDUNILVR", "MARUTI", "TMPV", "TATASTEEL", "SUNPHARMA", "TITAN", "ULTRACEMCO", "ASIANPAINT", "WIPRO", "NTPC", "POWERGRID", "M&M", "ADANIENT", "ADANIPORTS", "BAJAJFINSV", "HCLTECH", "JSWSTEEL", "ONGC", "COALINDIA", "TECHM", "GRASIM", "DIVISLAB", "DRREDDY", "CIPLA", "EICHERMOT", "HEROMOTOCO", "HINDALCO", "BPCL", "BRITANNIA", "APOLLOHOSP", "SBILIFE", "HDFCLIFE", "INDUSINDBK", "BAJAJ-AUTO", "TATACONSUM", "UPL", "SHREECEM", "NESTLEIND", "GAIL", "PIDILITIND", "DLF", "GODREJCP", "SIEMENS", "AMBUJACEM", "BANDHANBNK", "BANKBARODA", "PNB", "CANBK", "IDFCFIRSTB", "FEDERALBNK", "AUROPHARMA", "BEL", "BIOCON", "CHOLAFIN", "COLPAL", "CONCOR", "CUMMINSIND", "DABUR", "DEEPAKNTR", "ESCORTS", "EXIDEIND", "GODREJPROP", "HAVELLS", "HDFCAMC", "ICICIGI", "ICICIPRULI", "IEX", "INDIGO", "INDUSTOWER", "IOC", "IRCTC", "JINDALSTEL", "JUBLFOOD", "LICHSGFIN", "LTM", "LUPIN", "MANAPPURAM", "MARICO", "UNITDSPR", "MFSL", "MOTHERSON", "MPHASIS", "MRF", "MUTHOOTFIN", "NAUKRI", "NMDC", "OBEROIRLTY", "OFSS", "PAGEIND", "PIRAMALFIN", "PERSISTENT", "PETRONET", "PFC", "PIIND", "POLYCAB", "RECLTD", "SAIL", "SBICARD", "SRF", "SYNGENE", "TATACOMM", "TATAPOWER", "TORNTPHARM", "TRENT", "TVSMOTOR", "UBL", "VOLTAS", "ZEEL", "ZYDUSLIFE", "CDSL", "IRFC", "IDEA", "YESBANK", "SUZLON", "ETERNAL", "DMART", "JIOFIN", "PAYTM", "NYKAA", "POLICYBZR", "DELHIVERY", "LODHA", "PATANJALI", "ABCAPITAL", "ALKEM", "APLAPOLLO", "ASHOKLEY", "ASTRAL", "ATUL", "BALKRISIND", "BATAINDIA", "BHARATFORG", "BHEL", "BSOFT", "CANFINHOME", "CROMPTON", "CUB", "DALBHARAT", "GLENMARK", "GMRAIRPORT", "GNFC", "GRANULES", "HAL", "HINDCOPPER", "HINDPETRO", "SAMMAANCAP", "IGL", "INDHOTEL", "INDIAMART", "IPCALAB", "JKCEMENT", "LALPATHLAB", "LAURUSLABS", "M&MFIN", "METROPOLIS", "NATIONALUM", "NAVINFLUOR", "OIL", "PVRINOX", "RAIN", "RBLBANK", "SUNTV", "TATACHEM", "TATAELXSI", "TORNTPOWER", "UNIONBANK", "VBL", "WHIRLPOOL", "AARTIIND", "ABFRL", "ANGELONE", "APOLLOTYRE", "AUBANK", "BANKINDIA", "BSE", "CGPOWER", "CHAMBLFERT", "COFORGE", "COROMANDEL", "DIXON", "FORTIS", "GICRE", "GODFRYPHLP", "GRAPHITE", "HFCL", "HUDCO", "IIFL", "INDIACEM", "IRB", "ITI", "KALYANKJIL", "KEI", "LTF", "MANKIND", "MAXHEALTH", "MGL", "MOTILALOFS", "NBCC", "NCC", "NHPC", "PFIZER", "PGEL", "POWERINDIA", "PRESTIGE", "RVNL", "SJVN", "SOLARINDS", "SONACOMS", "STARHEALTH", "SUPREMEIND", "TIINDIA", "TITAGARH", "VEDL", "ZFCVINDIA", "SHRIRAMFIN"]


def read_key():
    if os.path.exists(CRED_FILE):
        with open(CRED_FILE, "r", encoding="utf-8-sig") as f:
            for line in f:
                if line.strip().startswith("KOTAK_CONSUMER_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("KOTAK_CONSUMER_KEY", "").strip()


def main():
    key = read_key()
    if not key or key.upper().startswith("PUT_"):
        print("KOTAK_CONSUMER_KEY missing in " + CRED_FILE)
        sys.exit(1)
    client = NeoAPI(consumer_key=key, environment="prod")
    url = client.scrip_master("nse_cm")
    print("scrip master reply type:", type(url).__name__, str(url)[:150])
    if not isinstance(url, str):
        print("RESULT: could not get the scrip master file location")
        return
    text = httpx.get(url, timeout=120, follow_redirects=True).text
    rows = list(csv.DictReader(io.StringIO(text)))
    print("rows:", len(rows))
    if not rows:
        print("RESULT: scrip master was empty")
        return
    print("columns:", list(rows[0].keys())[:25])
    print("sample row:", {k: rows[0][k] for k in list(rows[0].keys())[:12]})

    def col(*names):
        low = {k.strip().lower(): k for k in rows[0].keys()}
        for n in names:
            if n.lower() in low:
                return low[n.lower()]
        return None

    c_tok = col("pSymbol")
    c_name = col("pSymbolName")
    c_trd = col("pTrdSymbol")
    c_grp = col("pGroup")
    print("using columns:", c_tok, c_name, c_trd, c_grp)

    by_name, by_trd = {}, {}
    for r in rows:
        grp = (r.get(c_grp) or "").strip().upper() if c_grp else "EQ"
        if grp not in ("EQ", ""):
            continue
        tok = (r.get(c_tok) or "").strip()
        if not tok:
            continue
        if c_name:
            by_name.setdefault((r.get(c_name) or "").strip().upper(), tok)
        if c_trd:
            by_trd.setdefault((r.get(c_trd) or "").strip().upper(), tok)

    mapping, missing = {}, []
    for s in SYMBOLS:
        u = s.upper()
        tok = by_name.get(u) or by_trd.get(u + "-EQ") or by_trd.get(u)
        if tok:
            mapping[s] = tok
        else:
            missing.append(s)
    with open("kotak_tokens.json", "w") as f:
        json.dump(mapping, f, indent=1)
    print("matched", len(mapping), "of", len(SYMBOLS))
    print("missing:", missing)
    print("RESULT: DONE")


if __name__ == "__main__":
    main()
