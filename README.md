# dryarapureddy-kotak

Separate from the Upstox apps. Goal: get a live price feed (NSE stocks + MCX commodities) from Kotak Neo's
Trade API WebSocket, then feed it to the levels apps.

Step 1 (now): `kotak_test.py` -- login + scrip search + 20-second live feed test. Read-only, places no orders.
Credentials only in environment variables (never committed, never pasted in chat).
