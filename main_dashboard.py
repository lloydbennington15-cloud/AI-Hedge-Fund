import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import requests
import time
from datetime import datetime, timezone

# 1. Verification of Safe Bot Compilation Imports
try:
    from gemini_bot import GeminiOfficialBot
    from claude_bot import ClaudeBot
    from chatgpt_bot import ChatGPTBot
    from grok_bot import GrokBot
except ImportError as e:
    st.error(f"⚠️ Missing a bot file in your folder! Error details: {e}")
    st.stop()

st.set_page_config(page_title="AI Infinite Universe Fund", layout="wide", initial_sidebar_state="expanded")

COMMISSION_FEE = 0.001  # 0.1% uniform real-world transaction execution penalty

st.sidebar.title("🔐 Alpaca Server Credentials")
ALPACA_KEY = st.sidebar.text_input("Alpaca API Key ID", type="password", value="PASTE_YOUR_KEY_HERE")
ALPACA_SECRET = st.sidebar.text_input("Alpaca Secret Key", type="password", value="PASTE_YOUR_SECRET_HERE")

st.sidebar.title("🎮 Server Workspace Controls")
live_mode = st.sidebar.toggle("🚀 Activate 24/7 Live Portfolio Feed", value=False)
refresh_speed = st.sidebar.slider("Polling Frequency (Seconds)", 5, 30, 10)

# Initialize single global ledger accounting matrix for UNLIMITED assets
if "history_logs" not in st.session_state:
    st.session_state.history_logs = []
    st.session_state.tick_counter = 0
    st.session_state.ledgers = {ai: [] for ai in ["Gemini", "Claude", "ChatGPT", "Grok"]}
    st.session_state.tracked_prices = {}
    
    # UNRESTRICTED PORTFOLIO LEDGER (The Engine Handles the Books!)
    st.session_state.accounts = {
        ai: {
            "cash": 100.0,
            "positions": {},  # Dynamically fills with ANY stock ticker the bots choose
            "avg_costs": {},
            "trades": [],
            "peak_equity": 100.0,
            "max_dd": 0.0
        } for ai in ["Gemini", "Claude", "ChatGPT", "Grok"]
    }
    
    st.session_state.bot_gemini = GeminiOfficialBot()
    st.session_state.bot_claude = ClaudeBot()
    st.session_state.bot_chatgpt = ChatGPTBot()
    st.session_state.bot_grok = GrokBot()

if st.sidebar.button("🔄 Reset Server Portfolios"):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

