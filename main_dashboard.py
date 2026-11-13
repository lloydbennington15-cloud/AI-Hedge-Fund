"""
main_dashboard.py - the tournament's web page (viewer and control panel).

    streamlit run main_dashboard.py

The trading itself happens in engine.py. This page only shows what the engine
has saved. It works in two ways and picks the right one by itself:

* GitHub mode (Streamlit Community Cloud): the engine runs on GitHub Actions
  and publishes its files to the `tournament-data` branch of the repository.
  This page reads them from there. It is view-only; the engine is controlled
  from the repository's Actions tab.

* Local mode (your own PC or server): the engine runs on the same machine.
  This page reads its files directly and can start, pause or reset it.

Opening the page in several tabs or devices at once is always safe.
"""

import hmac
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import engine as E

try:
    from zoneinfo import ZoneInfo
    UK = ZoneInfo("Europe/London")
except Exception:
    UK = timezone.utc

# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------
DEFAULT_GITHUB_REPO = "lloydbennington15-cloud/AI-Hedge-Fund"
DATA_BRANCH = "tournament-data"

COLORS = {"Gemini": "#10b981", "Claude": "#a855f7", "ChatGPT": "#f97316",
          "Grok": "#6b7280", "Fable": "#3b82f6", "Lloyd": "#f472b6"}
ICONS = {"Gemini": "🤖", "Claude": "🟪", "ChatGPT": "🟠", "Grok": "⚫", "Fable": "🔷",
         "Lloyd": "🕹️"}
HUMAN = getattr(E, "HUMAN_PLAYER", None)
SPARE_COLORS = ["#ef4444", "#eab308", "#14b8a6", "#ec4899"]
BOT_NAMES = [b[0] for b in E.BOTS]
for _i, _name in enumerate(n for n in BOT_NAMES if n not in COLORS):
    COLORS[_name] = SPARE_COLORS[_i % len(SPARE_COLORS)]

st.set_page_config(page_title="AI Trading Tournament", page_icon="🏛️",
                   layout="wide", initial_sidebar_state="auto")
st.markdown("""<style>
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"], .block-container {
    padding-left: 0.7rem !important; padding-right: 0.7rem !important; padding-top: 2.6rem !important; }
  h1 { font-size: 1.55rem !important; }
  h2, h3 { font-size: 1.2rem !important; }
  [data-testid="stMetricValue"] { font-size: 1.35rem !important; }
}
</style>""", unsafe_allow_html=True)


def github_repo():
    """owner/name of the repository this page was deployed from."""
    if os.environ.get("TOURNAMENT_GITHUB_REPO"):
        return os.environ["TOURNAMENT_GITHUB_REPO"]
    try:
        with open(os.path.join(E.BASE_DIR, ".git", "config"), "r", encoding="utf-8") as f:
            found = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?\s*$", f.read(), re.M)
        if found:
            return found.group(1)
    except Exception:
        pass
    return DEFAULT_GITHUB_REPO


MODE = os.environ.get("TOURNAMENT_MODE", "").lower()
if MODE not in ("github", "local"):
    MODE = "github" if E.BASE_DIR.startswith("/mount/src") else "local"   # Streamlit Cloud path
REPO = github_repo()
STATE_URL = (os.environ.get("TOURNAMENT_STATE_URL")
             or f"https://raw.githubusercontent.com/{REPO}/{DATA_BRANCH}/tournament_state.json")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def uk_time(text, fmt="%a %d %b %H:%M"):
    dt = E.parse_iso(text) if text else None
    return dt.astimezone(UK).strftime(fmt) if dt else "-"


def money(x):
    return "-" if x is None else f"${x:,.2f}"


