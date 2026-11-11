
import os
import json
import math
from datetime import datetime, timezone


class ChatGPTBot:
    """
    ChatGPT Trading Tournament Bot

    Strategy:
    - Market-wide stock discovery using the engine's movers lists.
    - Daily trend analysis using EMA 20/50 and multi-week momentum.
    - Intraday confirmation using VWAP and short-term momentum.
    - Long and short trading.
    - Volatility-aware position selection.
    - Persistent trade tracking and trailing stops.
    - Conservative position sizing to limit turnover and fees.
    """

    def __init__(self):
        self.watchlist = [
            "NVDA", "TSLA", "AAPL", "MSFT", "AMZN",
            "META", "GOOGL", "AMD", "NFLX", "AVGO",
            "PLTR", "MSTR", "COIN", "HOOD", "APP",
            "MU", "SMCI", "ORCL", "CRM", "PANW",
            "JPM", "BAC", "LLY", "XOM", "GE",
            "WMT", "COST", "INTC", "ARM", "SOFI"
        ]

        self.status = "Initialising market analysis..."

        self.storage_dir = None
        self.state_file = None

        self.state = {
            "positions": {},
            "last_trade": {},
            "last_watchlist_update": "",
            "last_session": "",
            "last_tick": 0
        }

        self.max_positions = 3
        self.position_fraction = 0.32
        self.stop_loss = 0.035
        self.trailing_stop = 0.045

        self.minimum_daily_history = 55
        self.minimum_intraday_bars = 20

        self._loaded = False

    # ---------------------------------------------------------
    # Persistent storage
    # ---------------------------------------------------------

    def _load_state(self, data):
        if self._loaded:
            return

        self._loaded = True
        self.storage_dir = data.get("storage_dir")

        if not self.storage_dir:
            return

        try:
            os.makedirs(self.storage_dir, exist_ok=True)
            self.state_file = os.path.join(
                self.storage_dir, "chatgpt_strategy.json"
            )

            if os.path.exists(self.state_file):
                with open(self.state_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)

                if isinstance(saved, dict):
                    for key in self.state:
                        if key in saved:
                            self.state[key] = saved[key]

        except Exception:
            self.status = "Persistent state unavailable; using fresh strategy state."

    def _save_state(self):
        if not self.state_file:
            return

        try:
            temp_file = self.state_file + ".tmp"

            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, separators=(",", ":"))

            os.replace(temp_file, self.state_file)

        except Exception:
            pass

    # ---------------------------------------------------------
    # General helpers
    # ---------------------------------------------------------

    @staticmethod
    def _number(value, default=0.0):
        try:
            result = float(value)
            if math.isfinite(result):
                return result
        except (TypeError, ValueError):
            pass

        return default

    @staticmethod
    def _ema(values, period):
        if len(values) < period:
            return None

        multiplier = 2.0 / (period + 1.0)
        result = sum(values[:period]) / period

        for value in values[period:]:
            result += multiplier * (value - result)

        return result

    @staticmethod
    def _timestamp(value):
        try:
            return datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _percentage_change(current, previous):
        if previous <= 0:
            return 0.0

        return (current / previous - 1.0) * 100.0

    # ---------------------------------------------------------
    # Market-wide stock discovery
    # ---------------------------------------------------------

    def _discover_stocks(self, data):
        movers = data.get("movers", {})
        universe = data.get("universe", {})
        market = data.get("market", {})

        candidates = []
        seen = set()

        # Existing positions must remain monitored.
        positions = data.get("account", {}).get("positions", {})

        for symbol in positions:
            if symbol not in seen:
                candidates.append(symbol)
                seen.add(symbol)

        # Prioritise stocks showing unusual market activity.
        categories = [
            "most_actives",
            "day_gainers",
            "day_losers",
            "small_cap_gainers"
        ]

        for category in categories:
            for stock in movers.get(category, []):
                symbol = str(stock.get("symbol", "")).upper().strip()

                if not symbol or symbol in seen:
                    continue

                if not stock.get("tradable", False):
                    continue

                price = self._number(stock.get("price"))

                if price < 5.0:
                    continue

                if symbol not in universe and data.get("universe_complete"):
                    continue

                candidates.append(symbol)
                seen.add(symbol)

        # Retain existing watchlist names where possible.
        for symbol in self.watchlist:
            if symbol in seen:
                continue

            if symbol in universe or not data.get("universe_complete"):
                candidates.append(symbol)
                seen.add(symbol)

        # Maximum accepted watchlist size is 30.
        self.watchlist = candidates[:30]

        if not self.watchlist:
            self.watchlist = ["NVDA", "AAPL", "MSFT", "AMZN", "META"]

        self.state["last_watchlist_update"] = (
            market.get("new_york_date", "")
            + " "
            + market.get("new_york_time", "")
        )

    # ---------------------------------------------------------
    # Daily market analysis
    # ---------------------------------------------------------

    def _analyse_daily(self, symbol, daily_bars):
        if not daily_bars or len(daily_bars) < self.minimum_daily_history:
            return None

        closes = [
            self._number(bar[4])
            for bar in daily_bars
            if len(bar) >= 6 and self._number(bar[4]) > 0
        ]

        if len(closes) < self.minimum_daily_history:
            return None

        price = closes[-1]

        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)

        if ema20 is None or ema50 is None:
            return None

        momentum_5 = self._percentage_change(price, closes[-6])
        momentum_20 = self._percentage_change(price, closes[-21])
        momentum_50 = self._percentage_change(price, closes[-51])

        trend = 0

        if price > ema20 > ema50:
            trend = 1
        elif price < ema20 < ema50:
            trend = -1

        # Daily trend score.
        score = (
            momentum_5 * 0.15
            + momentum_20 * 0.50
            + momentum_50 * 0.20
            + ((price - ema20) / ema20 * 100.0) * 0.15
        )

        return {
            "trend": trend,
            "score": score,
            "momentum_5": momentum_5,
            "momentum_20": momentum_20,
            "momentum_50": momentum_50,
            "ema20": ema20,
            "ema50": ema50,
            "price": price
        }

    # ---------------------------------------------------------
    # Intraday analysis
    # ---------------------------------------------------------

    def _analyse_intraday(self, symbol, bars, current_price):
        if not bars or len(bars) < self.minimum_intraday_bars:
            return None

        closes = []
        volumes = []
        highs = []
        lows = []
        typical_values = []

        for bar in bars:
            if len(bar) < 6:
                continue

            close = self._number(bar[4])
            high = self._number(bar[2])
            low = self._number(bar[3])
            volume = self._number(bar[5])

            if close <= 0:
                continue

            closes.append(close)
            highs.append(high)
            lows.append(low)
            volumes.append(volume)

            typical_values.append(
                (high + low + close) / 3.0
            )

        if len(closes) < self.minimum_intraday_bars:
            return None

        # VWAP.
        total_volume = sum(volumes)

        if total_volume > 0:
            vwap = sum(
                price * volume
                for price, volume in zip(typical_values, volumes)
            ) / total_volume
        else:
            vwap = self._ema(closes, 20) or current_price

        momentum_5 = self._percentage_change(
            current_price, closes[max(0, len(closes) - 6)]
        )

        momentum_15 = self._percentage_change(
            current_price, closes[max(0, len(closes) - 16)]
        )

        ema9 = self._ema(closes, 9)
        ema20 = self._ema(closes, 20)

        if ema9 is None or ema20 is None:
            return None

        recent_high = max(highs[-20:])
        recent_low = min(lows[-20:])

        score = (
            momentum_5 * 0.30
            + momentum_15 * 0.40
            + self._percentage_change(current_price, vwap) * 0.30
        )

        direction = 0

        if current_price > vwap and ema9 > ema20:
            direction = 1
        elif current_price < vwap and ema9 < ema20:
            direction = -1

        return {
            "score": score,
            "direction": direction,
            "momentum_5": momentum_5,
            "momentum_15": momentum_15,
            "vwap": vwap,
            "ema9": ema9,
            "ema20": ema20,
            "recent_high": recent_high,
            "recent_low": recent_low
        }

    # ---------------------------------------------------------
    # Combined strategy score
    # ---------------------------------------------------------

    def _analyse_stock(self, symbol, data):
        price = self._number(data.get("prices", {}).get(symbol))

        if price < 5.0:
            return None

        daily = self._analyse_daily(
            symbol,
            data.get("daily_bars", {}).get(symbol, [])
        )

        intraday = self._analyse_intraday(
            symbol,
            data.get("intraday_bars", {}).get(symbol, []),
            price
        )

        if daily is None or intraday is None:
            return None

        # Daily and intraday trends must agree.
        if daily["trend"] == 0 or intraday["direction"] == 0:
            return None

        if daily["trend"] != intraday["direction"]:
            return None

        direction = daily["trend"]

        # Avoid buying an already exhausted intraday move.
        if direction == 1 and intraday["momentum_5"] > 3.0:
            return None

        if direction == -1 and intraday["momentum_5"] < -3.0:
            return None

        combined_score = (
            daily["score"] * 0.60
            + intraday["score"] * 0.40
        )

        # Reward agreement between daily and intraday momentum.
        if direction == 1:
            if daily["momentum_20"] <= 0:
                return None
            if intraday["momentum_15"] <= 0:
                return None

        else:
            if daily["momentum_20"] >= 0:
                return None
            if intraday["momentum_15"] >= 0:
                return None

        return {
            "symbol": symbol,
            "price": price,
            "direction": direction,
            "score": combined_score,
            "daily": daily,
            "intraday": intraday
        }

    # ---------------------------------------------------------
    # Position management
    # ---------------------------------------------------------

    def _manage_positions(self, data):
        account = data.get("account", {})
        positions = account.get("positions", {})
        prices = data.get("prices", {})
        orders = []

        for symbol, position in positions.items():
            units = self._number(position.get("units"))
            entry = self._number(position.get("avg_price"))
            price = self._number(prices.get(symbol))

            if not units or entry <= 0 or price <= 0:
                continue

            side = "LONG" if units > 0 else "SHORT"

            tracking = self.state["positions"].setdefault(
                symbol,
                {
                    "side": side,
                    "best_price": price,
                    "entry_price": entry
                }
            )

            # Reset tracking if the position direction has changed.
            if tracking.get("side") != side:
                tracking = {
                    "side": side,
                    "best_price": price,
                    "entry_price": entry
                }
                self.state["positions"][symbol] = tracking

            best_price = self._number(tracking.get("best_price"), price)

            if side == "LONG":
                best_price = max(best_price, price)
                tracking["best_price"] = best_price

                loss = (price / entry) - 1.0
                trailing_return = (price / best_price) - 1.0

                should_exit = (
                    loss <= -self.stop_loss
                    or trailing_return <= -self.trailing_stop
                )

            else:
                best_price = min(best_price, price)
                tracking["best_price"] = best_price

                loss = (entry / price) - 1.0
                trailing_return = (best_price / price) - 1.0

                should_exit = (
                    loss <= -self.stop_loss
                    or trailing_return <= -self.trailing_stop
                )

            # Exit when the broader trend reverses.
            analysis = self._analyse_stock(symbol, data)

            if analysis is not None:
                if side == "LONG" and analysis["direction"] == -1:
                    should_exit = True

                elif side == "SHORT" and analysis["direction"] == 1:
                    should_exit = True

            if should_exit:
                orders.append({
                    "symbol": symbol,
                    "action": "SELL" if side == "LONG" else "COVER",
                    "amount": "ALL"
                })

                self.state["last_trade"][symbol] = data.get("tick", 0)

        return orders

    # ---------------------------------------------------------
    # Entry selection
    # ---------------------------------------------------------

    def _find_opportunities(self, data):
        opportunities = []

        for symbol in self.watchlist:
            if symbol in data.get("account", {}).get("positions", {}):
                continue

            analysis = self._analyse_stock(symbol, data)

            if analysis is None:
                continue

            # Require a meaningful risk-adjusted signal.
            if analysis["direction"] == 1 and analysis["score"] < 0.45:
                continue

            if analysis["direction"] == -1 and analysis["score"] > -0.45:
                continue

            opportunities.append(analysis)

        # Strongest signals first.
        opportunities.sort(
            key=lambda item: abs(item["score"]),
            reverse=True
        )

        return opportunities

    # ---------------------------------------------------------
    # Main engine interface
    # ---------------------------------------------------------

    def market_tick(self, data):
        if not isinstance(data, dict):
            return []

        self._load_state(data)

        account = data.get("account", {})
        market = data.get("market", {})
        tick = data.get("tick", 0)

        self.state["last_tick"] = tick

        # Discover new opportunities periodically.
        current_session = market.get("new_york_date", "")

        if (
            not self.state.get("last_watchlist_update")
            or current_session != self.state.get("last_session")
        ):
            self._discover_stocks(data)
            self.state["last_session"] = current_session

        elif tick % 45 == 0:
            self._discover_stocks(data)

        # Housekeeping can continue outside market hours.
        if not market.get("is_open", False):
            self.status = (
                f"Market closed | Monitoring {len(self.watchlist)} stocks"
            )
            self._save_state()
            return []

        orders = []

        # First manage existing positions.
        exit_orders = self._manage_positions(data)
        orders.extend(exit_orders)

        positions = account.get("positions", {})
        open_count = len(positions)

        # Avoid opening new positions on the same cycle as exits.
        # Account limits are a pre-execution snapshot.
        if exit_orders:
            self.status = (
                f"Managing {open_count} positions | "
                f"Closing {len(exit_orders)} position(s)"
            )
            self._save_state()
            return orders

        if open_count >= self.max_positions:
            self.status = (
                f"Holding {open_count} positions | "
                f"Scanning for exit signals"
            )
            self._save_state()
            return orders

        # Find the strongest available opportunity.
        opportunities = self._find_opportunities(data)

        if not opportunities:
            self.status = (
                f"Scanning {len(self.watchlist)} stocks | "
                f"No sufficiently strong signals"
            )
            self._save_state()
            return orders

        best = opportunities[0]
        equity = self._number(account.get("equity"))
        remaining_slots = self.max_positions - open_count

        if equity <= 0 or remaining_slots <= 0:
            self.status = "Capital protection mode"
            self._save_state()
            return orders

        # Use only the engine's current approved exposure limits.
        if best["direction"] == 1:
            available = self._number(account.get("max_new_buy"))
            action = "BUY"
        else:
            available = self._number(account.get("max_new_short"))
            action = "SHORT"

        target_amount = equity * self.position_fraction

        # Avoid excessive concentration and rejected orders.
        amount = min(target_amount, available)

        if amount < 1.0:
            self.status = "Insufficient available exposure for a new position"
            self._save_state()
            return orders

        orders.append({
            "symbol": best["symbol"],
            "action": action,
            "amount": round(amount, 2)
        })

        self.state["positions"][best["symbol"]] = {
            "side": "LONG" if action == "BUY" else "SHORT",
            "best_price": best["price"],
            "entry_price": best["price"]
        }

        self.state["last_trade"][best["symbol"]] = tick

        self.status = (
            f"{action} {best['symbol']} | "
            f"Signal strength: {best['score']:.2f} | "
            f"Daily/intraday trends aligned | "
            f"Position: ${amount:.2f}"
        )

        self._save_state()

        return orders