# ------------------------------------------------------------------
# 📡 THE LIVE API EXECUTION LOOP (INFINITE MARKET UNIVERSES)
# ------------------------------------------------------------------
if live_mode:
    if "PASTE_YOUR_KEY" in ALPACA_KEY or "PASTE_YOUR_SECRET" in ALPACA_SECRET:
        st.warning("⚠️ Please insert your free Alpaca Server Keys to connect!")
        st.stop()
        
    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    st.session_state.tick_counter += 1
    timestamp_utc = datetime.now(timezone.utc).strftime("%H:%M:%S")

    # 1. Ask the bots what stocks they want to scan or buy from the whole market right now
    gemini_watchlist = getattr(st.session_state.bot_gemini, 'watchlist', ['NVDA', 'AAPL'])
    claude_watchlist = getattr(st.session_state.bot_claude, 'watchlist', ['TSLA', 'MSFT'])
    chatgpt_watchlist = getattr(st.session_state.bot_chatgpt, 'watchlist', ['AMD', 'AMZN'])
    grok_watchlist = getattr(st.session_state.bot_grok, 'watchlist', ['NFLX', 'META'])
    
    # Bundle every requested asset from the entire stock market into a clean unique list
    requested_universe = list(set(gemini_watchlist + claude_watchlist + chatgpt_watchlist + grok_watchlist))

    # 2. Batch query Alpaca's high-speed data feed for the requested universe
    for ticker in requested_universe:
        try:
            symbol_formatted = ticker.replace("-", ".")
            data_url = f"https://alpaca.markets{symbol_formatted}/trades/latest"
            response = requests.get(data_url, headers=headers, timeout=5)
            if response.status_code == 200:
                st.session_state.tracked_prices[ticker] = float(response.json()['trade']['p'])
        except Exception:
            pass

    # 3. FIXED ROUTER SIGNAL: Run if we received any price points from the pool
    if len(st.session_state.tracked_prices) > 0:
        for ai_name, bot in [("Gemini", st.session_state.bot_gemini), ("Claude", st.session_state.bot_claude), 
                             ("ChatGPT", st.session_state.bot_chatgpt), ("Grok", st.session_state.bot_grok)]:
            
            signals = bot.market_tick(st.session_state.tracked_prices, timestamp_utc)
            acc = st.session_state.accounts[ai_name]
            
            for ticker, action in signals.items():
                if ticker not in st.session_state.tracked_prices:
                    continue
                price = st.session_state.tracked_prices[ticker]
                
                if ticker not in acc["positions"]: acc["positions"][ticker] = 0.0
                if ticker not in acc["avg_costs"]: acc["avg_costs"][ticker] = 0.0

                # Execute Long Buy Orders
                if action == "BUY" and acc["cash"] >= 20.0 and acc["positions"][ticker] <= 0:
                    if acc["positions"][ticker] < 0:
                        short_units = abs(acc["positions"][ticker])
                        acc["cash"] -= (short_units * price) * (1 + COMMISSION_FEE)
                        acc["positions"][ticker] = 0.0
                    
                    alloc = 20.0
                    fee = alloc * COMMISSION_FEE
                    units = (alloc - fee) / price
                    acc["cash"] -= alloc
                    acc["positions"][ticker] += units
                    acc["avg_costs"][ticker] = price
                    st.session_state.ledgers[ai_name].insert(0, {"Time": timestamp_utc, "Action": "🟩 BUY", "Asset": ticker, "Units": f"{units:.4f}", "Price": f"${price:.2f}", "Fee Paid": f"${fee:.3f}"})
                    acc["trades"].append({"type": "LONG", "pnl": -fee})
                
                # Execute Liquidations
                elif action == "SELL" and acc["positions"][ticker] > 0:
                    units = acc["positions"][ticker]
                    gross = units * price
                    fee = gross * COMMISSION_FEE
                    net_proceeds = gross - fee
                    pnl = net_proceeds - (units * acc["avg_costs"][ticker])
                    acc["cash"] += net_proceeds
                    acc["positions"][ticker] = 0.0
                    acc["avg_costs"][ticker] = 0.0
                    st.session_state.ledgers[ai_name].insert(0, {"Time": timestamp_utc, "Action": "🟥 SELL", "Asset": ticker, "Units": "All Units", "Price": f"${price:.2f}", "Fee Paid": f"${fee:.3f}"})
                    acc["trades"].append({"type": "LIQ", "pnl": pnl})

                # Execute Short Orders
                elif action == "SHORT" and acc["cash"] >= 20.0 and acc["positions"][ticker] >= 0:
                    if acc["positions"][ticker] > 0:
                        long_units = acc["positions"][ticker]
                        acc["cash"] += (long_units * price) * (1 - COMMISSION_FEE)
                        acc["positions"][ticker] = 0.0
                        
                    alloc = 20.0
                    fee = alloc * COMMISSION_FEE
                    units = (alloc - fee) / price
                    acc["cash"] += (alloc - fee)
                    acc["positions"][ticker] -= units
                    acc["avg_costs"][ticker] = price
                    st.session_state.ledgers[ai_name].insert(0, {"Time": timestamp_utc, "Action": "🟨 SHORT", "Asset": ticker, "Units": f"{units:.4f}", "Price": f"${price:.2f}", "Fee Paid": f"${fee:.3f}"})
                    acc["trades"].append({"type": "SHORT", "pnl": -fee})

        # Calculate real overall mark-to-market equity values
        for model in ["Gemini", "Claude", "ChatGPT", "Grok"]:
            acc = st.session_state.accounts[model]
            total_val = acc["cash"]
            for ticker, units in acc["positions"].items():
                if units != 0:
                    px = st.session_state.tracked_prices.get(ticker, acc["avg_costs"][ticker])
                    total_val += units * px
            
            if total_val > acc["peak_equity"]: acc["peak_equity"] = total_val
            dd = (acc["peak_equity"] - total_val) / acc["peak_equity"] if acc["peak_equity"] > 0 else 0.0
            if dd > acc["max_dd"]: acc["max_dd"] = dd
            
            st.session_state.history_logs.append({
                'Time': st.session_state.tick_counter, 'AI Model': model, 'Balance ($)': round(float(total_val), 2)
            })
st.title("🏛️ AI Unrestricted 24/7 Server Portfolio Terminal")
st.markdown("### 📊 Active Market Scanning Pool (Updated Real-Time via Server Data Logs)")

# Render active market quotes dynamically based on what the AIs are tracking
if st.session_state.tracked_prices:
    active_tickers = list(st.session_state.tracked_prices.keys())[:10]  # Cap metric display layout to top 10 for clean UI
    m_cols = st.columns(len(active_tickers))
    for i, t in enumerate(active_tickers):
        m_cols[i].metric(t, f"${st.session_state.tracked_prices[t]:.2f}")
else:
    st.caption("Waiting for live server connection to stream market assets...")

st.markdown("---")