@st.cache_data(ttl=60, show_spinner=False)
def fetch_remote_state(url):
    request = urllib.request.Request(url, headers={"User-Agent": "tournament-dashboard"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8")), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def load_state():
    if MODE == "github":
        return fetch_remote_state(STATE_URL)
    return E.read_json(E.STATE_FILE), None


def heartbeat_age(state):
    hb = E.parse_iso((state or {}).get("engine", {}).get("heartbeat_utc"))
    return (E.utc_now() - hb).total_seconds() if hb else None


def engine_running():
    age = E.lock_age()
    return age is not None and age < E.ENGINE_STALE_SECONDS


def start_engine():
    out = open(E.ENGINE_STDOUT, "a")
    kwargs = {"cwd": E.BASE_DIR, "stdout": out, "stderr": subprocess.STDOUT,
              "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200   # detached, own process group
    else:
        kwargs["start_new_session"] = True                 # keeps running if Streamlit stops
    subprocess.Popen([sys.executable, os.path.join(E.BASE_DIR, "engine.py")], **kwargs)


def send_command(command):
    E.atomic_write_json(E.COMMAND_FILE, {"id": uuid.uuid4().hex, "command": command,
                                         "sent_utc": E.iso(E.utc_now())})


# -----------------------------------------------------------------------------
# Sidebar
# -----------------------------------------------------------------------------
st.sidebar.title("🏛️ Tournament Control")


def github_sidebar():
    state, problem = load_state()
    actions_url = f"https://github.com/{REPO}/actions"
    age = heartbeat_age(state)
    market_open = E.market_status(E.utc_now())["is_open"]
    if state is None:
        st.sidebar.warning("No tournament data published yet.")
    elif market_open and age is not None and age > 20 * 60:
        st.sidebar.error(f"🔴 Market is open but the last update was {age / 60:.0f} min ago. "
                         "Check the Actions page.")
    elif market_open:
        st.sidebar.success("🟢 Engine running on GitHub")
    else:
        st.sidebar.info("😴 Market closed. The engine starts by itself about an hour "
                        "before the next open.")
    st.sidebar.markdown(
        f"The engine runs on **GitHub Actions**.\n\n"
        f"[Open the Actions page]({actions_url}) to:\n"
        f"- **restart** it: *Tournament engine → Run workflow → run*\n"
        f"- **reset** everyone to ${E.STARTING_CASH:.0f}: *Run workflow → reset*\n"
        f"- **stop** the tournament: *⋯ → Disable workflow*")
    st.sidebar.caption("This page shows saved results, updated every few minutes. "
                       "Closing it does not stop anything.")
    if st.sidebar.button("🔄 Refresh now"):
        fetch_remote_state.clear()
        st.rerun()


def is_unlocked():
    return not os.environ.get("TOURNAMENT_PASSWORD", "") or bool(st.session_state.get("unlocked"))


def controls_unlocked(where=None, key="pw_sidebar"):
    """Anyone can view the page. If TOURNAMENT_PASSWORD is set on the server,
    the controls (start, pause, reload, reset) and trading need that password."""
    if is_unlocked():
        return True
    where = where or st.sidebar
    password = os.environ.get("TOURNAMENT_PASSWORD", "")
    entered = where.text_input("🔒 Password to unlock the controls", type="password", key=key)
    if entered and hmac.compare_digest(entered.encode(), password.encode()):
        st.session_state["unlocked"] = True
        st.rerun()
    if entered:
        where.error("Wrong password")
    return False


def local_sidebar():
    if E.AUTO_START_FROM_DASHBOARD and not engine_running():
        last_try = st.session_state.get("engine_start_attempt", 0)
        if time.time() - last_try > 90:
            st.session_state["engine_start_attempt"] = time.time()
            try:
                start_engine()
                st.sidebar.info("Engine wasn't running - starting it now…")
            except Exception as e:
                st.sidebar.error(f"Couldn't start the engine: {e}")

    age = E.lock_age()
    unlocked = controls_unlocked()
    if engine_running():
        st.sidebar.success(f"🟢 Engine running (last heartbeat {age:.0f}s ago)")
    else:
        st.sidebar.error("🔴 Engine not running" + (f" (last seen {age / 60:.0f} min ago)" if age else ""))
        if unlocked and st.sidebar.button("▶️ Start engine"):
            start_engine()
            st.session_state["engine_start_attempt"] = time.time()
            st.rerun()

    paused = (E.read_json(E.STATE_FILE) or {}).get("engine", {}).get("paused", False)
    if paused:
        st.sidebar.warning("⏸️ Trading is paused")
    if unlocked:
        if paused:
            if st.sidebar.button("▶️ Resume trading"):
                send_command("resume")
                st.sidebar.info("Resume sent - takes effect within one cycle.")
        elif st.sidebar.button("⏸️ Pause trading"):
            send_command("pause")
            st.sidebar.info("Pause sent - takes effect within one cycle.")
        if st.sidebar.button("🔁 Reload bot files now"):
            send_command("reload_bots")
            st.sidebar.info("Reload sent. (Changed bot files are also picked up automatically.)")
        with st.sidebar.expander("⚠️ Reset tournament"):
            st.write(f"Puts every bot back to ${E.STARTING_CASH:.0f} with no positions, "
                     "clears the history and the bots' saved memory.")
            sure = st.checkbox("I understand this can't be undone")
            if st.button("Reset everything", disabled=not sure):
                send_command("reset")
                st.success("Reset sent - takes effect within one cycle.")
    else:
        st.sidebar.caption("Controls are locked - viewing only.")
    st.sidebar.caption("Closing this page does not stop the tournament.")


if MODE == "github":
    github_sidebar()
else:
    local_sidebar()
auto = st.sidebar.toggle("Auto-refresh this page", value=True)


# -----------------------------------------------------------------------------
# The arcade panel: an animated summary drawn from the same saved data
# -----------------------------------------------------------------------------
ARCADE_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#05050f;color:#fff;font-family:'Press Start 2P',ui-monospace,Menlo,Consolas,monospace}
@keyframes step{0%,49%{transform:translateY(0)}50%,100%{transform:translateY(-3px)}}
@keyframes jog{0%,100%{transform:translateX(0)}50%{transform:translateX(6px)}}
@keyframes climb{0%,49%{transform:translateY(0) scale(.8)}50%,100%{transform:translateY(-5px) scale(.8)}}
@keyframes blink{0%,60%{opacity:1}61%,100%{opacity:0}}
@keyframes snooze{0%{transform:translate(0,0);opacity:0}30%{opacity:1}100%{transform:translate(10px,-20px);opacity:0}}
@keyframes ticker{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.wrap{position:relative;height:100vh;padding:16px 22px 44px 22px;overflow:hidden}
.top{display:flex;justify-content:space-between;align-items:center;gap:6px 12px;flex-wrap:wrap}
.title{font-size:16px;color:#facc15}
.mid{font-size:9px;color:#cbd5e1}
.sign{font-size:11px}
.open{color:#4ade80;animation:blink 1.4s steps(1) infinite}
.shut{color:#f87171}
.key{margin-top:10px;font-size:8px;line-height:13px;color:#94a3b8}
.track{position:relative;margin-top:8px;padding-top:14px}
.start{position:absolute;top:0;bottom:0;width:0;border-left:3px dashed #22d3ee}
.startlab{position:absolute;top:-2px;font-size:8px;color:#22d3ee;background:#05050f;padding:0 4px;transform:translateX(-50%);white-space:nowrap}
.flag{position:absolute;right:0;top:14px;bottom:0;width:20px;background-image:repeating-conic-gradient(#fff 0 25%,#05050f 0 50%);background-size:20px 20px}
.lane{position:relative;height:88px}
.girder{position:absolute;left:200px;right:28px;top:52px;height:12px;background:repeating-linear-gradient(90deg,#e11d48 0 22px,#fb7185 22px 26px);border-top:3px solid #fda4af;border-bottom:3px solid #9f1239}
.who{position:absolute;left:0;top:10px;width:192px;font-size:11px;line-height:17px;white-space:nowrap;overflow:hidden;filter:brightness(1.3)}
.money{font-size:9px;color:#fff}
.down{color:#fca5a5}
.doing{position:absolute;left:200px;right:30px;top:71px;font-size:9px;line-height:13px;white-space:nowrap;overflow:hidden;filter:brightness(1.45)}
.alert{color:#fca5a5 !important;animation:blink 1.4s steps(1) infinite}
.bot{position:absolute;top:14px;width:30px;height:38px;animation:jog 1.2s steps(4) infinite}
.head{position:absolute;left:4px;top:0;width:22px;height:14px;background:currentColor}
.eye{position:absolute;top:4px;width:4px;height:5px;background:#fff}
.body{position:absolute;left:1px;top:16px;width:28px;height:13px;background:currentColor;filter:brightness(.8)}
.leg{position:absolute;top:31px;width:8px;height:7px;background:currentColor;animation:step .4s steps(1) infinite}
.busy{animation-duration:.7s}.busy .leg{animation-duration:.25s}
.calm{animation-duration:2s}.calm .leg{animation-duration:.7s}
.asleep,.asleep .leg{animation:none}
.asleep .eye{height:2px;top:7px}
.zed{position:absolute;left:30px;top:-6px;font-size:10px;color:#e2e8f0;animation:snooze 2.4s linear infinite}
.crates{position:absolute;left:34px;top:10px;display:flex;gap:4px}
.crate{height:18px;border:3px solid #fde68a;background:#b45309;color:#fef3c7;font-size:7px;line-height:12px;padding:0 3px;white-space:nowrap}
.iou{background:#1e1b4b;border-color:#a5b4fc;color:#e0e7ff}
.tick{position:absolute;left:0;right:0;bottom:0;height:34px;overflow:hidden;background:#111827;border-top:3px solid #facc15}
.tick div{display:flex;width:max-content;font-size:9px;line-height:31px;color:#fde68a;animation:ticker 40s linear infinite}
.tick span{padding-right:48px;white-space:nowrap}
.tall{display:none}
.tower{position:relative;margin-top:10px}
.finish{position:absolute;left:0;right:0;top:0;height:12px;background-image:repeating-conic-gradient(#fff 0 25%,#05050f 0 50%);background-size:12px 12px}
.ground{position:absolute;left:0;right:0;bottom:30px;height:10px;background:repeating-linear-gradient(90deg,#e11d48 0 18px,#fb7185 18px 21px);border-top:2px solid #fda4af;border-bottom:2px solid #9f1239}
.tstart{position:absolute;left:0;right:0;height:0;border-top:2px dashed #22d3ee}
.tstartlab{position:absolute;left:0;font-size:6px;color:#22d3ee;background:#05050f;padding:0 2px;transform:translateY(-110%)}
.col{position:absolute;top:0;bottom:0}
.ladder{position:absolute;left:50%;margin-left:-12px;width:24px;top:14px;bottom:40px;background:repeating-linear-gradient(180deg,transparent 0 9px,#22d3ee 9px 12px);border-left:3px solid #22d3ee;border-right:3px solid #22d3ee;opacity:.75}
.tbot{position:absolute;left:50%;margin-left:-15px;width:30px;height:38px;transform-origin:bottom center;transform:scale(.8);animation:climb 1.2s steps(1) infinite}
.tbot.busy{animation-duration:.5s}.tbot.calm{animation-duration:1.6s}
.tbot.asleep{animation:none}
.tbot .zed{left:24px}
.cname{position:absolute;left:0;right:0;bottom:12px;text-align:center;font-size:7px;line-height:9px;white-space:nowrap;overflow:hidden;filter:brightness(1.3)}
.ccrate{position:absolute;left:0;right:0;bottom:0;text-align:center;font-size:6px;line-height:9px;color:#fde68a;white-space:nowrap;overflow:hidden}
.rows{margin-top:10px}
.row{padding:4px 0;border-bottom:1px solid #1e293b}
.rtop{display:flex;justify-content:space-between;gap:8px;font-size:9px;line-height:13px}
.rname{filter:brightness(1.3);white-space:nowrap}
.rdoing{font-size:7px;line-height:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;filter:brightness(1.45)}
@media (max-width: 640px){
  .wide{display:none}
  .tall{display:block}
  .wrap{padding:10px 10px 40px 10px}
  .title{font-size:12px}
  .sign{font-size:9px}
  .mid{font-size:8px;order:3;width:100%}
}
"""

def _arcade_activity(name, summary, bot, ledger, positions, market_open, now):
    """One line saying what the bot is doing, plus its mood (busy/calm/asleep)."""
    longs = sum(1 for p in positions if p["side"] == "LONG")
    shorts = len(positions) - longs
    if not bot.get("loaded"):
        return "BOT FILE NOT LOADED", "asleep", True
    recent = []
    for row in ledger[:40]:
        when = E.parse_iso(row.get("time_utc"))
        if when and (now - when).total_seconds() <= 600:
            recent.append(row)
    if bot.get("error"):
        err_time = E.parse_iso(bot.get("error_time_utc"))
        if err_time and (now - err_time).total_seconds() <= 900:
            return "BOT ERROR. SEE ITS DESK BELOW", "asleep", True
    if recent:
        row = recent[0]
        sym = row.get("symbol") or ""
        value = row.get("value") or 0
        pnl = row.get("realized_pnl")
        result = "" if pnl is None else (f". MADE ${pnl:.2f}" if pnl >= 0 else f". LOST ${-pnl:.2f}")
        if row.get("status") == "REJECTED":
            count = sum(1 for r in recent if r.get("status") == "REJECTED")
            reason = str(row.get("reason") or "")
            why = ("NOT ENOUGH CASH" if "too large" in reason or "not enough" in reason
                   else reason.split(" - ")[0].upper()[:34])
            return f"{count} ORDER{'S' if count != 1 else ''} REJECTED. {why}", "asleep", True
        if row.get("action") == "BUY":
            return f"BOUGHT {sym} ${value:.2f}", "busy", False
        if row.get("action") == "SELL":
            return f"SOLD {sym}{result}", "busy", False
        if row.get("action") == "SHORT":
            return f"SHORTED {sym} ${value:.2f}", "busy", False
        if row.get("action") == "COVER":
            return f"BOUGHT BACK {sym}{result}", "busy", False
        if row.get("action") == "SPLIT":
            return f"{sym} SPLIT. SHARES ADJUSTED", "calm", False
    if not market_open:
        held = f"HOLDING {len(positions)}" if positions else "ALL CASH"
        return f"MARKET CLOSED. {held}", "asleep", False
    if longs and shorts:
        return f"HOLDING {longs} STOCKS, SHORT {shorts}", "calm", False
    if shorts:
        return f"SHORT {shorts} STOCK{'S' if shorts != 1 else ''}. OWES ${summary['short_exposure']:.2f}", "calm", False
    if longs:
        return f"HOLDING {longs} STOCK{'S' if longs != 1 else ''}", "calm", False
    return "ALL CASH. WAITING FOR A CHANCE", "calm", False


def arcade_html(state):
    """The animated race. Wide screens: bots run left to right along girders.
    Phones (under 640px wide): bots climb ladders, with a list of what each
    one is doing underneath. Both use the same data."""
    summary = state["summary"]
    now = E.utc_now()
    market = E.market_status(now)
    ranked = sorted(summary.items(), key=lambda kv: -kv[1]["equity"])
    n = len(ranked)
    equities = [s["equity"] for _, s in ranked] + [E.STARTING_CASH]
    low, high = min(equities), max(equities)
    if high - low < 0.5:                                  # zoom in when they're pennies apart
        mid = (high + low) / 2
        low, high = mid - 0.25, mid + 0.25
    pad = (high - low) * 0.12
    low, high = low - pad, high + pad * 2.2

    def spot(equity):                                     # 0..1 along the track
        return min(1.0, max(0.0, (equity - low) / (high - low)))

    def left(fraction, offset=0):
        return f"calc(200px + (100% - 330px) * {fraction:.4f} + {offset}px)"

    height = 118 + 88 * n + 44                            # the iframe's height (wide layout)
    tall_fixed = 10 + 50 + 26 + 10 + 10 + 31 * n + 40     # everything on a phone except the tower
    tower = max(240, height - tall_fixed)
    climb_room = tower - 14 - 40 - 31                     # finish band, names, bot height

    lanes, cols, rows = [], [], []
    for place, (name, s) in enumerate(ranked, start=1):
        color = "#9ca3af" if name == "Grok" else COLORS.get(name, "#e5e7eb")   # grey needs lifting on black
        label = html.escape(name.upper())
        positions = state.get("positions", {}).get(name, [])
        text, mood, alert = _arcade_activity(name, s, state.get("bots", {}).get(name, {}),
                                             state.get("ledgers", {}).get(name, []), positions,
                                             market["is_open"], now)
        longs = sum(1 for p in positions if p["side"] == "LONG")
        shorts = len(positions) - longs
        crates = (f'<div class="crate">x{longs}</div>' if longs else "") + \
                 (f'<div class="crate iou">IOU x{shorts}</div>' if shorts else "")
        down = " down" if s["return_pct"] < 0 else ""
        sprite = ('<div class="head"><div class="eye" style="left:4px"></div><div class="eye" style="left:14px"></div></div>'
                  '<div class="body"></div><div class="leg" style="left:4px"></div>'
                  '<div class="leg" style="left:18px;animation-delay:.2s"></div>'
                  + ('<div class="zed">Z</div>' if mood == "asleep" else ""))
        doing = f'<div class="doing{" alert" if alert else ""}" style="color:{color}">{html.escape(text)}</div>'
        lanes.append(
            f'<div class="lane"><div class="who" style="color:{color}">{place} {label}'
            f'<div class="money{down}">${s["equity"]:.2f} {s["return_pct"]:+.2f}%</div></div>'
            f'<div class="girder"></div>'
            f'<div class="bot {mood}" style="left:{left(spot(s["equity"]), -15)};color:{color}">{sprite}'
            + (f'<div class="crates" style="left:{46 if mood == "asleep" else 34}px">{crates}</div>' if crates else "")
            + f'</div>{doing}</div>')
        tags = " ".join(t for t in (f"x{longs}" if longs else "", f"IOU{shorts}" if shorts else "") if t)
        cols.append(
            f'<div class="col" style="left:{(place - 1) / n * 100:.3f}%;width:{100 / n:.3f}%">'
            f'<div class="ladder"></div>'
            f'<div class="tbot {mood}" style="bottom:{40 + climb_room * spot(s["equity"]):.0f}px;color:{color}">{sprite}</div>'
            f'<div class="cname" style="color:{color}">{label}</div>'
            f'<div class="ccrate">{tags or "CASH"}</div></div>')
        rows.append(
            f'<div class="row"><div class="rtop"><span class="rname" style="color:{color}">{place} {label}</span>'
            f'<span class="money{down}">${s["equity"]:.2f} {s["return_pct"]:+.2f}%</span></div>'
            f'<div class="rdoing{" alert" if alert else ""}" style="color:{color}">{html.escape(text)}</div></div>')

    fills = []
    for name, ledger in state.get("ledgers", {}).items():
        for row in ledger[:15]:
            if row.get("status") == "FILLED" and row.get("action") in ("BUY", "SELL", "SHORT", "COVER"):
                fills.append((row.get("time_utc", ""), name, row))
    fills.sort(key=lambda f: f[0], reverse=True)
    words = {"BUY": "BOUGHT", "SELL": "SOLD", "SHORT": "SHORTED", "COVER": "BOUGHT BACK"}
    items = []
    for _, name, row in fills[:8]:
        pnl = row.get("realized_pnl")
        tail = f"${row.get('value') or 0:.2f}" if pnl is None else f"{'+' if pnl >= 0 else '-'}${abs(pnl):.2f}"
        items.append(f"{name.upper()} {words[row['action']]} {row.get('symbol', '')} {tail}")
    if n > 1:
        items.append(f"GAP FIRST TO LAST ${ranked[0][1]['equity'] - ranked[-1][1]['equity']:.2f}")
    ticker = html.escape("LATEST: " + "  *  ".join(items)) if items else "NO TRADES YET"

    day = len(state.get("daily_summaries", [])) + (1 if market["is_trading_day"] and
                                                   market["session"] != "after-hours" else 0)
    target = market.get("closes_at_utc") if market["is_open"] else (
        market.get("opens_at_utc") if market["session"] == "pre-market" else "")
    label = "CLOSES IN" if market["is_open"] else "OPENS IN"
    sign = ('<div class="sign open">MARKET OPEN</div>' if market["is_open"]
            else '<div class="sign shut">MARKET CLOSED</div>')
    start_x = left(spot(E.STARTING_CASH))
    start_y = 40 + climb_room * spot(E.STARTING_CASH)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap">
<style>{ARCADE_CSS}</style></head><body><div class="wrap">
<div class="top"><div class="title">BOT TRADER '26</div>
<div class="mid">DAY {max(1, day):02d} <span id="count" data-target="{target or ''}" data-label="{label}"></span></div>{sign}</div>
<div class="wide">
<div class="key">FURTHER RIGHT = MORE MONEY &nbsp;&nbsp; CRATE = STOCKS OWNED &nbsp;&nbsp; IOU = SHORTS</div>
<div class="track"><div class="start" style="left:{start_x}"></div>
<div class="startlab" style="left:{start_x}">${E.STARTING_CASH:.0f} START</div><div class="flag"></div>
{''.join(lanes)}</div></div>
<div class="tall">
<div class="key">HIGHER = MORE MONEY<br>x5 = 5 STOCKS OWNED &nbsp; IOU3 = 3 SHORTS</div>
<div class="tower" style="height:{tower}px"><div class="finish"></div><div class="ground"></div>
<div class="tstart" style="bottom:{start_y:.0f}px"><span class="tstartlab">${E.STARTING_CASH:.0f}</span></div>
{''.join(cols)}</div>
<div class="rows">{''.join(rows)}</div></div>
<div class="tick"><div id="tick"><span>{ticker}</span><span aria-hidden="true">{ticker}</span></div></div>
</div><script>
(function(){{
  var t=document.getElementById('tick'); if(t) t.style.animationDelay='-'+((Date.now()/1000)%40)+'s';
  var c=document.getElementById('count'); var target=Date.parse(c.dataset.target||'');
  function show(){{ if(isNaN(target)) return; var s=Math.max(0,Math.floor((target-Date.now())/1000));
    var p=function(n){{return (n<10?'0':'')+n}};
    c.textContent='\\u00a0 '+c.dataset.label+' '+p(Math.floor(s/3600))+':'+p(Math.floor(s%3600/60))+':'+p(s%60); }}
  show(); setInterval(show,1000);
}})();
</script></body></html>""", height


# -----------------------------------------------------------------------------
# Lloyd's trading desk
# -----------------------------------------------------------------------------
ACTION_WORDS = {"Buy": "BUY", "Sell": "SELL", "Short": "SHORT", "Cover": "COVER"}


def human_dir():
    return os.path.join(E.BOT_DATA_DIR, HUMAN)


def send_to_human(message):
    """Drop one instruction in the human player's inbox. The engine picks it
    up on its next cycle (within about 20 seconds while the market is open)."""
    inbox = os.path.join(human_dir(), "inbox")
    os.makedirs(inbox, exist_ok=True)
    name = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}.json"
    E.atomic_write_json(os.path.join(inbox, name), message)


def tradable_symbols():
    cache = E.read_json(E.UNIVERSE_CACHE) or {}
    return cache.get("symbols") or {}


def _pct(new, old):
    return (new / old - 1) * 100 if new and old else None


def research(view, watch, positions):
    """Charts and figures for one stock, built only from the data the bots
    receive: about six months of daily prices, today's minute-by-minute
    prices, and the market movers lists."""
    st.markdown("**📊 Research a stock**")
    st.caption("This uses exactly the market data the bots get: about six months of daily "
               "prices, today's minute-by-minute prices, and the market movers lists.")
    history = (E.read_json(os.path.join(human_dir(), "daily.json")) or {}).get("daily", {})
    intraday = view.get("intraday", {})
    choices = list(dict.fromkeys(list(watch) + list(positions)))
    if not choices:
        st.caption("Add stocks to your watchlist (further down) to see their charts and figures.")
    else:
        names = {w["symbol"]: w.get("name", "") for w in watch.values()}
        pick = st.selectbox("Stock", choices, key="research_pick",
                            format_func=lambda x: f"{x} · {names.get(x, '')}"[:60])
        span = st.radio("Chart", ["Today", "1 month", "3 months", "6 months"], index=2,
                        horizontal=True, key="research_span")
        rows = history.get(pick, [])
        today = E.market_status(E.utc_now())["new_york_date"]
        done = [r for r in rows if r[0] < today]
        closes = [r[1] for r in done]
        price = (watch.get(pick) or {}).get("price") or (positions.get(pick) or {}).get("price")
        if not price and closes:
            price = closes[-1]
        if not rows and not intraday.get(pick):
            st.info(f"No price history for {pick} yet. It arrives after the next engine cycle.")
        else:
            def back(days):
                return closes[-1 - days] if len(closes) > days else None
            avg50 = sum(closes[-50:]) / len(closes[-50:]) if len(closes) >= 50 else None
            hi = max(closes) if closes else None
            moves = [closes[i] / closes[i - 1] - 1 for i in range(max(1, len(closes) - 20), len(closes))]
            daily_move = (sum(m * m for m in moves) / len(moves)) ** 0.5 * 100 if moves else None
            traded = sorted(r[1] * r[2] for r in done[-20:])
            traded = traded[len(traded) // 2] if traded else None

            def fmt(v, kind="pct"):
                if v is None:
                    return "-"
                return f"{v:+.2f}%" if kind == "pct" else f"${v:,.2f}"
            cells = [("Price", fmt(price, "$")), ("Today", fmt(_pct(price, back(0)))),
                     ("1 week", fmt(_pct(price, back(4)))), ("1 month", fmt(_pct(price, back(20)))),
                     ("3 months", fmt(_pct(price, back(62)))),
                     ("6 months", fmt(_pct(price, closes[0] if closes else None))),
                     ("vs 6-month high", fmt(_pct(price, hi))),
                     ("50-day average", fmt(avg50, "$"))]

            def tint(text):
                if text.startswith("+"):
                    return "color:#16a34a"
                if text.startswith("-") and text != "-":
                    return "color:#dc2626"
                return ""
            grid = "".join(
                f'<div style="padding:6px 8px;border:1px solid rgba(128,128,128,.25);border-radius:8px">'
                f'<div style="font-size:.75rem;opacity:.7">{label}</div>'
                f'<div style="font-size:1.05rem;font-weight:600;{tint(value)}">{value}</div></div>'
                for label, value in cells)
            st.markdown(f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(105px,1fr));'
                        f'gap:6px;margin-bottom:6px">{grid}</div>', unsafe_allow_html=True)
            notes = []
            if avg50:
                side = "above" if price >= avg50 else "below"
                notes.append(f"price is {side} its 50-day average")
            if daily_move:
                notes.append(f"typical daily move ±{daily_move:.1f}%")
            if traded:
                notes.append(f"about ${traded / 1e6:,.0f}m traded a day")
            if notes:
                st.caption(" · ".join(notes))

            if span == "Today":
                bars = intraday.get(pick, [])
                cdf = pd.DataFrame(bars, columns=["t", "Price", "Volume"]) if bars else pd.DataFrame()
                if not cdf.empty:
                    cdf["Time"] = (pd.to_datetime(cdf["t"], utc=True).dt.tz_convert(UK)
                                   .dt.tz_localize(None))
                    line = alt.Chart(cdf).mark_line(color="#f472b6", strokeWidth=2).encode(
                        x=alt.X("Time:T", title="UK time"),
                        y=alt.Y("Price:Q", scale=alt.Scale(zero=False), title="Price ($)"),
                        tooltip=[alt.Tooltip("Time:T", format="%H:%M"),
                                 alt.Tooltip("Price:Q", format="$.2f")])
                    st.altair_chart(line.properties(height=240), width="stretch")
                else:
                    st.caption("No trading yet today.")
            else:
                days = {"1 month": 21, "3 months": 63, "6 months": 200}[span]
                ddf = pd.DataFrame(rows, columns=["Date", "Price", "Volume"])
                ddf["50-day average"] = ddf["Price"].rolling(50).mean()
                ddf = ddf.tail(days)
                ddf["Date"] = pd.to_datetime(ddf["Date"])
                melted = ddf.melt(id_vars="Date", value_vars=["Price", "50-day average"],
                                  var_name="Line", value_name="Value").dropna()
                chart = alt.Chart(melted).mark_line(strokeWidth=2).encode(
                    x=alt.X("Date:T", title=None),
                    y=alt.Y("Value:Q", scale=alt.Scale(zero=False), title="Price ($)"),
                    color=alt.Color("Line:N", scale=alt.Scale(domain=["Price", "50-day average"],
                                                              range=["#f472b6", "#94a3b8"]),
                                    legend=alt.Legend(orient="bottom", title=None)),
                    strokeDash=alt.StrokeDash("Line:N", scale=alt.Scale(
                        domain=["Price", "50-day average"], range=[[1, 0], [5, 4]]), legend=None),
                    tooltip=[alt.Tooltip("Date:T", format="%d %b"), "Line:N",
                             alt.Tooltip("Value:Q", format="$.2f")])
                st.altair_chart(chart.properties(height=240), width="stretch")

    movers = view.get("movers") or {}
    if movers:
        with st.expander("🔥 Today's market movers (the same lists the bots get)"):
            titles = {"day_gainers": "Biggest gainers", "day_losers": "Biggest losers",
                      "most_actives": "Most traded", "small_cap_gainers": "Small-company gainers"}
            pick_list = st.radio("List", [k for k in titles if movers.get(k)], horizontal=True,
                                 format_func=lambda k: titles[k], key="movers_pick")
            items = movers.get(pick_list, [])
            mdf = pd.DataFrame([{"Stock": q.get("symbol"), "Change %": q.get("change_pct"),
                                 "Price": q.get("price"), "Name": q.get("name", ""),
                                 "Tradable": "✅" if q.get("tradable") else "⛔"} for q in items])
            if not mdf.empty:
                st.dataframe(mdf, hide_index=True, width="stretch", height=280,
                             column_config={"Price": st.column_config.NumberColumn(format="$%.2f"),
                                            "Change %": st.column_config.NumberColumn(format="%+.2f%%")})
                ok = [q["symbol"] for q in items if q.get("tradable") and q["symbol"] not in watch]
                if ok:
                    c1, c2 = st.columns([3, 1])
                    add = c1.selectbox("Add one to your watchlist", ok, key="mover_add",
                                       label_visibility="collapsed")
                    if c2.button("Watch", key="mover_watch"):
                        send_to_human({"type": "watch", "symbol": add})
                        st.toast(f"{add} added. Its chart appears after the next engine cycle.")
            st.caption("⛔ = can't be traded here (an ETF or other non-stock, or under $1).")


def stop_text(stop, side):
    """One line describing a position's stop-loss / take-profit."""
    if not stop:
        return ""
    parts = []
    if stop.get("stop_price"):
        parts.append(f"stop-loss at ${stop['stop_price']:.2f}")
    if stop.get("trail_pct"):
        best = stop.get("best")
        if best:
            level = best * (1 - stop["trail_pct"] / 100) if side == "LONG" else best * (1 + stop["trail_pct"] / 100)
            parts.append(f"trailing stop {stop['trail_pct']:g}% (now ${level:.2f})")
        else:
            parts.append(f"trailing stop {stop['trail_pct']:g}%")
    if stop.get("target_price"):
        parts.append(f"take-profit at ${stop['target_price']:.2f}")
    return " · ".join(parts)


def stop_editor(positions, stops):
    with st.expander("🛑 Set or change a stop-loss / take-profit"):
        st.caption("Checked every engine cycle while the market is open (about every 20 seconds). "
                   "When one is crossed, the whole position is closed at that cycle's price. "
                   "If a stock jumps past your level, especially overnight, it closes at the "
                   "next price, which can be worse than your level.")
        sym = st.selectbox("Position", list(positions), key="stop_sym")
        p = positions[sym]
        price = p["price"]
        long = p["side"] == "LONG"
        st.write(f"{sym} is {p['side'].lower()} at {money(price)} now.")
        kind = st.radio("Stop-loss type", ["None", "Fixed price", "Trailing %"], horizontal=True,
                        key="stop_kind")
        stop_price = trail_pct = None
        if kind == "Fixed price":
            default = round(price * (0.95 if long else 1.05), 2)
            stop_price = st.number_input(
                f"Close if the price {'falls to' if long else 'rises to'} ($)", min_value=0.01,
                value=default, step=0.5, format="%.2f", key=f"stop_price_{sym}")
        elif kind == "Trailing %":
            trail_pct = st.number_input("Close if the price moves this % against its best since now",
                                        min_value=0.5, max_value=50.0, value=5.0, step=0.5,
                                        format="%.1f", key=f"trail_{sym}")
        target = st.number_input(f"Take-profit: close if the price {'rises to' if long else 'falls to'} "
                                 "($, 0 = none)", min_value=0.0, value=0.0, step=0.5,
                                 format="%.2f", key=f"target_{sym}")
        c1, c2 = st.columns(2)
        if c1.button("Save", type="primary", key="stop_save", use_container_width=True):
            problem = None
            if stop_price and (stop_price >= price if long else stop_price <= price):
                problem = (f"A stop-loss on a {'long' if long else 'short'} position must be "
                           f"{'below' if long else 'above'} the current price ({money(price)}).")
            elif target and (target <= price if long else target >= price):
                problem = (f"A take-profit must be {'above' if long else 'below'} the current "
                           f"price ({money(price)}).")
            elif not (stop_price or trail_pct or target):
                problem = "Choose a stop-loss or a take-profit (or use Remove)."
            if problem:
                st.error(problem)
            else:
                send_to_human({"type": "stop", "symbol": sym, "stop_price": stop_price,
                               "trail_pct": trail_pct, "target_price": target or None})
                st.success("Saved. It takes effect on the next engine cycle.")
        if stops.get(sym) and c2.button("Remove", key="stop_clear", use_container_width=True):
            send_to_human({"type": "clear_stop", "symbol": sym})
            st.success(f"Stops on {sym} removed (on the next engine cycle).")


def trading_desk():
    st.subheader(f"{ICONS.get(HUMAN, '')} Your trading desk")
    if MODE == "github":
        st.info("Trading works when the engine runs on your own server.")
        return
    if not controls_unlocked(st, key="pw_desk"):
        st.caption("Enter the dashboard password to trade.")
        return
    view = E.read_json(os.path.join(human_dir(), "view.json"))
    if not view:
        st.info(f"Your account appears here once the engine has loaded lloyd_bot.py. That happens "
                "within a minute of uploading it, then takes one more engine cycle.")
        return
    updated = E.parse_iso(view.get("updated_utc"))
    age = (E.utc_now() - updated).total_seconds() if updated else None
    if age is not None and age > 300:
        st.warning(f"Your account was last updated {age / 60:.0f} minutes ago. "
                   "Check the engine is running (sidebar).")

    acct = view.get("account", {})
    positions = acct.get("positions", {})
    market = E.market_status(E.utc_now())
    m1, m2 = st.columns(2)
    m1.metric("Your equity", money(acct.get("equity")),
              f"{(acct.get('equity', E.STARTING_CASH) / E.STARTING_CASH - 1) * 100:+.2f}%")
    m2.metric("Cash", money(acct.get("cash")))
    st.caption(f"You can buy up to **{money(acct.get('max_new_buy'))}** and short up to "
               f"**{money(acct.get('max_new_short'))}** right now. Fee: 0.1% of each order.")
    if market["is_open"]:
        st.success("Market open. Orders go through on the next engine cycle, within about 20 seconds.")
    else:
        st.info("Market closed. Orders you place now wait, and go through at the next open "
                "(2:30pm UK on normal days).")
    if st.button("🔄 Refresh my desk", key="desk_refresh"):
        st.rerun()

    watch = {w["symbol"]: w for w in view.get("watch", [])}
    research(view, watch, positions)

    # ---------------- place an order
    with st.form("order_form", clear_on_submit=True):
        st.markdown("**Place an order**")
        ticker = st.text_input("Stock ticker", placeholder="e.g. AAPL")
        verb = st.radio("Action", list(ACTION_WORDS), horizontal=True)
        amount = st.number_input("Amount in dollars", min_value=1.0, value=10.0, step=1.0,
                                 format="%.2f")
        whole = st.checkbox("Whole position (for Sell or Cover)")
        st.caption("Optional, for Buy or Short: protect the position as soon as it fills. "
                   "Leave at 0 for none.")
        c1, c2 = st.columns(2)
        stop_pct = c1.number_input("Stop-loss %", min_value=0.0, max_value=50.0, value=0.0,
                                   step=0.5, format="%.1f",
                                   help="Closes the position if the price moves this far against "
                                        "you from your fill price.")
        target_pct = c2.number_input("Take-profit %", min_value=0.0, max_value=200.0, value=0.0,
                                     step=0.5, format="%.1f",
                                     help="Closes the position once it has made this much.")
        trail = st.checkbox("Make the stop-loss trailing (it follows the price as it moves your way)")
        placed = st.form_submit_button("Place order", type="primary", use_container_width=True)
    if placed:
        sym = E.normalize_symbol(ticker)
        action = ACTION_WORDS[verb]
        held = positions.get(sym)
        universe = tradable_symbols()
        price = (watch.get(sym) or {}).get("price") or (held or {}).get("price")
        problem = None
        if not E.SYMBOL_RE.match(sym or ""):
            problem = "Type a ticker, for example AAPL or BRK-B."
        elif universe and sym not in universe and not held:
            problem = f"{sym} isn't on the US stock lists (or it's an ETF, warrant or unit, which aren't allowed)."
        elif action == "BUY" and held and held["side"] == "SHORT":
            problem = f"You're short {sym}. Cover it before buying."
        elif action == "SHORT" and held and held["side"] == "LONG":
            problem = f"You own {sym}. Sell it before shorting."
        elif action == "SELL" and not (held and held["side"] == "LONG"):
            problem = f"You don't own {sym}."
        elif action == "COVER" and not (held and held["side"] == "SHORT"):
            problem = f"You're not short {sym}."
        elif action == "BUY" and amount > (acct.get("max_new_buy") or 0) + 1e-9:
            problem = f"Too large. The most you can buy right now is {money(acct.get('max_new_buy'))}."
        elif action == "SHORT" and amount > (acct.get("max_new_short") or 0) + 1e-9:
            problem = f"Too large. The most you can short right now is {money(acct.get('max_new_short'))}."
        elif action == "SHORT" and price and price < E.MIN_SHORT_PRICE:
            problem = f"Stocks under ${E.MIN_SHORT_PRICE:.0f} can't be shorted."
        elif action in ("BUY", "SHORT") and price and price < E.MIN_PRICE:
            problem = f"Stocks under ${E.MIN_PRICE:.0f} can't be traded."
        if problem:
            st.error(problem)
        else:
            amt = "ALL" if (whole and action in ("SELL", "COVER")) else round(float(amount), 2)
            message = {"type": "order", "id": uuid.uuid4().hex[:10], "symbol": sym,
                       "action": action, "amount": amt, "placed_utc": E.iso(E.utc_now())}
            if action in ("BUY", "SHORT"):
                message.update(stop_pct=float(stop_pct), trail=bool(trail),
                               target_pct=float(target_pct))
            send_to_human(message)
            when = ("on the next engine cycle" if market["is_open"] else "at the next open")
            what = "the whole position" if amt == "ALL" else f"${amt:.2f}"
            st.success(f"Order sent: {verb.lower()} {what} of {sym}. It goes through {when}; "
                       "the result appears under Recent results and in your desk below.")

    # ---------------- waiting orders
    pending = view.get("pending", [])
    if pending:
        st.markdown("**Waiting to go through**")
        for o in pending:
            what = "whole position" if o["amount"] == "ALL" else f"${o['amount']:.2f}"
            arm = o.get("arm") or {}
            extra = []
            if arm.get("stop_pct"):
                extra.append(f"{'trailing ' if arm.get('trail') else ''}stop {arm['stop_pct']:g}%")
            if arm.get("target_pct"):
                extra.append(f"take-profit {arm['target_pct']:g}%")
            c1, c2 = st.columns([3, 1])
            c1.write(f"{o['action'].title()} {o['symbol']} · {what}"
                     + (f" · {', '.join(extra)}" if extra else ""))
            if c2.button("Cancel", key=f"cancel_{o['id']}"):
                send_to_human({"type": "cancel", "id": o["id"]})
                st.toast("Cancel sent. It takes effect on the next engine cycle.")

    # ---------------- positions
    st.markdown("**Your positions**")
    stops = view.get("stops", {})
    if positions:
        for sym, p in positions.items():
            c1, c2 = st.columns([3, 1])
            guard = stop_text(stops.get(sym), p["side"])
            if not guard and sym in (view.get("arming") or {}):
                guard = "protection being set up"
            c1.write(f"{sym} · {p['side'].lower()} · {money(abs(p['value']))} · "
                     f"P&L {p['unrealized_pnl']:+.2f}" + (f"  \n🛑 {guard}" if guard else ""))
            if c2.button("Close", key=f"close_{sym}"):
                send_to_human({"type": "order", "id": uuid.uuid4().hex[:10], "symbol": sym,
                               "action": "SELL" if p["side"] == "LONG" else "COVER",
                               "amount": "ALL", "placed_utc": E.iso(E.utc_now())})
                st.toast(f"Closing {sym}: sent.")
        stop_editor(positions, stops)
    else:
        st.caption("None yet.")

    # ---------------- watchlist
    st.markdown(f"**Your watchlist** (prices update every cycle while the market is open)")
    rows = [w for w in view.get("watch", []) if w.get("on_watchlist") or w["symbol"] in positions]
    if rows:
        wdf = pd.DataFrame([{"Stock": w["symbol"], "Price": w.get("price"),
                             "Today %": w.get("change_pct"), "Name": w.get("name", "")}
                            for w in rows])
        st.dataframe(wdf, hide_index=True, width="stretch",
                     column_config={"Price": st.column_config.NumberColumn(format="$%.2f"),
                                    "Today %": st.column_config.NumberColumn(format="%+.2f%%")})
    else:
        st.caption("Empty. Add stocks to see their prices.")
    with st.form("watch_form", clear_on_submit=True):
        new = st.text_input("Add a stock to your watchlist", placeholder="e.g. NVDA")
        if st.form_submit_button("Add", use_container_width=True):
            sym = E.normalize_symbol(new)
            universe = tradable_symbols()
            if not E.SYMBOL_RE.match(sym or ""):
                st.error("Type a ticker, for example NVDA.")
            elif universe and sym not in universe:
                st.error(f"{sym} isn't on the US stock lists.")
            else:
                send_to_human({"type": "watch", "symbol": sym})
                st.success(f"{sym} added. Its price appears after the next engine cycle.")
    mine = [w["symbol"] for w in view.get("watch", []) if w.get("on_watchlist")]
    if mine:
        c1, c2 = st.columns([3, 1])
        drop = c1.selectbox("Remove a stock", mine, label_visibility="collapsed")
        if c2.button("Remove", key="unwatch"):
            send_to_human({"type": "unwatch", "symbol": drop})
            st.toast(f"{drop} removed.")
    if view.get("refused"):
        st.caption("Refused by the engine: " + "; ".join(f"{k} ({v})" for k, v in
                                                       list(view["refused"].items())[:5]))

    # ---------------- recent results
    results = view.get("results", [])
    if results:
        st.markdown("**Recent results**")
        for r in results[:10]:
            when = uk_time(r.get("time_utc"), "%a %H:%M")
            if r.get("status") == "TRIGGERED":
                st.write(f"🛑 {when} · {r.get('symbol')}: {r.get('reason')}. "
                         "Closing the whole position.")
            elif r.get("status") == "FILLED":
                pnl = r.get("realized_pnl")
                extra = "" if pnl is None else f", P&L {pnl:+.2f}"
                st.write(f"✅ {when} · {str(r.get('action', '')).title()} {r.get('symbol')} "
                         f"{money(r.get('value'))} at {money(r.get('price'))}{extra}")
            else:
                st.write(f"❌ {when} · {str(r.get('action', '')).title()} {r.get('symbol')}: "
                         f"{r.get('reason')}")


# -----------------------------------------------------------------------------
# Main page
# -----------------------------------------------------------------------------
def render_arcade():
    state, _ = load_state()
    if not state or not state.get("summary"):
        return
    page, height = arcade_html(state)
    if hasattr(st, "iframe"):                     # Streamlit 1.5x+: the supported way
        st.iframe(page, height=height)
    else:                                         # older Streamlit versions
        components.html(page, height=height, scrolling=False)


def render_details():
    state, _ = load_state()
    if not state or not state.get("summary"):
        return
    eng = state.get("engine", {})
    market = E.market_status(E.utc_now())
    session = market.get("session", "?")
    badge = {"open": "🟢 OPEN", "pre-market": "🌅 PRE-MARKET", "after-hours": "🌙 AFTER HOURS",
             "weekend": "😴 WEEKEND", "holiday": "🎌 HOLIDAY"}.get(session, session)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("US market", badge, f"New York {market.get('new_york_time', '-')[:5]}", delta_color="off")
    c2.metric("UK time", datetime.now(UK).strftime("%a %d %b %H:%M"))
    c3.metric("Stocks being tracked", eng.get("symbols_tracked", 0),
              f"of {eng.get('universe_size', 0):,} tradable", delta_color="off")
    c4.metric("Results as of (UK)", uk_time(eng.get("heartbeat_utc"), "%a %H:%M"))
    if eng.get("data_errors") and not eng.get("throttled"):
        st.warning("Data problems on the last download: " + " | ".join(eng["data_errors"])[:500])
    st.caption(f"Stock list: {eng.get('universe_source', '-')}. Orders only fill while the US "
               "market is open (2:30pm–9pm UK time on normal days).")

    # ---------------- leaderboard
    summary = state["summary"]
    names = [n for n in BOT_NAMES if n in summary] + [n for n in summary if n not in BOT_NAMES]
    rows = []
    for name in names:
        s = summary[name]
        b = state.get("bots", {}).get(name, {})
        health = "⚠️ error" if b.get("error") else ("✅" if b.get("loaded") else "⛔ not loaded")
        rows.append({"Bot": f"{ICONS.get(name, '')} {name}", "Equity": s["equity"],
                     "Return %": s["return_pct"], "Cash": s["cash"],
                     "Long $": s["long_exposure"], "Short $": s["short_exposure"],
                     "Open": s["open_positions"], "Fees": s["fees_paid"],
                     "Realised P&L": s["realized_pnl"], "Trades closed": s["closed_trades"],
                     "Win %": s["win_rate_pct"], "Max DD %": s["max_drawdown_pct"],
                     "Health": health})
    board = pd.DataFrame(rows).sort_values("Equity", ascending=False)
    st.subheader("🏆 Leaderboard")
    st.dataframe(
        board, hide_index=True, width="stretch",
        column_config={
            "Equity": st.column_config.NumberColumn(format="$%.2f"),
            "Return %": st.column_config.NumberColumn(format="%+.2f%%"),
            "Cash": st.column_config.NumberColumn(format="$%.2f"),
            "Long $": st.column_config.NumberColumn(format="$%.2f"),
            "Short $": st.column_config.NumberColumn(format="$%.2f"),
            "Fees": st.column_config.NumberColumn(format="$%.2f"),
            "Realised P&L": st.column_config.NumberColumn(format="$%+.2f"),
            "Win %": st.column_config.NumberColumn(format="%.0f%%"),
            "Max DD %": st.column_config.NumberColumn(format="%.2f%%"),
        })
    if "Claude" in summary and "Fable" in summary:
        opus, fable = summary["Claude"], summary["Fable"]
        gap = fable["equity"] - opus["equity"]
        verdict = "level" if abs(gap) < 0.005 else (f"Fable ahead by ${gap:.2f}" if gap > 0
                                                     else f"Opus ahead by ${-gap:.2f}")
        st.caption(f"**Claude Opus 5.5 vs Claude Fable 5.2:** 🟪 Claude (Opus) "
                   f"{opus['return_pct']:+.2f}% · 🔷 Fable {fable['return_pct']:+.2f}% → {verdict}")

    # ---------------- chart
    history = state.get("history", [])
    if history:
        df = pd.DataFrame(history)
        df["Time"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(UK).dt.tz_localize(None)
        shown = [n for n in names if n in df.columns]
        long_df = df.melt(id_vars="Time", value_vars=shown, var_name="Bot", value_name="Equity ($)")
        chart = alt.Chart(long_df).mark_line(strokeWidth=2.5).encode(
            x=alt.X("Time:T", title="UK time"),
            y=alt.Y("Equity ($):Q", scale=alt.Scale(zero=False)),
            color=alt.Color("Bot:N", scale=alt.Scale(domain=shown,
                                                     range=[COLORS.get(n, "#888888") for n in shown]),
                            legend=alt.Legend(orient="bottom", columns=3, title=None)),
            tooltip=["Time:T", "Bot:N", alt.Tooltip("Equity ($):Q", format="$.2f")],
        ).properties(height=320)
        st.subheader("📈 Equity over time")
        st.altair_chart(chart, width="stretch")

    # ---------------- daily summaries
    days = state.get("daily_summaries", [])
    if days:
        st.subheader("🗓️ Daily summaries")
        for i, day in enumerate(reversed(days[-30:])):
            ranked = sorted(day["bots"].items(), key=lambda kv: -kv[1]["equity"])
            label = datetime.strptime(day["date"], "%Y-%m-%d").strftime("%A %d %B")
            winner = max(day["bots"].items(), key=lambda kv: kv[1]["day_change"])
            title = (f"{label} · best day: {winner[0]} ({winner[1]['day_change']:+.2f}) · "
                     f"leader: {ranked[0][0]} (${ranked[0][1]['equity']:.2f})")
            with st.expander(title, expanded=(i == 0)):
                if day.get("half_day"):
                    st.caption("Half day: the market closed early.")
                for place, (name, b) in enumerate(ranked, start=1):
                    words = {"BUY": "buy", "SELL": "sell", "SHORT": "short", "COVER": "cover",
                             "SPLIT": "split adjustment"}
                    trades = ", ".join(f"{n} {words.get(a, a.lower())}{'s' if n != 1 and a != 'SPLIT' else ''}"
                                       for a, n in b["orders"].items()) or "no trades"
                    parts = [f"**{place}. {ICONS.get(name, '')} {name}: ${b['equity']:.2f}** "
                             f"({b['day_change']:+.2f} today, {b['day_pct']:+.2f}%; "
                             f"{b['total_pct']:+.2f}% overall).",
                             f"Trades: {trades}" + (f" worth ${b['traded_value']:.2f}, fees ${b['fees']:.2f}."
                                                    if b["orders"] else "."),
                             f"Holding {b['open_positions']} positions "
                             f"(${b['long']:.2f} long, ${b['short']:.2f} short, ${b['cash']:.2f} cash)."]
                    if b["orders"] and b["realized"]:
                        parts.append(f"Closed trades made {b['realized']:+.2f}.")
                    if b.get("best"):
                        parts.append(f"Best holding: {b['best']['symbol']} ({b['best']['pnl']:+.2f}).")
                    if b.get("worst"):
                        parts.append(f"Worst: {b['worst']['symbol']} ({b['worst']['pnl']:+.2f}).")
                    flags = []
                    if b["rejected"]:
                        flags.append(f"{b['rejected']} orders rejected")
                    if b["margin_calls"]:
                        flags.append(f"{b['margin_calls']} positions closed by margin call")
                    if b["error"]:
                        flags.append("the bot reported an error")
                    if not b["loaded"]:
                        flags.append("the bot was not loaded")
                    if flags:
                        parts.append("⚠️ " + "; ".join(flags) + ".")
                    st.markdown(" ".join(parts))

    # ---------------- per-bot desks
    st.subheader("🗂️ Bot desks")
    tabs = st.tabs([f"{ICONS.get(n, '')} {n}" for n in names])
    for tab, name in zip(tabs, names):
        with tab:
            b = state.get("bots", {}).get(name, {})
            s = summary.get(name, {})
            if b.get("error"):
                st.error(f"Last error ({uk_time(b.get('error_time_utc'))} UK):\n\n```\n{b['error']}\n```")
            if not b.get("loaded"):
                st.warning(f"{b.get('file', name)} is not loaded.")
            if b.get("status"):
                st.info(f"Bot says: {b['status']}")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Equity", money(s.get("equity")), f"{s.get('return_pct', 0):+.2f}%")
            m2.metric("Cash", money(s.get("cash")))
            m3.metric("Fees paid", money(s.get("fees_paid")))
            m4.metric("Orders filled / rejected", f"{s.get('orders_filled', 0)} / {s.get('orders_rejected', 0)}")
            if b.get("slow_ticks"):
                st.caption(f"⏱️ Took longer than {E.SLOW_BOT_SECONDS:.0f}s on {b['slow_ticks']} cycles "
                           f"(last cycle {b.get('last_tick_seconds', 0):.2f}s).")

            left, right = st.columns(2)
            with left:
                st.markdown("**Open positions**")
                pos = state.get("positions", {}).get(name, [])
                if pos:
                    pdf = pd.DataFrame(pos)[["symbol", "side", "units", "avg_price", "price",
                                             "value", "unrealized_pnl"]]
                    pdf["units"] = pdf["units"].abs()
                    pdf["value"] = pdf["value"].abs()
                    st.dataframe(pdf.rename(columns={
                        "symbol": "Stock", "side": "Side", "units": "Shares", "avg_price": "Avg price",
                        "price": "Price", "value": "Value", "unrealized_pnl": "P&L"}),
                        hide_index=True, width="stretch",
                        column_config={"Avg price": st.column_config.NumberColumn(format="$%.2f"),
                                       "Price": st.column_config.NumberColumn(format="$%.2f"),
                                       "Value": st.column_config.NumberColumn(format="$%.2f"),
                                       "P&L": st.column_config.NumberColumn(format="$%+.2f"),
                                       "Shares": st.column_config.NumberColumn(format="%.4f")})
                else:
                    st.caption("No open positions.")
                wl = b.get("watchlist", [])
                st.markdown(f"**Watchlist** ({len(wl)}/{E.MAX_WATCHLIST})")
                st.caption(", ".join(wl) if wl else "Empty")
                if b.get("rejected_watchlist"):
                    st.caption("Refused: " + "; ".join(f"{k} ({v})" for k, v in
                                                      list(b["rejected_watchlist"].items())[:10]))
            with right:
                st.markdown("**Order history** (newest first)")
                ledger = state.get("ledgers", {}).get(name, [])
                if ledger:
                    ldf = pd.DataFrame(ledger[:300]).reindex(columns=[
                        "time_utc", "status", "action", "symbol", "value", "price", "fee",
                        "realized_pnl", "reason"])
                    ldf["Time (UK)"] = ldf["time_utc"].map(lambda t: uk_time(t, "%d %b %H:%M:%S"))
                    show = ldf[["Time (UK)", "status", "action", "symbol", "value", "price",
                                "fee", "realized_pnl", "reason"]].rename(columns={
                        "status": "Result", "action": "Action", "symbol": "Stock", "value": "Value",
                        "price": "Price", "fee": "Fee", "realized_pnl": "Realised P&L",
                        "reason": "Note"})
                    st.dataframe(show, hide_index=True, width="stretch", height=360,
                                 column_config={"Value": st.column_config.NumberColumn(format="$%.2f"),
                                                "Price": st.column_config.NumberColumn(format="$%.2f"),
                                                "Fee": st.column_config.NumberColumn(format="$%.3f"),
                                                "Realised P&L": st.column_config.NumberColumn(format="$%+.2f")})
                else:
                    st.caption("No orders yet.")

    st.caption(f"Engine cycle #{eng.get('tick', 0)} · last saved {uk_time(eng.get('heartbeat_utc'), '%H:%M:%S')} UK"
               + (f" · reading from github.com/{REPO}" if MODE == "github" else ""))




def main():
    state, problem = load_state()
    st.title("🏛️ AI Trading Tournament")
    if not state or not state.get("summary"):
        if MODE == "github":
            st.info("No results have been published yet. After the first engine run finishes its "
                    "first few minutes, they appear here automatically (GitHub can take up to "
                    "5 minutes to show new data).")
            if problem and "404" not in problem:
                st.caption(f"Could not read the data: {problem}")
        else:
            st.info("Waiting for the engine's first cycle. This can take a minute after it starts "
                    "(it downloads the stock list and price history first).")
        return

    refresh = (60 if MODE == "github" else 15) if (auto and hasattr(st, "fragment")) else None
    if refresh:
        st.fragment(run_every=refresh)(render_arcade)()
    else:
        render_arcade()

    can_trade = bool(HUMAN) and MODE == "local"
    if can_trade:
        c1, c2 = st.columns(2)
        trade = c1.toggle("🕹️ My trading desk", key="show_desk")
        details = c2.toggle("▾ Full details", key="show_details",
                            help="Show the leaderboard, chart, daily summaries and every bot's "
                                 "positions and orders.")
    else:
        trade = False
        details = st.toggle("▾ Full details", key="show_details",
                            help="Show the leaderboard, chart, daily summaries and every bot's "
                                 "positions and orders.")
    eng = state.get("engine", {})
    if eng.get("throttled"):
        st.warning(f"Yahoo is limiting how often prices can be downloaded, so prices currently "
                   f"refresh every {eng.get('price_refresh_seconds', 0):.0f} seconds. "
                   "The engine speeds back up by itself.")
    if trade:
        trading_desk()
    if details:
        if refresh:
            st.fragment(run_every=refresh)(render_details)()
        else:
            render_details()
    elif not trade:
        st.caption(f"Last saved {uk_time(eng.get('heartbeat_utc'), '%H:%M:%S')} UK. "
                   "Switch on Full details for everything else.")


main()