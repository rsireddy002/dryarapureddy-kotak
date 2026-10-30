"""Volume-at-price (estimated buy/sell split) and order-book depth views for the Chart tab.

Neither needs a live tick feed:
  * candle_volume_profile(): spreads each 1-minute candle's volume over its price range and splits it into buy
    and sell by where the candle closed inside its range. An ESTIMATE -- real bid/ask trade data is not in
    Upstox's candle API -- so it is always labelled approximate on screen.
  * record_depth_snapshot(): keeps the order-book depth (best 5 bids/asks with order counts, plus total buy/sell
    quantity) that Upstox's quote response already carries, once per scan, so a day's history builds up in memory
    while the app is running. It is what is WAITING to trade, not what traded.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go

_IST = timezone(timedelta(hours=5, minutes=30))

_DEPTH_HISTORY = {}          # symbol -> list of snapshot dicts (oldest first)
_DEPTH_LOCK = threading.Lock()
_DEPTH_MAX_SNAPSHOTS = 400   # ~ a full day at one per minute
_DEPTH_MIN_GAP_SECONDS = 30


# ----------------------------------------------------------------------------------------------------------------
# Volume at price
# ----------------------------------------------------------------------------------------------------------------
def candle_volume_profile(df, n_bins=40):
    """df: candles with open/high/low/close/volume. Returns a DataFrame with one row per price bin:
    price (bin middle), buy, sell, total, delta (buy - sell). Volume of a candle is spread evenly over its
    low..high range; the buy share is (close - low) / (high - low), i.e. a candle that closes at its high is all
    buying, one that closes at its low is all selling (0.5 for a flat candle)."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["price", "buy", "sell", "total", "delta"])
    d = df.dropna(subset=["high", "low", "close", "volume"])
    lo, hi = float(d["low"].min()), float(d["high"].max())
    if hi <= lo:
        hi = lo + max(lo * 0.001, 0.01)
    edges = np.linspace(lo, hi, n_bins + 1)
    buy = np.zeros(n_bins)
    sell = np.zeros(n_bins)
    for h, l, c, v in zip(d["high"].to_numpy(float), d["low"].to_numpy(float),
                          d["close"].to_numpy(float), d["volume"].to_numpy(float)):
        if v <= 0:
            continue
        rng = h - l
        frac = 0.5 if rng <= 0 else min(max((c - l) / rng, 0.0), 1.0)
        if rng <= 0:
            i = min(max(int(np.searchsorted(edges, l, side="right") - 1), 0), n_bins - 1)
            buy[i] += v * frac
            sell[i] += v * (1 - frac)
            continue
        overlap = np.clip(np.minimum(edges[1:], h) - np.maximum(edges[:-1], l), 0, None)
        tot = overlap.sum()
        if tot <= 0:
            continue
        share = overlap / tot * v
        buy += share * frac
        sell += share * (1 - frac)
    mid = (edges[:-1] + edges[1:]) / 2
    out = pd.DataFrame({"price": mid, "buy": buy, "sell": sell})
    out["total"] = out["buy"] + out["sell"]
    out["delta"] = out["buy"] - out["sell"]
    return out


def volume_profile_fig(profile, last_price=None, title="Volume at price (estimated buy / sell)", height=520):
    fig = go.Figure()
    if profile is None or profile.empty:
        return fig
    step = float(profile["price"].iloc[1] - profile["price"].iloc[0]) if len(profile) > 1 else 1.0
    fig.add_bar(y=profile["price"], x=profile["buy"], orientation="h", name="Buy (est.)",
                marker_color="#26a69a", width=step * 0.9)
    fig.add_bar(y=profile["price"], x=-profile["sell"], orientation="h", name="Sell (est.)",
                marker_color="#ef5350", width=step * 0.9)
    poc = float(profile.loc[profile["total"].idxmax(), "price"])
    fig.add_hline(y=poc, line_dash="dot", line_color="#e0a336", line_width=1,
                  annotation_text=f"most volume {poc:g}", annotation_position="top right",
                  annotation_font_size=10, annotation_font_color="#e0a336")
    if last_price:
        fig.add_hline(y=float(last_price), line_color="#4c8bf5", line_width=1,
                      annotation_text=f"LTP {float(last_price):g}", annotation_position="bottom right",
                      annotation_font_size=10, annotation_font_color="#4c8bf5")
    fig.update_layout(barmode="relative", height=height, title=title, margin=dict(l=10, r=10, t=40, b=10),
                      legend=dict(orientation="h", y=1.04), bargap=0.05,
                      xaxis_title="sell  <  volume  >  buy", yaxis_title="price")
    return fig


