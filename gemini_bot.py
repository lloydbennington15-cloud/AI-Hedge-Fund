from datetime import datetime, timezone
from operator import itemgetter
import time

import numpy as np


def _to_epoch(text):
    """ISO 8601 time string -> seconds since 1970 (None if it can't be read)."""
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.timestamp()
    except (TypeError, ValueError):
        return None


class GeminiBot:
    def __init__(self):
        # Core parameters tuned for 1-minute US market ticks & 0.1% commission
        self.watchlist = [
            "NVDA", "TSLA", "AAPL", "MSFT", "AMZN",
            "META", "GOOGL", "AMD", "NFLX", "PLTR"
        ]
        self.price_history = {}
        self.window_size = 20
        self.num_std = 2.1
        self.max_positions = 5

        # Time rules, in MINUTES of real time (fix: these used to be counted in
        # engine cycles, which are 20 seconds long, so they ran 3x too fast)
        self.min_hold_minutes = 10
        self.cooldown_minutes = 25

        # Internal state tracking (persists across ticks in memory)
        self.tick_count = 0
        self.entry_time = {}       # ticker -> time entered (seconds since 1970)
        self.entry_price = {}      # ticker -> price at entry
        self.peak_price = {}       # ticker -> best price since entry
        self.cooldown_until = {}   # ticker -> earliest time allowed to re-enter (seconds since 1970)
        self.status = "🤖 Gemini v2.0: Trend-Confirmed Breakout & Trailing Lock Active."

    def _compute_ema(self, values, span):
        alpha = 2.0 / (span + 1.0)
        it = iter(values)
        ema = next(it)
        for val in it:
            ema = alpha * val + (1.0 - alpha) * ema
        return ema

    def market_tick(self, data):
        """Processes market ticks with strict 5-slot sizing, anti-churn cooldowns, and trailing stops."""
        orders = []
        self.tick_count += 1
        now = _to_epoch(data.get("timestamp_utc"))
        if now is None:
            now = time.time()

        account = data.get("account", {})
        current_positions = account.get("positions", {})
        held_tickers = list(current_positions.keys())

        # A. DISCOVERY: Harvest active movers while ALWAYS preserving held tickers on watchlist
        movers_list = []
        movers_data = data.get("movers", {})
        for category in ["day_gainers", "day_losers", "most_actives", "small_cap_gainers"]:
            for item in movers_data.get(category, []):
                if item.get("tradable") and item.get("symbol"):
                    sym = item["symbol"]
                    if sym not in movers_list:
                        movers_list.append(sym)

        combined_watchlist = []
        for sym in held_tickers + movers_list + self.watchlist:
            if sym not in combined_watchlist:
                combined_watchlist.append(sym)
            if len(combined_watchlist) >= 30:
                break
        if combined_watchlist:
            self.watchlist = combined_watchlist

        current_prices = data.get("prices", {})

        # Clean up tracking dicts for positions no longer held
        for tracked in list(self.entry_time.keys()):
            if tracked not in current_positions:
                self.entry_time.pop(tracked, None)
                self.entry_price.pop(tracked, None)
                self.peak_price.pop(tracked, None)

        market = data.get("market", {})
        if not market.get("is_open"):
            return []

        # B. PRICE SYNCHRONIZATION: rolling history of the last 60 one-minute closes.
        # FIX: built from today's 1-minute candles, so each entry really is one
        # minute apart (window_size = 20 -> 20 minutes). It used to append the
        # price every 20-second cycle (and every 60 seconds overnight), so the
        # "20-minute" window only covered ~6.7 minutes. The newest candle's close
        # is the current price, exactly as before.
        session_open = _to_epoch(market.get("opens_at_utc"))
        bars_by_ticker = data.get("intraday_bars", {})
        self.price_history = {}
        for ticker in current_prices:
            closes = []
            for bar in reversed(bars_by_ticker.get(ticker, ())):
                if len(closes) >= 60:
                    break
                bar_time = _to_epoch(bar[0])
                if session_open is not None and bar_time is not None and bar_time < session_open:
                    break                       # only today's regular session
                if bar[4] is not None:
                    closes.append(float(bar[4]))
            closes.reverse()
            self.price_history[ticker] = closes

        # C. PORTFOLIO & RISK PARAMETERS
        equity = float(account.get("equity", 100.0))
        max_buy = float(account.get("max_new_buy", 0.0))
        max_short = float(account.get("max_new_short", 0.0))

        # Target 19.2% equity per slot across max 5 slots (~96% max deployed, zero margin rejections)
        target_alloc = round(equity * 0.192, 2)
        min_entry_alloc = round(equity * 0.12, 2)
        active_slot_count = len(current_positions)
        # D. STEP 1: MANAGE EXITS
        for ticker in held_tickers:
            if len(orders) >= 20:
                break
            if ticker not in current_prices:
                continue

            pos = current_positions[ticker]
            side = pos.get("side")
            current_price = float(current_prices[ticker])

            # Initialize tracking if not yet recorded
            if ticker not in self.entry_time:
                self.entry_time[ticker] = now
            if ticker not in self.entry_price:
                self.entry_price[ticker] = float(pos.get("avg_price", pos.get("entry_price", current_price)))
            if ticker not in self.peak_price:
                self.peak_price[ticker] = current_price

            # Update best favorable price seen since entry
            if side == "LONG":
                self.peak_price[ticker] = max(self.peak_price[ticker], current_price)
            elif side == "SHORT":
                self.peak_price[ticker] = min(self.peak_price[ticker], current_price)

            minutes_held = (now - self.entry_time[ticker]) / 60.0
            history = self.price_history.get(ticker, [])
            entry_px = self.entry_price[ticker]

            # Never exit within the first 10 minutes of holding (FIX: real minutes, not 20-second cycles)
            if minutes_held < self.min_hold_minutes or len(history) < self.window_size:
                continue

            window = history[-self.window_size:]
            mean = float(np.mean(window))
            std = float(np.std(window))
            upper_band = mean + (self.num_std * std)
            lower_band = mean - (self.num_std * std)

            should_exit = False

            if side == "LONG":
                peak = self.peak_price[ticker]
                trailing_floor = peak - max(1.5 * std, peak * 0.008)
                hard_stop_hit = current_price < lower_band
                midband_fail = current_price < mean
                # BUG FIX: Trailing stop only arms after price rises >= 1.5% above entry_px
                trailing_hit = (peak >= entry_px * 1.015) and (current_price < trailing_floor)
                if hard_stop_hit or midband_fail or trailing_hit:
                    should_exit = True

            elif side == "SHORT":
                trough = self.peak_price[ticker]
                trailing_ceiling = trough + max(1.5 * std, trough * 0.008)
                hard_stop_hit = current_price > upper_band
                midband_fail = current_price > mean
                # BUG FIX: Trailing stop only arms after price drops >= 1.5% below entry_px
                trailing_hit = (trough <= entry_px * 0.985) and (current_price > trailing_ceiling)
                if hard_stop_hit or midband_fail or trailing_hit:
                    should_exit = True

            if should_exit:
                action = "SELL" if side == "LONG" else "COVER"
                orders.append({"symbol": ticker, "action": action, "amount": "ALL"})
                self.cooldown_until[ticker] = now + self.cooldown_minutes * 60  # FIX: 25 real minutes
                self.entry_time.pop(ticker, None)
                self.entry_price.pop(ticker, None)
                self.peak_price.pop(ticker, None)
                active_slot_count = max(0, active_slot_count - 1)

        # E. STEP 2: RANK & ENTER HIGH-CONVICTION BREAKOUTS (UP TO 5 SLOTS TOTAL)
        if active_slot_count >= self.max_positions:
            return orders

        candidates = []
        for ticker in self.watchlist:
            if ticker in current_positions:
                continue
            if now < self.cooldown_until.get(ticker, 0):
                continue
            if ticker not in current_prices:
                continue

            history = self.price_history.get(ticker, [])
            if len(history) < self.window_size:
                continue

            current_price = float(current_prices[ticker])
            window = history[-self.window_size:]
            mean = float(np.mean(window))
            std = float(np.std(window))

            if mean <= 0.0:
                continue

            # Filter out dead sideways chop (band width must be >= 0.35% of price)
            band_width_pct = (2.0 * self.num_std * std) / mean
            if band_width_pct < 0.0035:
                continue

            upper_band = mean + (self.num_std * std)
            lower_band = mean - (self.num_std * std)

            # Trend confirmation via fast vs slow EMA
            ema_fast = self._compute_ema(window[-8:], 5)
            ema_slow = self._compute_ema(window, 15)

            # Z-score measures breakout strength for candidate ranking
            z_score = (current_price - mean) / max(std, 1e-6)

            if current_price > upper_band and ema_fast > ema_slow and current_price >= 1.0:
                candidates.append((abs(z_score), "BUY", ticker, current_price))
            elif current_price < lower_band and ema_fast < ema_slow and current_price >= 5.0:
                candidates.append((abs(z_score), "SHORT", ticker, current_price))

        # Prioritize strongest statistical breakouts first
        candidates.sort(key=itemgetter(0), reverse=True)

        for _, action, ticker, _ in candidates:
            if len(orders) >= 20 or active_slot_count >= self.max_positions:
                break

            if action == "BUY":
                if max_buy < min_entry_alloc:
                    continue
                trade_amt = round(min(target_alloc, max_buy * 0.98), 2)
                if trade_amt >= min_entry_alloc:
                    orders.append({"symbol": ticker, "action": "BUY", "amount": trade_amt})
                    max_buy -= trade_amt * 1.002
                    max_short -= trade_amt
                    active_slot_count += 1
                    self.entry_time[ticker] = now
                    self.entry_price[ticker] = float(current_prices[ticker])
                    self.peak_price[ticker] = float(current_prices[ticker])

            elif action == "SHORT":
                if max_short < min_entry_alloc:
                    continue
                trade_amt = round(min(target_alloc, max_short * 0.98), 2)
                if trade_amt >= min_entry_alloc:
                    orders.append({"symbol": ticker, "action": "SHORT", "amount": trade_amt})
                    max_short -= trade_amt * 1.002
                    max_buy -= trade_amt
                    active_slot_count += 1
                    self.entry_time[ticker] = now
                    self.entry_price[ticker] = float(current_prices[ticker])
                    self.peak_price[ticker] = float(current_prices[ticker])

        return orders