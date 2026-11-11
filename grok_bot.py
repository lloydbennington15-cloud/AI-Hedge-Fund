class GrokBot:
    def __init__(self):
        # 1. Broad Real-World Multi-Asset Server Watchlist Pool
        self.watchlist = [
            "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AMD",
            "NFLX", "PLTR", "SMCI", "ARM", "AVGO", "TSM", "COIN", "MSTR",
            "SOFI", "HOOD", "RKLB", "IONQ", "ASTS", "OKLO", "PATH", "SNOW",
            "CRWD", "PANW", "NET", "DDOG", "SHOP", "UBER"
        ]
        self.status = "GrokBot v3 ready"
        self.price_history = {}
        self.signal_state = {}
        
        # 2. Strategy Tunings & Volatility Configurations
        self.max_history = 60
        self.fast_period = 8
        self.slow_period = 21
        self.vol_lookback = 10
        self.min_history = 25
        self.max_positions = 6
        self.risk_per_trade = 0.18

    def _ema(self, data, period):
        """Calculates internal Exponential Moving Average signals"""
        if len(data) < period:
            return None
        k = 2.0 / (period + 1.0)
        ema = float(data[0])
        for p in data[1:]:
            ema = float(p) * k + ema * (1.0 - k)
        return ema

    def _avg_range(self, data):
        """Measures historical price range vectors proxy to check volatility values"""
        if len(data) < self.vol_lookback + 1:
            return None
        ranges = [abs(data[i] - data[i - 1]) for i in range(-self.vol_lookback, 0)]
        return sum(ranges) / float(len(ranges))

    def _get_closes(self, data, symbol):
        """Builds a clean tracking series out of the server's candle queues"""
        bars = data.get("intraday_bars", {}).get(symbol)
        if bars and len(bars) >= 5:
            closes = [float(b[4]) for b in bars if b[4] is not None]
            if len(closes) >= 5:
                return closes
                
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
        """Processes the full market data payload returned by engine.py every cycle"""
        orders = []
        if not isinstance(data, dict):
            return orders
            
        market = data.get("market", {})
        is_open = market.get("is_open", False)
        account = data.get("account", {})
        prices = data.get("prices", {})
        positions = account.get("positions", {})
        
        equity = float(account.get("equity", 100.0) or 100.0)
        cash = float(account.get("cash", 0.0) or 0.0)
        max_buy = float(account.get("max_new_buy", 0.0) or 0.0)
        max_short = float(account.get("max_new_short", 0.0) or 0.0)
        open_count = len(positions)
        
        self.status = f"eq=${equity:.2f} pos={open_count} cash=${cash:.2f}"
        
        # Dynamically add a few high-momentum names from movers if room
        movers = data.get("movers", {})
        gainers = movers.get("day_gainers", []) or []
        actives = movers.get("most_actives", []) or []
        candidates = []
        
        for lst in (gainers[:8], actives[:6]):
            for item in lst:
                sym = str(item.get("symbol", "")).upper().replace(".", "-")
                if not sym or sym in self.watchlist:
                    continue
                if item.get("tradable") is False:
                    continue
                px = item.get("price") or 0
                if px < 5.0:
                    continue
                candidates.append(sym)
                
        if candidates and len(self.watchlist) < 28:
            for sym in candidates[:4]:
                if sym not in self.watchlist:
                    self.watchlist.append(sym)
                    
        # Trim watchlist if somehow over limit
        if len(self.watchlist) > 30:
            held = set(positions.keys())
            self.watchlist = [s for s in self.watchlist if s in held] + [s for s in self.watchlist if s not in held]
            self.watchlist = self.watchlist[:30]
            
        if not is_open:
            return orders
            
        for symbol in list(prices.keys()):
            price = prices.get(symbol)
            if price is None or price <= 0:
                continue
            price = float(price)
            
            closes = self._get_closes(data, symbol)
            if len(closes) < self.min_history:
                continue
                
            fast = self._ema(closes, self.fast_period)
            slow = self._ema(closes, self.slow_period)
            vol = self._avg_range(closes)
            
            if fast is None or slow is None or vol is None or vol <= 0:
                continue
                
            recent_move = abs(closes[-1] - closes[-4]) if len(closes) >= 4 else vol
            if recent_move < vol * 0.40:
                continue
                
            bullish = fast > slow * 1.0018
            bearish = fast < slow * 0.9982
            prev = self.signal_state.get(symbol, "FLAT")
            
            pos = positions.get(symbol)
            side = pos["side"] if pos else None
            units = float(pos["units"]) if pos else 0.0
            unrealized = float(pos.get("unrealized_pnl", 0.0)) if pos else 0.0
            value = abs(float(pos.get("value", 0.0))) if pos else 0.0
            
            # Exit logic first
            if side == "LONG":
                if bearish or (unrealized > 0 and abs(fast - slow) / price < 0.0010):
                    orders.append({"symbol": symbol, "action": "SELL", "amount": "ALL"})
                    self.signal_state[symbol] = "FLAT"
                    continue
                if unrealized < -0.035 * value and value > 2.0:
                    orders.append({"symbol": symbol, "action": "SELL", "amount": "ALL"})
                    self.signal_state[symbol] = "FLAT"
                    continue
                    
            if side == "SHORT":
                if bullish or (unrealized > 0 and abs(fast - slow) / price < 0.0010):
                    orders.append({"symbol": symbol, "action": "COVER", "amount": "ALL"})
                    self.signal_state[symbol] = "FLAT"
                    continue
                if unrealized < -0.035 * value and value > 2.0:
                    orders.append({"symbol": symbol, "action": "COVER", "amount": "ALL"})
                    self.signal_state[symbol] = "FLAT"
                    continue
                    
            # Entry logic
            if side is None and open_count < self.max_positions:
                target = min(equity * self.risk_per_trade, max_buy if bullish else max_short)
                if target < 3.0:
                    continue
                if bullish and max_buy >= 3.0 and price >= 1.0:
                    amt = min(target, max_buy * 0.95)
                    if amt >= 3.0:
                        orders.append({"symbol": symbol, "action": "BUY", "amount": round(amt, 2)})
                        self.signal_state[symbol] = "LONG"
                        open_count += 1
                elif bearish and max_short >= 3.0 and price >= 5.0:
                    amt = min(target, max_short * 0.95)
                    if amt >= 3.0:
                        orders.append({"symbol": symbol, "action": "SHORT", "amount": round(amt, 2)})
                        self.signal_state[symbol] = "SHORT"
                        open_count += 1
                        
            if len(orders) >= 18:
                break
                
        return orders[:20]