# Calculate total collective values for metrics banners
valuations = {}
for model in ["Gemini", "Claude", "ChatGPT", "Grok"]:
    acc = st.session_state.accounts[model]
    v = acc["cash"]
    for ticker, units in acc["positions"].items():
        px = st.session_state.tracked_prices.get(ticker, acc["avg_costs"].get(ticker, 0.0))
        v += units * px
    valuations[model] = v

col1, col2, col3, col4 = st.columns(4)
col1.metric("🤖 Gemini Portfolio", f"${valuations['Gemini']:.2f}", f"{((valuations['Gemini']-100)/100)*100:.2f}%")
col2.metric("🟪 Claude Portfolio", f"${valuations['Claude']:.2f}", f"{((valuations['Claude']-100)/100)*100:.2f}%")
col3.metric("🟠 ChatGPT Portfolio", f"${valuations['ChatGPT']:.2f}", f"{((valuations['ChatGPT']-100)/100)*100:.2f}%")
col4.metric("⚫ Grok Portfolio", f"${valuations['Grok']:.2f}", f"{((valuations['Grok']-100)/100)*100:.2f}%")

st.markdown("---")

if st.session_state.history_logs:
    chart_data = pd.DataFrame(st.session_state.history_logs)
    multiline_chart = alt.Chart(chart_data).mark_line(strokeWidth=3).encode(
        x='Time:Q', y=alt.Y('Balance ($):Q', scale=alt.Scale(zero=False)),
        color=alt.Color('AI Model:N', scale=alt.Scale(
            domain=['Gemini', 'Claude', 'ChatGPT', 'Grok'], range=['#10b981', '#a855f7', '#f97316', '#6b7280']
        ))
    ).properties(height=300)
    st.altair_chart(multiline_chart, width="stretch")

st.markdown("---")

st.markdown("### 📝 Competitor Strategy Breakdown Desks")
tabs = st.tabs(["🤖 Gemini Desk", "🟪 Claude Desk", "🟠 ChatGPT Desk", "⚫ Grok Desk"])
ai_keys = {"🤖 Gemini Desk": "Gemini", "🟪 Claude Desk": "Claude", "🟠 ChatGPT Desk": "ChatGPT", "⚫ Grok Desk": "Grok"}

for tab_name, ai_key in ai_keys.items():
    with tabs[list(ai_keys.keys()).index(tab_name)]:
        stat_col, inv_col, led_col = st.columns(3)
        acc = st.session_state.accounts[ai_key]
        
        with stat_col:
            st.markdown("##### 📊 Performance Audit")
            closed_trades = acc["trades"]
            profits = [t["pnl"] for t in closed_trades if t["pnl"] > 0]
            losses = [abs(t["pnl"]) for t in closed_trades if t["pnl"] < 0]
            win_rate = (len(profits) / len(closed_trades) * 100) if closed_trades else 0.0
            profit_factor = sum(profits) / sum(losses) if sum(losses) > 0 else (sum(profits) if sum(profits) > 0 else 1.0)
            
            st.write(f"**Net Equity:** ${valuations[ai_key]:.2f}")
            st.write(f"**Win Rate:** {win_rate:.1f}%")
            st.write(f"**Profit Factor:** {profit_factor:.2f}x")
            st.write(f"**Max Drawdown:** {acc['max_dd']*100:.2f}%")
            st.write(f"**Total Orders Issued:** {len(st.session_state.ledgers[ai_key])}")
            
        with inv_col:
            st.markdown("##### 📦 Open Position Holdings")
            inv_data = []
            for ticker, units in acc["positions"].items():
                if units != 0:
                    avg_buy = acc["avg_costs"][ticker]
                    current_px = st.session_state.tracked_prices.get(ticker, avg_buy)
                    total_value = units * current_px
                    pnl = (current_px - avg_buy) * units if units > 0 else (avg_buy - current_px) * abs(units)
                    inv_data.append({
                        "Asset": ticker, "Type": "LONG" if units > 0 else "SHORT", "Shares": f"{abs(units):.4f}",
                        "Avg Cost": f"${avg_buy:.2f}", "Price": f"${current_px:.2f}", "Value": f"${abs(total_value):.2f}", "P&L": f"${pnl:+.2f}"
                    })
            if inv_data: st.dataframe(pd.DataFrame(inv_data), width="stretch", hide_index=True)
            else: st.caption("No open inventory positions. Wallet flat in cash.")
            
        with led_col:
            st.markdown("##### 📝 Independent Transaction Logs")
            history = st.session_state.ledgers[ai_key]
            if history: st.dataframe(pd.DataFrame(history), width="stretch", hide_index=True, height=200)
            else: st.caption("Waiting for data pings...")

if live_mode:
    time.sleep(refresh_speed)
    st.rerun()
