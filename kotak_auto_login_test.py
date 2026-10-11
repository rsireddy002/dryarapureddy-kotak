import asyncio, json, time
import kotak_feed
from neo_api_client.websocket.feed import WsToken

client = kotak_feed.login()
print("AUTO LOGIN OK (no TOTP typed)")
tok = json.load(open("kotak_tokens.json"))["RELIANCE"]

async def go():
    async with client.create_websocket() as ws:
        await ws.subscribe_scrips([WsToken("nse_cm", tok)])
        end = time.time() + 15
        async for msg in ws:
            d = getattr(msg, "model_dump", lambda: {})()
            if d.get("last_traded_price") is not None:
                print("RELIANCE LTP:", d["last_traded_price"]); return
            if time.time() > end:
                print("no tick in 15s (normal when market is closed)"); return

asyncio.run(go())
