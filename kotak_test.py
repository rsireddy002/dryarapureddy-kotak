import asyncio
import os
import sys
import time

from neo_api_client import NeoAPI
from neo_api_client.websocket.feed import WsToken

STOCK = os.environ.get("TEST_STOCK", "RELIANCE")
COMMODITY = os.environ.get("TEST_COMMODITY", "CRUDEOILM")
SECONDS = int(os.environ.get("TEST_SECONDS", "20"))
CRED_FILE = "kotak_creds.txt"


def load_creds():
    """Read NAME=value lines from kotak_creds.txt (a plain text file that stays on this PC)."""
    creds = {}
    if os.path.exists(CRED_FILE):
        with open(CRED_FILE, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    creds[k.strip()] = v.strip().strip('"').strip("'")
    return creds


CREDS = load_creds()


def need(name):
    v = (CREDS.get(name) or os.environ.get(name, "")).strip()
    if not v or v.upper().startswith("PUT_"):
        print("Missing or not filled in: " + name + "  (edit " + CRED_FILE + ")")
        sys.exit(1)
    return v


def is_error(resp):
    return (not resp) or (isinstance(resp, dict) and (resp.get("error") or resp.get("Error Message") or resp.get("errors")))


def first_token(resp):
    data = resp.get("data") if isinstance(resp, dict) else resp
    if isinstance(data, dict):
        data = data.get("data") or data.get("scrips") or list(data.values())
    if not data:
        return None, None
    row = data[0] if isinstance(data, list) else data
    if isinstance(row, dict):
        tok = row.get("pSymbol") or row.get("instrument_token") or row.get("token")
        name = row.get("pTrdSymbol") or row.get("pSymbolName") or row.get("trading_symbol")
        return (str(tok) if tok else None), name
    return None, None


async def run_feed(client, tokens):
    seen = {}
    async with client.create_websocket() as ws:
        await ws.subscribe_scrips(tokens)
        end = time.time() + SECONDS
        async for msg in ws:
            d = getattr(msg, "model_dump", lambda: {})()
            ltp = d.get("last_traded_price")
            if ltp is not None:
                key = str(d.get("exchange_segment")) + "|" + str(d.get("instrument_token"))
                seen[key] = ltp
                print("  " + key + " (" + str(d.get("trading_symbol")) + "): LTP " + str(ltp))
            if time.time() > end:
                break
    return seen


def run_ws(client, tokens):
    return asyncio.run(run_feed(client, tokens))


def main():
    consumer_key = need("KOTAK_CONSUMER_KEY")
    mobile = need("KOTAK_MOBILE")
    ucc = need("KOTAK_UCC")
    mpin = need("KOTAK_MPIN")
    client = NeoAPI(consumer_key=consumer_key, environment="prod")

    totp = input("Enter the current 6-digit TOTP from your authenticator app: ").strip()
    try:
        r1 = client.totp_login(mobile_number=mobile, ucc=ucc, totp=totp)
        if is_error(r1):
            print("RESULT: LOGIN STEP 1 FAILED:", str(r1)[:300])
            return
        print("1. totp_login ok")
        r2 = client.totp_validate(mpin=mpin)
        if is_error(r2):
            print("RESULT: LOGIN STEP 2 (MPIN) FAILED:", str(r2)[:300])
            return
        print("2. totp_validate ok")
    except Exception as e:
        print("RESULT: LOGIN FAILED:", e)
        return

    try:
        print("3. IP seen by Kotak:", str(client.whatsmyip())[:300])
    except Exception as e:
        print("3. whatsmyip failed:", e)

    tokens = []
    for seg, sym in (("nse_cm", STOCK), ("mcx_fo", COMMODITY)):
        try:
            resp = client.search_scrip(exchange_segment=seg, symbol=sym)
            tok, name = first_token(resp)
            print("4. " + seg + " " + sym + ": token=" + str(tok) + " name=" + str(name))
            if tok:
                tokens.append(WsToken(seg, tok))
            else:
                print("   (could not read a token; raw reply:", str(resp)[:300], ")")
        except Exception as e:
            print("4. search_scrip " + seg + " " + sym + " failed:", e)

    if not tokens:
        print("RESULT: LOGIN OK but no tokens found for the WebSocket test")
        return

    print("5. WebSocket: subscribing to " + str(len(tokens)) + " instrument(s) for " + str(SECONDS) + "s ...")
    try:
        seen = run_ws(client, tokens)
    except Exception as e:
        print("RESULT: WEBSOCKET FAILED:", repr(e))
        return
    print("RESULT: OK, got live prices for", len(seen), "instrument(s):", seen)


if __name__ == "__main__":
    main()
