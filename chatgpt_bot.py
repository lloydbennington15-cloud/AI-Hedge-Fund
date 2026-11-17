
import json
import os


class ChatGPTBot:
    def __init__(self):
        self.watchlist = [
            "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
            "META", "TSLA", "AMD", "AVGO", "NFLX",
            "PLTR", "SOFI", "HOOD", "IONQ", "RGTI",
            "RKLB", "SMCI", "SNOW", "APP", "ARM",
            "U", "NET", "CRWD", "COIN", "MSTR",
            "INTC", "MU", "SHOP", "HIMS", "ONDS"
        ]

        self.status = "Selective trend strategy | 3 positions max"

        self.state = {
            "positions": {},
            "cooldowns": {},
            "last_discovery_tick": -999999
        }

        self.storage_dir = None

    # =========================================================
    # PERSISTENT STATE
    # =========================================================

    def _load_state(self, data):
        self.storage_dir = data.get("storage_dir")

        if not self.storage_dir:
            return

        path = os.path.join(
            self.storage_dir,
            "chatgpt_state.json"
        )

        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)

            if isinstance(saved, dict):
                self.state.update(saved)

        except (OSError, ValueError, TypeError):
            pass

        self.state.setdefault("positions", {})
        self.state.setdefault("cooldowns", {})
        self.state.setdefault("last_discovery_tick", -999999)

    def _save_state(self):
        if not self.storage_dir:
            return

        try:
            os.makedirs(self.storage_dir, exist_ok=True)

            path = os.path.join(
                self.storage_dir,
                "chatgpt_state.json"
            )

            temp = path + ".tmp"

            with open(temp, "w", encoding="utf-8") as f:
                json.dump(self.state, f)

            os.replace(temp, path)

        except (OSError, TypeError, ValueError):
            pass

    # =========================================================
    # TECHNICAL INDICATORS
    # =========================================================

    @staticmethod
    def _ema(values, period):
        if len(values) < period:
            return None

        alpha = 2.0 / (period + 1.0)

        result = sum(values[:period]) / period

        for value in values[period:]:
            result = (
                alpha * value
                + (1.0 - alpha) * result
            )

        return result

    @staticmethod
    def _valid_bars(bars):
        result = []

        for bar in bars or []:
            try:
                close = float(bar[4])
                volume = float(bar[5])

                if close > 0 and volume >= 0:
                    result.append((close, volume))

            except (IndexError, TypeError, ValueError):
                continue

        return result

    def _analyse(self, symbol, data):
        prices = data.get("prices", {})

        try:
            price = float(prices.get(symbol, 0))
        except (TypeError, ValueError):
            return None

        if price < 1:
            return None

        daily = self._valid_bars(
            data.get("daily_bars", {}).get(symbol, [])
        )

        intraday = self._valid_bars(
            data.get("intraday_bars", {}).get(symbol, [])
        )

        if len(daily) < 55 or len(intraday) < 21:
            return None

        daily_closes = [x[0] for x in daily]
        intraday_closes = [x[0] for x in intraday]

        ema20 = self._ema(daily_closes, 20)
        ema50 = self._ema(daily_closes, 50)

        ema9_intra = self._ema(intraday_closes, 9)
        ema20_intra = self._ema(intraday_closes, 20)

        if not all((
            ema20,
            ema50,
            ema9_intra,
            ema20_intra
        )):
            return None

        # Daily trend and momentum.
        trend = (
            ema20 / ema50 - 1.0
        ) * 100.0

        daily_momentum = (
            daily_closes[-1] / daily_closes[-6] - 1.0
        ) * 100.0

        # Intraday VWAP.
        volume_total = sum(v for _, v in intraday)

        if volume_total <= 0:
            return None

        vwap = sum(
            close * volume
            for close, volume in intraday
        ) / volume_total

        vwap_deviation = (
            price / vwap - 1.0
        ) * 100.0

        # Intraday trend and momentum.
        intraday_trend = (
            ema9_intra / ema20_intra - 1.0
        ) * 100.0

        intraday_momentum = (
            intraday_closes[-1]
            / intraday_closes[-6] - 1.0
        ) * 100.0

        # Combined directional score.
        score = (
            0.35 * trend
            + 0.25 * daily_momentum
            + 0.20 * vwap_deviation
            + 0.10 * intraday_trend
            + 0.10 * intraday_momentum
        )

        return {
            "price": price,
            "score": score,
            "trend": trend,
            "daily_momentum": daily_momentum,
            "vwap_deviation": vwap_deviation,
            "intraday_trend": intraday_trend,
            "intraday_momentum": intraday_momentum
        }

    # =========================================================
    # WATCHLIST DISCOVERY
    # =========================================================

    def _discover(self, data):
        tick = int(data.get("tick", 0))

        last_tick = int(
            self.state.get("last_discovery_tick", -999999)
        )

        # Refresh approximately every 15 minutes.
        if tick - last_tick < 45:
            return

        universe = data.get("universe", {})
        candidates = []

        # Always retain existing holdings.
        positions = data.get("account", {}).get(
            "positions", {}
        )

        for symbol in positions:
            if symbol in universe and symbol not in candidates:
                candidates.append(symbol)

        # Discover stocks from market movers.
        movers = data.get("movers", {})

        groups = [
            "day_gainers",
            "day_losers",
            "most_actives",
            "small_cap_gainers"
        ]

        for group in groups:
            for item in movers.get(group, []) or []:

                symbol = item.get("symbol")

                if not symbol or symbol not in universe:
                    continue

                if item.get("tradable") is False:
                    continue

                try:
                    price = float(item.get("price", 0))
                except (TypeError, ValueError):
                    continue

                if price >= 1 and symbol not in candidates:
                    candidates.append(symbol)

        # Retain established stocks as fallback candidates.
        for symbol in self.watchlist:
            if symbol in universe and symbol not in candidates:
                candidates.append(symbol)

        self.watchlist = candidates[:30]

        self.state["last_discovery_tick"] = tick

    # =========================================================
    # POSITION MANAGEMENT
    # =========================================================

    def _manage_positions(self, data):
        account = data.get("account", {})
        positions = account.get("positions", {})
        tick = int(data.get("tick", 0))

        orders = []

        tracked = self.state.setdefault("positions", {})
        cooldowns = self.state.setdefault("cooldowns", {})

        # Remove tracking for positions no longer held.
        for symbol in list(tracked):
            if symbol not in positions:
                del tracked[symbol]

        for symbol, position in positions.items():

            try:
                price = float(
                    data.get("prices", {}).get(symbol, 0)
                )

                entry = float(
                    position.get("avg_price", 0)
                )

            except (TypeError, ValueError):
                continue

            if price <= 0 or entry <= 0:
                continue

            side = str(
                position.get("side", "")
            ).upper()

            if side not in ("LONG", "SHORT"):
                continue

            record = tracked.setdefault(symbol, {
                "side": side,
                "best_price": price
            })

            # Reset tracking if position direction changes.
            if record.get("side") != side:
                record = {
                    "side": side,
                    "best_price": price
                }

                tracked[symbol] = record

            best = float(
                record.get("best_price", price)
            )

            should_exit = False

            if side == "LONG":

                # Highest price reached while holding.
                best = max(best, price)
                record["best_price"] = best

                loss = price / entry - 1.0
                trailing_return = price / best - 1.0

                # Initial stop: -3.5%.
                # Trailing stop: 4% below best price.
                if loss <= -0.035:
                    should_exit = True

                if trailing_return <= -0.040:
                    should_exit = True

                analysis = self._analyse(symbol, data)

                if analysis and analysis["score"] < -0.90:
                    should_exit = True

                if should_exit:
                    orders.append({
                        "symbol": symbol,
                        "action": "SELL",
                        "amount": "ALL"
                    })

            else:

                # Lowest price reached while short.
                best = min(best, price)
                record["best_price"] = best

                loss = price / entry - 1.0

                # Correct short trailing calculation:
                # A rising price is adverse to a short.
                trailing_return = price / best - 1.0

                # Initial stop: price rises 3.5%.
                # Trailing stop: price rises 4% from trough.
                if loss >= 0.035:
                    should_exit = True

                if trailing_return >= 0.040:
                    should_exit = True

                analysis = self._analyse(symbol, data)

                if analysis and analysis["score"] > 0.90:
                    should_exit = True

                if should_exit:
                    orders.append({
                        "symbol": symbol,
                        "action": "COVER",
                        "amount": "ALL"
                    })

            # Apply cooldown only to positions being exited.
            if should_exit:
                cooldowns[symbol] = tick

        return orders

    # =========================================================
    # ENTRY SELECTION
    # =========================================================

    def _find_entry(self, data):
        account = data.get("account", {})
        positions = account.get("positions", {})

        tick = int(data.get("tick", 0))

        # Maximum three open positions.
        if len(positions) >= 3:
            return []

        cooldowns = self.state.setdefault("cooldowns", {})

        candidates = []

        for symbol in self.watchlist:

            if symbol in positions:
                continue

            last_exit = int(
                cooldowns.get(symbol, -999999)
            )

            # Wait at least 15 ticks after an exit.
            if tick - last_exit < 15:
                continue

            analysis = self._analyse(symbol, data)

            if not analysis:
                continue

            score = analysis["score"]

            # LONG entry.
            if (
                score >= 0.65
                and analysis["trend"] > 0
                and analysis["intraday_trend"] > 0
                and analysis["vwap_deviation"] > 0
            ):
                candidates.append((
                    score,
                    symbol,
                    "BUY"
                ))

            # SHORT entry.
            elif (
                score <= -0.65
                and analysis["trend"] < 0
                and analysis["intraday_trend"] < 0
                and analysis["vwap_deviation"] < 0
                and analysis["price"] >= 5
            ):
                candidates.append((
                    abs(score),
                    symbol,
                    "SHORT"
                ))

        if not candidates:
            return []

        # Select strongest opportunity only.
        candidates.sort(reverse=True)

        _, symbol, action = candidates[0]

        try:
            equity = float(account.get("equity", 0))

            if equity <= 0:
                return []

            # Target approximately 28% of account equity.
            target = equity * 0.28

            if action == "BUY":
                available = float(
                    account.get("max_new_buy", 0)
                )
            else:
                available = float(
                    account.get("max_new_short", 0)
                )

            amount = min(target, available)

            if amount < 1:
                return []

            return [{
                "symbol": symbol,
                "action": action,
                "amount": round(amount, 2)
            }]

        except (TypeError, ValueError):
            return []

    # =========================================================
    # MAIN ENGINE INTERFACE
    # =========================================================

    def market_tick(self, data):
        self._load_state(data)

        market = data.get("market", {})

        if not market.get("is_open", False):
            self._save_state()
            return []

        self._discover(data)

        # Exits always take priority over new entries.
        exits = self._manage_positions(data)

        if exits:
            self._save_state()
            return exits[:20]

        # Only consider an entry when no exit is required.
        entries = self._find_entry(data)

        self._save_state()

        return entries[:20]
