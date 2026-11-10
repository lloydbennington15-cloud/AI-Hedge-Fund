import numpy as np

class GeminiOfficialBot:
    def __init__(self, window_size=10, num_std=2.0):
        self.window_size = window_size
        self.num_std = num_std
        
        # 1. Unrestricted Server Watchlist (Change, remove, or expand tickers to scan the whole market!)
        self.watchlist = ["NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AMD", "NFLX", "BRK-B", "PLTR", "SMCI"]
        self.price_history = {ticker: [] for ticker in self.watchlist}

    def market_tick(self, prices_dict, timestamp_utc):
        """Scans the custom server pool watchlists and returns signal instructions directly to central books"""
        signals = {}
        for ticker in self.watchlist:
            if ticker not in prices_dict:
                continue
                
            current_price = float(prices_dict[ticker])
            self.price_history[ticker].append(current_price)
            
            if len(self.price_history[ticker]) > 40:
                self.price_history[ticker].pop(0)
                
            if len(self.price_history[ticker]) < self.window_size:
                continue

            window = self.price_history[ticker][-self.window_size:]
            mean = np.mean(window)
            std = np.std(window)
            
            upper_band = mean + (self.num_std * std)
            lower_band = mean - (self.num_std * std)

            # Volatility Sniper Execution Decisions: Includes Longs and Shorts!
            if current_price > upper_band:
                signals[ticker] = "BUY"
            elif current_price < lower_band:
                signals[ticker] = "SHORT"
            else:
                signals[ticker] = "HOLD"
                
        return signals

    def get_portfolio_value(self, prices_dict=None):
        return 0.0  # Delegated entirely to central broker server books
