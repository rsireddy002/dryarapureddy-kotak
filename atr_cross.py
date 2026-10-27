"""
atr_cross.py - lower/upper ATR line crossings, with the time they happened.

RECLAIM (bullish): a candle's low went under the lower ATR line (previous close - 1 ATR), and a later
candle closed back at/above it. REJECT (bearish): a candle's high went over the upper ATR line
(previous close + 1 ATR), and a later candle closed back at/below it. The scan then asks for a validated
zone sitting at that line (support for a reclaim, resistance for a reject) and room to run to the next
zone on the other side. Pure pandas/plotly, no Streamlit; same file in the live and Kotak apps.
"""
import pandas as pd
import plotly.graph_objects as go

UP_COLOR = "#26A69A"
DOWN_COLOR = "#EF5350"
ZONE_TOL_PCT = 0.5     # a zone counts as "at" the line if the line is within its band +/- this %
MIN_ROOM_PCT = 1.0     # needs more than this % to the next zone on the other side (None = open air)


def _events(df, line, kind):
    if df is None or df.empty:
        return []
    out, armed = [], False
    for ts, hi, lo, cl in zip(df["timestamp"], df["high"], df["low"], df["close"]):
        if kind == "reclaim":
            if float(lo) < line:
                armed = True
            if armed and float(cl) >= line:
                out.append((ts, line)); armed = False
        else:
            if float(hi) > line:
                armed = True
            if armed and float(cl) <= line:
                out.append((ts, line)); armed = False
    return out


def reclaim_events(df, atr_info):
    if not atr_info:
        return []
    atr, pc = atr_info
    return _events(df, float(pc) - float(atr), "reclaim")


def reject_events(df, atr_info):
    if not atr_info:
        return []
    atr, pc = atr_info
    return _events(df, float(pc) + float(atr), "reject")


def add_cross_markers(fig, df, atr_info, compact=False):
    """Green ▲ (reclaim) / red ▼ (reject) on the ATR line at each crossing candle, labelled with its time.
    Works on a fresh copy-safe figure; returns it."""
    for events, kind in ((reclaim_events(df, atr_info), "reclaim"), (reject_events(df, atr_info), "reject")):
        if not events:
            continue
        up = kind == "reclaim"
        xs = [e[0] for e in events]
        ys = [float(e[1]) for e in events]
        txt = [f"ATR {kind} {pd.Timestamp(t).strftime('%H:%M')}" for t in xs]
        col = UP_COLOR if up else DOWN_COLOR
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="markers+text", text=txt,
            textposition="bottom center" if up else "top center",
            textfont=dict(size=9 if compact else 11, color=col),
            marker=dict(symbol="triangle-up" if up else "triangle-down", size=11 if compact else 14,
                        color=col, line=dict(color="white", width=1)),
            name=f"ATR {kind}", showlegend=False,
            hovertemplate="%{text}<br>%{y:.2f}<extra></extra>"))
    return fig


def _zone_at_line(zones, line, ltp, want_support):
    tol = line * ZONE_TOL_PCT / 100
    best = None
    for z in zones or []:
        try:
            lo, hi, mode = float(z["price_low"]), float(z["price_high"]), float(z["price_mode"])
        except (TypeError, ValueError, KeyError):
            continue
        if (mode <= ltp) != want_support:
            continue
        if lo - tol <= line <= hi + tol and (best is None or abs(mode - line) < abs(float(best["price_mode"]) - line)):
            best = z
    return best


def _room(zones, ltp, below):
    """(level, % distance from ltp to its near edge) of the nearest zone on one side, or (None, None)."""
    best = (None, None)
    for z in zones or []:
        try:
            lo, hi, mode = float(z["price_low"]), float(z["price_high"]), float(z["price_mode"])
        except (TypeError, ValueError, KeyError):
            continue
        if below and mode <= ltp:
            d = (ltp - hi) / ltp * 100
        elif not below and mode > ltp:
            d = (lo - ltp) / ltp * 100
        else:
            continue
        if best[1] is None or d < best[1]:
            best = (mode, round(d, 2))
    return best


def scan_symbol(symbol, df, atr_info, validated_zones):
    """Returns {"reclaim": row|None, "reject": row|None} for one symbol (rows already room-filtered)."""
    out = {"reclaim": None, "reject": None}
    if df is None or df.empty or not atr_info:
        return out
    atr, pc = float(atr_info[0]), float(atr_info[1])
    ltp = float(df["close"].iloc[-1])
    lower, upper = pc - atr, pc + atr
    ev = reclaim_events(df, atr_info)
    if ev and ltp >= lower:
        z = _zone_at_line(validated_zones, lower, ltp, want_support=True)
        nxt, room = _room(validated_zones, ltp, below=False)
        if z is not None and (room is None or room > MIN_ROOM_PCT):
            out["reclaim"] = {
                "Symbol": symbol, "Time": pd.Timestamp(ev[-1][0]).strftime("%H:%M"), "LTP": round(ltp, 2),
                "Lower ATR": round(lower, 2), "Above line %": round((ltp - lower) / lower * 100, 2),
                "Support": round(float(z["price_mode"]), 2), "Next resistance": nxt, "Room to run %": room,
                "Upper ATR": round(upper, 2), "To upper ATR %": round((upper - ltp) / ltp * 100, 2)}
    ev = reject_events(df, atr_info)
    if ev and ltp <= upper:
        z = _zone_at_line(validated_zones, upper, ltp, want_support=False)
        nxt, room = _room(validated_zones, ltp, below=True)
        if z is not None and (room is None or room > MIN_ROOM_PCT):
            out["reject"] = {
                "Symbol": symbol, "Time": pd.Timestamp(ev[-1][0]).strftime("%H:%M"), "LTP": round(ltp, 2),
                "Upper ATR": round(upper, 2), "Below line %": round((upper - ltp) / upper * 100, 2),
                "Resistance": round(float(z["price_mode"]), 2), "Next support": nxt, "Room to fall %": room,
                "Lower ATR": round(lower, 2), "To lower ATR %": round((ltp - lower) / ltp * 100, 2)}
    return out