def volume_profile_table(profile, top_n=8):
    """The price levels with the most traded volume, with the estimated buy/sell split."""
    if profile is None or profile.empty:
        return pd.DataFrame()
    t = profile.sort_values("total", ascending=False).head(top_n).copy()
    t["buy %"] = (t["buy"] / t["total"] * 100).round(0)
    t = t.rename(columns={"price": "Price", "buy": "Buy vol", "sell": "Sell vol", "total": "Total", "delta": "Delta"})
    for col in ("Buy vol", "Sell vol", "Total", "Delta"):
        t[col] = t[col].round(0).astype(int)
    t["Price"] = t["Price"].round(2)
    return t[["Price", "Buy vol", "Sell vol", "Delta", "buy %"]].reset_index(drop=True)


# ----------------------------------------------------------------------------------------------------------------
# Order-book depth
# ----------------------------------------------------------------------------------------------------------------
def _levels(side):
    out = []
    for lv in (side or [])[:5]:
        try:
            out.append({"price": float(lv.get("price") or 0), "quantity": int(lv.get("quantity") or 0),
                        "orders": int(lv.get("orders") or 0)})
        except (TypeError, ValueError):
            continue
    return out


def record_depth_snapshot(symbol, quote):
    """Store one depth snapshot from an Upstox quote dict (depth.buy / depth.sell, total_buy_quantity,
    total_sell_quantity). No-op when the quote carries no depth. Safe to call from any thread."""
    depth = (quote or {}).get("depth") or {}
    buys, sells = _levels(depth.get("buy")), _levels(depth.get("sell"))
    if not buys and not sells:
        return
    now = time.time()
    with _DEPTH_LOCK:
        hist = _DEPTH_HISTORY.setdefault(symbol, [])
        if hist and now - hist[-1]["epoch"] < _DEPTH_MIN_GAP_SECONDS:
            return
        hist.append({
            "epoch": now,
            "time": datetime.now(_IST).strftime("%H:%M:%S"),
            "ltp": quote.get("last_price"),
            "buy": buys, "sell": sells,
            "buy_qty5": sum(l["quantity"] for l in buys), "sell_qty5": sum(l["quantity"] for l in sells),
            "buy_orders5": sum(l["orders"] for l in buys), "sell_orders5": sum(l["orders"] for l in sells),
            "tbq": quote.get("total_buy_quantity"), "tsq": quote.get("total_sell_quantity"),
        })
        if len(hist) > _DEPTH_MAX_SNAPSHOTS:
            del hist[: len(hist) - _DEPTH_MAX_SNAPSHOTS]


def depth_history(symbol):
    with _DEPTH_LOCK:
        return list(_DEPTH_HISTORY.get(symbol, []))


def depth_levels_df(snapshot):
    """Latest snapshot as a table: best 5 bids and asks side by side."""
    if not snapshot:
        return pd.DataFrame()
    rows = []
    for i in range(5):
        b = snapshot["buy"][i] if i < len(snapshot["buy"]) else {}
        s = snapshot["sell"][i] if i < len(snapshot["sell"]) else {}
        rows.append({"Bid orders": b.get("orders"), "Bid qty": b.get("quantity"), "Bid price": b.get("price"),
                     "Ask price": s.get("price"), "Ask qty": s.get("quantity"), "Ask orders": s.get("orders")})
    return pd.DataFrame(rows)


