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
          "Grok": "#6b7280", "Fable": "#3b82f6"}
ICONS = {"Gemini": "🤖", "Claude": "🟪", "ChatGPT": "🟠", "Grok": "⚫", "Fable": "🔷"}
SPARE_COLORS = ["#ef4444", "#eab308", "#14b8a6", "#ec4899"]
BOT_NAMES = [b[0] for b in E.BOTS]
for _i, _name in enumerate(n for n in BOT_NAMES if n not in COLORS):
    COLORS[_name] = SPARE_COLORS[_i % len(SPARE_COLORS)]

st.set_page_config(page_title="AI Trading Tournament", page_icon="🏛️",
                   layout="wide", initial_sidebar_state="expanded")


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


def controls_unlocked():
    """Anyone can view the page. If TOURNAMENT_PASSWORD is set on the server,
    the controls (start, pause, reload, reset) need that password."""
    password = os.environ.get("TOURNAMENT_PASSWORD", "")
    if not password or st.session_state.get("unlocked"):
        return True
    entered = st.sidebar.text_input("🔒 Password to unlock the controls", type="password")
    if entered and hmac.compare_digest(entered.encode(), password.encode()):
        st.session_state["unlocked"] = True
        st.rerun()
    if entered:
        st.sidebar.error("Wrong password")
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
# Main page
# -----------------------------------------------------------------------------
def render():
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
    if eng.get("throttled"):
        st.warning(f"Yahoo is limiting how often prices can be downloaded, so prices currently "
                   f"refresh every {eng.get('price_refresh_seconds', 0):.0f} seconds. "
                   "The engine speeds back up by itself.")
    elif eng.get("data_errors"):
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
                                                     range=[COLORS.get(n, "#888888") for n in shown])),
            tooltip=["Time:T", "Bot:N", alt.Tooltip("Equity ($):Q", format="$.2f")],
        ).properties(height=320)
        st.subheader("📈 Equity over time")
        st.altair_chart(chart, width="stretch")

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
                    ldf = pd.DataFrame(ledger[:300])
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


if auto and hasattr(st, "fragment"):
    st.fragment(run_every=60 if MODE == "github" else 15)(render)()
else:
    render()
