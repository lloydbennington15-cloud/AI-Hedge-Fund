class GrokBot:
    def __init__(self):
        # 1. Broad Real-World Multi-Asset Server Watchlist Pool
        self.watchlist = [
            "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AMD",
            "NFLX", "PLTR", "SOFI", "COIN", "MSTR", "GME", "SMCI", "ARM",
            "AVGO", "TSM", "QCOM", "INTC"
        ]
        self.price_history = {t: [] for t in self.watchlist}
        self.last_signal = {t: "HOLD" for t in self.watchlist}
        
        # 2. Mathematical Indicators Parameters Tuning Configuration
        self.max_history = 80
        self.fast_period = 8
        self.slow_period = 21
        self.vol_lookback = 12
        self.min_history = self.slow_period + 2

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

    def market_tick(self, prices_dict, timestamp_utc):
        """Processes the live multi-ticker feed frame array independently inside central books"""
        signals = {}
        if not isinstance(prices_dict, dict):
            return signals
            
        for ticker in list(self.watchlist):
            if ticker not in prices_dict:
                continue
            try:
                price = float(prices_dict[ticker])
            except (TypeError, ValueError):
                continue
                
            if price <= 0.0:
                continue
                
            if ticker not in self.price_history:
                self.price_history[ticker] = []
                self.last_signal[ticker] = "HOLD"
                
            hist = self.price_history[ticker]
            hist.append(price)
            
            if len(hist) > self.max_history:
                hist.pop(0)
                
            if len(hist) < self.min_history:
                signals[ticker] = "HOLD"
                continue
                
            fast = self._ema(hist, self.fast_period)
            slow = self._ema(hist, self.slow_period)
            vol = self._avg_range(hist)
            
            if fast is None or slow is None or vol is None or vol <= 0.0:
                signals[ticker] = "HOLD"
                continue
                
            recent_move = abs(hist[-1] - hist[-4]) if len(hist) >= 4 else vol
            if recent_move < vol * 0.42:
                signals[ticker] = "HOLD"
                self.last_signal[ticker] = "HOLD"
                continue
                
            bullish = fast > slow * 1.0015
            bearish = fast < slow * 0.9985
            prev = self.last_signal.get(ticker, "HOLD")
            
            # Complex Strategy Order Routing decisions
            if bullish:
                signals[ticker] = "BUY"
                self.last_signal[ticker] = "BUY"
            elif bearish:
                if prev in ("BUY",):
                    signals[ticker] = "SELL"
                else:
                    signals[ticker] = "SHORT"
                self.last_signal[ticker] = "SHORT"
            else:
                if prev in ("BUY", "SHORT"):
                    momentum_fade = abs(fast - slow) / price < 0.0012
                    if momentum_fade:
                        signals[ticker] = "SELL"
                        self.last_signal[ticker] = "SELL"
                    else:
                        signals[ticker] = "HOLD"
                else:
                    signals[ticker] = "HOLD"
                    
        return signals

    def get_portfolio_value(self, prices_dict=None):
        return 0.0