def depth_imbalance_pct(snapshot):
    """Share of the best-5 resting quantity that is on the bid side (50 = balanced)."""
    if not snapshot:
        return None
    tot = snapshot["buy_qty5"] + snapshot["sell_qty5"]
    return None if tot <= 0 else snapshot["buy_qty5"] / tot * 100


def depth_history_fig(hist, height=260):
    fig = go.Figure()
    pts = [(h["time"], depth_imbalance_pct(h)) for h in hist]
    pts = [(t, v) for t, v in pts if v is not None]
    if not pts:
        return fig
    fig.add_scatter(x=[p[0] for p in pts], y=[p[1] for p in pts], mode="lines+markers",
                    name="bid share of best-5 qty", line=dict(color="#26a69a"))
    fig.add_hline(y=50, line_dash="dot", line_color="#888", line_width=1)
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10),
                      title="Resting bid share of the best 5 prices over the day (above 50 = more waiting to buy)",
                      yaxis=dict(range=[0, 100], title="% bids"), xaxis_title="snapshot time (IST)")
    return fig


# ----------------------------------------------------------------------------------------------------------------
# Plain-language reading of the two views
# ----------------------------------------------------------------------------------------------------------------
def value_area(profile, share=0.70):
    """(val, vah, poc): the price range holding `share` of the day's volume, built by adding the busiest bins until
    the share is reached, plus the busiest price (poc)."""
    if profile is None or profile.empty or profile["total"].sum() <= 0:
        return None
    p = profile.sort_values("total", ascending=False)
    cum = p["total"].cumsum() / p["total"].sum()
    inside = p[cum.shift(fill_value=0) < share]
    return float(inside["price"].min()), float(inside["price"].max()), float(p["price"].iloc[0])


def recent_buy_share(df, minutes=30):
    """Estimated buy share (0-1) of volume in the last `minutes` minutes (same close-in-range rule as the profile)."""
    if df is None or df.empty:
        return None
    d = df
    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"])
        d = df[ts >= ts.max() - pd.Timedelta(minutes=minutes)]
    if d.empty:
        return None
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    frac = ((d["close"] - d["low"]) / rng).fillna(0.5).clip(0, 1)
    vol = d["volume"].astype(float)
    return None if vol.sum() <= 0 else float((frac * vol).sum() / vol.sum())


