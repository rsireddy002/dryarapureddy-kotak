# dryarapureddy-kotak
Copy of the Dr Yarapu Reddy Levels app whose live quotes/candles come from the Kotak Neo feed
(runs inside the app as a background thread). Falls back to Upstox REST when the feed is not live.

## Deploy on Streamlit Cloud
New app -> repo `rsireddy002/dryarapureddy-kotak`, branch `main`, file `app.py`.
Settings -> Secrets (type these yourself, never commit them):

    UPSTOX_ACCESS_TOKEN = "analytical token (candle history)"
    app_password = "..."
    KOTAK_CONSUMER_KEY = "..."
    KOTAK_MOBILE = "+91..."
    KOTAK_UCC = "..."
    KOTAK_MPIN = "..."
    # optional, for automatic login (needs TOTP registered with Kotak):
    KOTAK_TOTP_SECRET = "..."

Without `KOTAK_TOTP_SECRET` the sidebar shows a box to type today's 6-digit code.
Also add repo secret `UPSTOX_ANALYTICAL_TOKEN` (GitHub Actions) so levels refresh nightly.
Feed runs Mon-Fri 08:50-15:45 IST; Streamlit Cloud sleeps idle apps, so open the app to wake it.
