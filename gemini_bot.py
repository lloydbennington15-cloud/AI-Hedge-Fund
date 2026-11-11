import numpy as np

class GeminiBot:
    def __init__(self):
        # 1. Initialize a dynamic startup matrix matching the engine constraints
        self.watchlist = ["NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AMD", "NFLX", "PLTR"]
        self.price_history = {}
        self.window_size = 15
        self.num_std = 2.0
        self.status = "🤖 Gemini Volatility Sniper: Dynamic Sizing Active."

    def market_tick(self, data):
        """Processes the institutional server data matrix across the complete US stock market pool"""
        orders = []
        
        # A. DISCOVERY: Harvest explosive movers across the market
        movers_list = []
        movers_data = data.get("movers", {})
        for category in ["day_gainers", "day_losers", "most_actives", "small_cap_gainers"]:
            for item in movers_data.get(category, []):
                if item.get("tradable") and item.get("symbol"):
                    movers_list.append(item["symbol"])
                    
        if movers_list:
            unique_movers = list(set(movers_list))[:25]
            self.watchlist = unique_movers

        # B. PRICE SYNCHRONIZATION: Read available data frames fed by the server
        current_prices = data.get("prices", {})
        for ticker, price in current_prices.items():
            if ticker not in self.price_history:
                self.price_history[ticker] = []
            self.price_history[ticker].append(float(price))
            
            if len(self.price_history[ticker]) > 50:
                self.price_history[ticker].pop(0)

        if not data.get("market", {}).get("is_open"):
            return []

        # C. MATHEMATICAL EXECUTION & DYNAMIC EXPOSURE SIZING
        account = data.get("account", {})
        current_positions = account.get("positions", {})
        equity = float(account.get("equity", 100.0))
        max_buy = float(account.get("max_new_buy", 0.0))
        max_short = float(account.get("max_new_short", 0.0))

        # Target 19.5% of equity per slot (leaves clean buffer for 0.1% fees across 5 positions)
        target_alloc = round(equity * 0.195, 2)

        for ticker in self.watchlist:
            if ticker not in self.price_history or len(self.price_history[ticker]) < self.window_size:
                continue
                
            if ticker not in current_prices:
                continue

            current_price = float(current_prices[ticker])
            window = self.price_history[ticker][-self.window_size:]
            mean = np.mean(window)
            std = np.std(window)
            
            upper_band = mean + (self.num_std * std)
            lower_band = mean - (self.num_std * std)
            
            is_held = ticker in current_positions
            position_side = current_positions[ticker]["side"] if is_held else None

            if len(orders) >= 20:
                break

            # 1. Breakout Long Entry (Checks exact engine buying room)
            if current_price > upper_band and not is_held and max_buy >= 2.0 and current_price >= 1.0:
                trade_amt = round(min(target_alloc, max_buy * 0.99), 2)
                if trade_amt >= 2.0:
                    orders.append({"symbol": ticker, "action": "BUY", "amount": trade_amt})
                    max_buy -= trade_amt * 1.002
                    max_short -= trade_amt
                
            # 2. Breakout Short Entry (Checks exact engine shorting room & $5 price rule)
            elif current_price < lower_band and not is_held and max_short >= 2.0 and current_price >= 5.0:
                trade_amt = round(min(target_alloc, max_short * 0.99), 2)
                if trade_amt >= 2.0:
                    orders.append({"symbol": ticker, "action": "SHORT", "amount": trade_amt})
                    max_short -= trade_amt
                    max_buy -= trade_amt
                
            # 3. Exit Triggers
            elif is_held:
                if position_side == "LONG" and current_price < lower_band:
                    orders.append({"symbol": ticker, "action": "SELL", "amount": "ALL"})
                elif position_side == "SHORT" and current_price > upper_band:
                    orders.append({"symbol": ticker, "action": "COVER", "amount": "ALL"})

        return orders
