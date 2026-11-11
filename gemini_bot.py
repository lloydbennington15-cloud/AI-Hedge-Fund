import numpy as np

class GeminiBot:
    def __init__(self):
        # 1. Initialize a dynamic startup matrix matching the engine constraints
        self.watchlist = ["NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AMD", "NFLX", "PLTR"]
        self.price_history = {}
        self.window_size = 15
        self.num_std = 2.0
        self.status = "🤖 Gemini Volatility Sniper: Active Scanning Mode."

    def market_tick(self, data):
        """Processes the institutional server data matrix across the complete US stock market pool"""
        orders = []
        
        # A. DISCOVERY: Automatically harvest the most explosive movers across thousands of stocks
        movers_list = []
        movers_data = data.get("movers", {})
        for category in ["day_gainers", "day_losers", "most_actives", "small_cap_gainers"]:
            for item in movers_data.get(category, []):
                if item.get("tradable") and item.get("symbol"):
                    movers_list.append(item["symbol"])
                    
        # Update our active watchlist on the server (Capped safely under the 30-stock engine limit)
        if movers_list:
            unique_movers = list(set(movers_list))[:25]
            self.watchlist = unique_movers

        # B. PRICE SYNCHRONIZATION: Read available data frames fed by the server
        current_prices = data.get("prices", {})
        for ticker, price in current_prices.items():
            if ticker not in self.price_history:
                self.price_history[ticker] = []
            self.price_history[ticker].append(float(price))
            
            # Maintain a rolling technical memory footprint per stock
            if len(self.price_history[ticker]) > 50:
                self.price_history[ticker].pop(0)

        # Never issue trades if the market is closed or session links are dark
        if not data.get("market", {}).get("is_open"):
            return []

        # C. MATHEMATICAL EXECUTION LOGIC
        account = data.get("account", {})
        current_positions = account.get("positions", {})
        available_cash = float(account.get("cash", 0.0))

        for ticker in self.watchlist:
            if ticker not in self.price_history or len(self.price_history[ticker]) < self.window_size:
                continue
                
            current_price = current_prices[ticker]
            window = self.price_history[ticker][-self.window_size:]
            mean = np.mean(window)
            std = np.std(window)
            
            upper_band = mean + (self.num_std * std)
            lower_band = mean - (self.num_std * std)
            
            is_held = ticker in current_positions
            position_side = current_positions[ticker]["side"] if is_held else None

            # Breakout Signals Routing Capped to 20-Order Engine Threshold Limits
            if len(orders) >= 20:
                break

            # 1. Breakout Long Entry
            if current_price > upper_band and not is_held and available_cash >= 20.0:
                orders.append({"symbol": ticker, "action": "BUY", "amount": 20.00})
                available_cash -= 20.00
                
            # 2. Breakout Short Entry (Enforces the engine's strict $5 minimum short rule)
            elif current_price < lower_band and not is_held and available_cash >= 20.0 and current_price >= 5.0:
                orders.append({"symbol": ticker, "action": "SHORT", "amount": 20.00})
                available_cash -= 20.00
                
            # 3. Exit Triggers (Trailing breakdown trailing stop floors)
            elif is_held:
                if position_side == "LONG" and current_price < lower_band:
                    orders.append({"symbol": ticker, "action": "SELL", "amount": "ALL"})
                elif position_side == "SHORT" and current_price > upper_band:
                    orders.append({"symbol": ticker, "action": "COVER", "amount": "ALL"})

        return orders
