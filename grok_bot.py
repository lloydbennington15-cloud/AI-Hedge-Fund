class GrokBot:
    def __init__(self):
        self.watchlist = [
            "NVDA", "TSLA", "AMD", "META", "AMZN", "GOOGL", "MSFT", "AAPL",
            "PLTR", "SMCI", "ARM", "AVGO", "COIN", "MSTR", "SNOW", "CRWD",
            "NET", "DDOG", "PANW", "UBER", "SHOP", "HOOD", "SOFI", "IONQ",
            "RKLB", "ASTS", "OKLO", "PATH", "U", "APP"
        ]
        self.status = "GrokBot v4 - selective"
        self.price_history = {}
        self.signal_state = {}
        self.entry_time = {}
        self.max_history = 80
        self.fast_period = 10
        self.slow_period = 24
        self.vol_lookback = 12
        self.min_history = 28
        self.max_positions = 3
        self.risk_frac = 0.22
        self.min_hold_ticks = 8

    def _ema(self, data, period):
        if len(data) < period:
            return None
        k = 2.0 / (period + 1.0)
        ema = float(data[0])
        for p in data[1:]:
            ema = float(p) * k + ema * (1.0 - k)
        return ema

    def _avg_range(self, data):
        if len(data) < self.vol_lookback + 1:
            return None
        ranges = [abs(data[i] - data[i-1]) for i in range(-self.vol_lookback, 0)]
        return sum(ranges) / float(len(ranges))

    def _get_closes(self, data, symbol):
        bars = data.get("intraday_bars", {}).get(symbol)
        if bars and len(bars) >= 8:
            closes = [float(b[4]) for b in bars if b[4] is not None]
            if len(closes) >= 8:
                return closes[-self.max_history:]
        price = data.get("prices", {}).get(symbol)
        if price is None:
            return []
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        hist = self.price_history[symbol]
        hist.append(float(price))
        if len(hist) > self.max_history:
            hist.pop(0)
        return list(hist)

    def market_tick(self, data):
        orders = []
        if not isinstance(data, dict):
            return orders

        market = data.get("market", {})
        is_open = bool(market.get("is_open", False))
        account = data.get("account", {})
        prices = data.get("prices", {})
        positions = account.get("positions", {})
        equity = float(account.get("equity", 100.0) or 100.0)
        max_buy = float(account.get("max_new_buy", 0.0) or 0.0)
        max_short = float(account.get("max_new_short", 0.0) or 0.0)
        tick = int(data.get("tick", 0))

        open_count = len(positions)
        self.status = f"v4 eq=${equity:.2f} pos={open_count}"

        if not is_open:
            return orders

        # --- Exit logic first ---
        for symbol, pos in list(positions.items()):
            price = prices.get(symbol)
            if price is None or price <= 0:
                continue
            price = float(price)
            side = pos.get("side")
            units = float(pos.get("units", 0))
            value = abs(float(pos.get("value", 0)))
            unreal = float(pos.get("unrealized_pnl", 0))
            held_ticks = tick - self.entry_time.get(symbol, tick)

            closes = self._get_closes(data, symbol)
            if len(closes) < self.min_history:
                continue

            fast = self._ema(closes, self.fast_period)
            slow = self._ema(closes, self.slow_period)
            if fast is None or slow is None:
                continue

            # Profit take or momentum fade
            take_profit = unreal > 0.018 * value
            fade = abs(fast - slow) / price < 0.0009

            if side == "LONG":
                if (fast < slow * 0.9975) or (take_profit and fade) or (unreal < -0.028 * value):
                    if held_ticks >= self.min_hold_ticks or unreal < -0.028 * value:
                        orders.append({"symbol": symbol, "action": "SELL", "amount": "ALL"})
                        self.signal_state[symbol] = "FLAT"
                        self.entry_time.pop(symbol, None)

            elif side == "SHORT":
                if (fast > slow * 1.0025) or (take_profit and fade) or (unreal < -0.028 * value):
                    if held_ticks >= self.min_hold_ticks or unreal < -0.028 * value:
                        orders.append({"symbol": symbol, "action": "COVER", "amount": "ALL"})
                        self.signal_state[symbol] = "FLAT"
                        self.entry_time.pop(symbol, None)

        if len(orders) >= 12:
            return orders[:20]

        # --- Entry logic ---
        if open_count >= self.max_positions:
            return orders[:20]

        candidates = []
        for symbol in list(prices.keys()):
            if symbol in positions:
                continue
            price = float(prices[symbol])
            if price < 5.0:
                continue

            closes = self._get_closes(data, symbol)
            if len(closes) < self.min_history:
                continue

            fast = self._ema(closes, self.fast_period)
            slow = self._ema(closes, self.slow_period)
            vol = self._avg_range(closes)
            if fast is None or slow is None or vol is None or vol <= 0:
                continue

            # Require clear trend + decent activity
            sep = abs(fast - slow) / price
            recent = abs(closes[-1] - closes[-5]) if len(closes) >= 5 else 0
            if sep < 0.0022 or recent < vol * 0.55:
                continue

            strength = sep * 1000 + (recent / vol)
            if fast > slow * 1.0028:
                candidates.append((strength, symbol, "BUY", price))
            elif fast < slow * 0.9972:
                candidates.append((strength, symbol, "SHORT", price))

        # Take only the strongest signals
        candidates.sort(reverse=True)
        for strength, symbol, action, price in candidates[:4]:
            if open_count >= self.max_positions:
                break
            target = min(equity * self.risk_frac, max_buy if action == "BUY" else max_short)
            if target < 6.0:
                continue
            amt = round(min(target * 0.92, (max_buy if action == "BUY" else max_short) * 0.90), 2)
            if amt < 6.0:
                continue
            orders.append({"symbol": symbol, "action": action, "amount": amt})
            self.signal_state[symbol] = action
            self.entry_time[symbol] = tick
            open_count += 1

        return orders[:20]