def order_flow_read(profile, last_price, candles, depth_hist, zones=None):
    """List of dicts {Check, Reading, Leans} -- a reading aid built from the volume profile and the order book.
    NOT a signal: these rules are common order-flow conventions and have not been tested on your data the way the
    zone checks were."""
    rows = []

    def add(check, reading, leans):
        rows.append({"Check": check, "Reading": reading, "Leans": leans})

    if profile is not None and not profile.empty and last_price:
        va = value_area(profile)
        tot = float(profile["total"].sum())
        day_buy = float(profile["buy"].sum() / tot) if tot > 0 else None
        if va:
            val, vah, poc = va
            if last_price > vah:
                add("Price vs value area", f"Above the range where 70% of volume traded ({val:g} to {vah:g}): price is "
                    "trading higher than most of today's business; holds if buyers keep defending the top of that range.", "bullish")
            elif last_price < val:
                add("Price vs value area", f"Below the range where 70% of volume traded ({val:g} to {vah:g}): price is "
                    "trading lower than most of today's business; stays weak while it cannot get back inside.", "bearish")
            else:
                add("Price vs value area", f"Inside the range where 70% of volume traded ({val:g} to {vah:g}): "
                    "balanced; no edge from this check alone.", "neutral")
            d = (poc - last_price) / last_price * 100
            add("Busiest price", f"{poc:g}, {abs(d):.1f}% {'above' if d > 0 else 'below'} price. Price tends to gravitate "
                "back to it when moves lack follow-through.", "neutral")
        if day_buy is not None:
            lean = "bullish" if day_buy >= 0.55 else ("bearish" if day_buy <= 0.45 else "neutral")
            add("Estimated buying today", f"{day_buy * 100:.0f}% of volume looks like buying (by candle closes).", lean)
        busy = profile[profile["total"] >= profile["total"].quantile(0.8)]
        below = busy[busy["price"] < last_price]
        above = busy[busy["price"] > last_price]
        if not below.empty:
            r = below.loc[below["price"].idxmax()]
            share = r["buy"] / r["total"] * 100
            add("Nearest heavy volume below", f"{r['price']:g} ({(last_price - r['price']) / last_price * 100:.1f}% down), "
                f"{share:.0f}% estimated buying. Heavy volume with buying behind it is where support is likelier.",
                "bullish" if share >= 52 else "neutral")
        if not above.empty:
            r = above.loc[above["price"].idxmin()]
            share = r["sell"] / r["total"] * 100
            add("Nearest heavy volume above", f"{r['price']:g} ({(r['price'] - last_price) / last_price * 100:.1f}% up), "
                f"{share:.0f}% estimated selling. Heavy volume with selling behind it is where resistance is likelier.",
                "bearish" if share >= 52 else "neutral")
        for z in (zones or []):
            try:
                lo, hi = float(z["price_low"]), float(z["price_high"])
            except (TypeError, ValueError, KeyError):
                continue
            if abs((lo + hi) / 2 - last_price) / last_price > 0.01:
                continue
            node = busy[(busy["price"] >= lo) & (busy["price"] <= hi)]
            kind = "support" if (lo + hi) / 2 <= last_price else "resistance"
            if not node.empty:
                add(f"Zone vs volume ({kind} {lo:g}-{hi:g})", "A heavy-volume level sits inside this zone, so today's "
                    "trading backs the zone.", "bullish" if kind == "support" else "bearish")
            else:
                add(f"Zone vs volume ({kind} {lo:g}-{hi:g})", "No heavy-volume level inside this zone today; it rests "
                    "on the 18-day history only.", "neutral")
    rb = recent_buy_share(candles)
    if rb is not None:
        lean = "bullish" if rb >= 0.58 else ("bearish" if rb <= 0.42 else "neutral")
        add("Last 30 minutes", f"About {rb * 100:.0f}% of the latest volume looks like buying. Compare with the "
            "day figure: a rising share means buyers are stepping up.", lean)

    if depth_hist:
        last = depth_hist[-1]
        imb = depth_imbalance_pct(last)
        if imb is not None:
            lean = "bullish" if imb >= 58 else ("bearish" if imb <= 42 else "neutral")
            add("Order book, best 5 prices", f"{imb:.0f}% of the waiting quantity is on the bid side. More waiting "
                "buyers than sellers near price; can be pulled at any moment.", lean)
        tbq, tsq = last.get("tbq"), last.get("tsq")
        if tbq and tsq:
            ratio = tbq / tsq
            lean = "bullish" if ratio >= 1.2 else ("bearish" if ratio <= 0.83 else "neutral")
            add("Whole order book", f"Total waiting buy quantity is {ratio:.2f} times the sell quantity.", lean)
        if len(depth_hist) >= 6:
            ims = [depth_imbalance_pct(h) for h in depth_hist[-6:]]
            ims = [v for v in ims if v is not None]
            if len(ims) >= 6:
                shift = sum(ims[-3:]) / 3 - sum(ims[:3]) / 3
                if abs(shift) >= 8:
                    add("Order book trend", f"Bid share moved {shift:+.0f} points over the last few snapshots.",
                        "bullish" if shift > 0 else "bearish")
    return rows


def read_summary(rows):
    """One line: how many checks lean each way."""
    n = {"bullish": 0, "bearish": 0, "neutral": 0}
    for r in rows:
        n[r["Leans"]] = n.get(r["Leans"], 0) + 1
    return f"{n['bullish']} lean bullish, {n['bearish']} lean bearish, {n['neutral']} neutral"
