from operator import itemgetter
import numpy as np


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

        # Internal state tracking (persists across ticks in memory)
        self.tick_count = 0
        self.entry_tick = {}       # ticker -> tick entered
        self.entry_price = {}      # ticker -> price at entry
        self.peak_price = {}       # ticker -> best price since entry
        self.cooldown_until = {}   # ticker -> earliest tick allowed to re-enter
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

        # B. PRICE SYNCHRONIZATION: Maintain rolling 60-tick price history
        current_prices = data.get("prices", {})
        for ticker, price in current_prices.items():
            if ticker not in self.price_history:
                self.price_history[ticker] = []
            self.price_history[ticker].append(float(price))
            if len(self.price_history[ticker]) > 60:
                self.price_history[ticker].pop(0)

        # Clean up tracking dicts for positions no longer held
        for tracked in list(self.entry_tick.keys()):
            if tracked not in current_positions:
                self.entry_tick.pop(tracked, None)
                self.entry_price.pop(tracked, None)
                self.peak_price.pop(tracked, None)

        if not data.get("market", {}).get("is_open"):
            return []

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
            if ticker not in self.entry_tick:
                self.entry_tick[ticker] = self.tick_count
            if ticker not in self.entry_price:
                self.entry_price[ticker] = float(pos.get("avg_price", pos.get("entry_price", current_price)))
            if ticker not in self.peak_price:
                self.peak_price[ticker] = current_price

            # Update best favorable price seen since entry
            if side == "LONG":
                self.peak_price[ticker] = max(self.peak_price[ticker], current_price)
            elif side == "SHORT":
                self.peak_price[ticker] = min(self.peak_price[ticker], current_price)

            ticks_held = self.tick_count - self.entry_tick[ticker]
            history = self.price_history.get(ticker, [])
            entry_px = self.entry_price[ticker]

            # BUG FIX: Never exit within the first 10 ticks of holding
            if ticks_held < 10 or len(history) < self.window_size:
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
                self.cooldown_until[ticker] = self.tick_count + 25
                self.entry_tick.pop(ticker, None)
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
            if self.tick_count < self.cooldown_until.get(ticker, 0):
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
                    self.entry_tick[ticker] = self.tick_count
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
                    self.entry_tick[ticker] = self.tick_count
                    self.entry_price[ticker] = float(current_prices[ticker])
                    self.peak_price[ticker] = float(current_prices[ticker])

        return orders
