
from collections import deque
from datetime import datetime, timezone
from math import sqrt, isfinite


class ChatGPTBot:
    def __init__(self):
        self.watchlist = [
            "NVDA", "TSLA", "AAPL", "MSFT", "AMZN",
            "META", "GOOGL", "AMD", "NFLX", "BRK-B"
        ]

        self.history = {}
        self.position_state = {}
        self.last_trade_time = {}
        self.last_prices = {}

        self.history_limit = 120
        self.minimum_history = 30

        # Strategy parameters
        self.fast_ema_period = 8
        self.slow_ema_period = 21
        self.momentum_period = 12
        self.long_momentum_period = 25
        self.volatility_period = 20

        self.entry_threshold = 0.85
        self.exit_threshold = -0.25
        self.reversal_threshold = 1.10

        self.minimum_trade_interval = 5
        self.minimum_price_change = 0.0015
        self.maximum_volatility = 0.065

    def set_watchlist(self, symbols):
        """Replace the watchlist with any valid stock symbols."""
        if not isinstance(symbols, (list, tuple, set)):
            raise TypeError("Watchlist must be a list, tuple, or set.")

        cleaned = []
        for symbol in symbols:
            if isinstance(symbol, str):
                symbol = symbol.strip().upper()
                if symbol and symbol not in cleaned:
                    cleaned.append(symbol)

        self.watchlist = cleaned

        for symbol in self.watchlist:
            self.history.setdefault(symbol, deque(maxlen=self.history_limit))
            self.position_state.setdefault(symbol, 0)

    @staticmethod
    def _ema(values, period):
        if len(values) < period:
            return None

        multiplier = 2.0 / (period + 1.0)
        result = sum(values[:period]) / period

        for value in values[period:]:
            result = (value - result) * multiplier + result

        return result

    @staticmethod
    def _returns(values, period):
        if len(values) <= period:
            return []

        result = []
        start = len(values) - period

        for index in range(start, len(values)):
            previous = values[index - 1]
            current = values[index]

            if previous > 0:
                result.append((current / previous) - 1.0)

        return result

    @staticmethod
    def _mean(values):
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _standard_deviation(values):
        if len(values) < 2:
            return 0.0

        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)

        return sqrt(variance)

    @staticmethod
    def _parse_timestamp(timestamp_utc):
        if isinstance(timestamp_utc, datetime):
            parsed = timestamp_utc
        else:
            try:
                parsed = datetime.fromisoformat(
                    str(timestamp_utc).replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    def _analyse_asset(self, symbol):
        prices = list(self.history.get(symbol, []))

        if len(prices) < self.minimum_history:
            return None

        current_price = prices[-1]

        fast_ema = self._ema(prices, self.fast_ema_period)
        slow_ema = self._ema(prices, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        short_returns = self._returns(
            prices, self.momentum_period
        )
        long_returns = self._returns(
            prices, self.long_momentum_period
        )
        volatility_returns = self._returns(
            prices, self.volatility_period
        )

        if not short_returns or not long_returns:
            return None

        short_momentum = (
            current_price / prices[-self.momentum_period - 1]
        ) - 1.0

        long_momentum = (
            current_price / prices[-self.long_momentum_period - 1]
        ) - 1.0

        volatility = self._standard_deviation(volatility_returns)

        if volatility > self.maximum_volatility:
            return {
                "score": 0.0,
                "trend": 0,
                "volatility": volatility,
                "price": current_price
            }

        # Normalised trend strength
        trend_strength = (
            (fast_ema - slow_ema) / current_price
        ) * 100.0

        # Momentum confirmation
        momentum_strength = (
            short_momentum * 0.65 + long_momentum * 0.35
        ) * 100.0

        # Short-term price acceleration
        recent_returns = self._returns(prices, 5)
        acceleration = self._mean(recent_returns) * 100.0

        # Risk-adjusted composite score
        risk_denominator = max(volatility * 100.0, 0.10)

        score = (
            trend_strength * 0.45
            + momentum_strength * 0.45
            + acceleration * 0.10
        ) / risk_denominator

        # Directional trend confirmation
        if fast_ema > slow_ema and current_price > fast_ema:
            trend = 1
        elif fast_ema < slow_ema and current_price < fast_ema:
            trend = -1
        else:
            trend = 0

        return {
            "score": score,
            "trend": trend,
            "volatility": volatility,
            "short_momentum": short_momentum,
            "long_momentum": long_momentum,
            "price": current_price
        }

    def _is_on_cooldown(self, symbol, timestamp):
        last_trade = self.last_trade_time.get(symbol)

        if last_trade is None or timestamp is None:
            return False

        elapsed = (timestamp - last_trade).total_seconds()

        return elapsed < self.minimum_trade_interval

    def _record_signal(self, symbol, signal, timestamp):
        if signal == "BUY":
            # BUY covers an internally tracked short or opens a long.
            if self.position_state.get(symbol, 0) == -1:
                self.position_state[symbol] = 0
            else:
                self.position_state[symbol] = 1

        elif signal == "SELL":
            # SELL liquidates an internally tracked long.
            self.position_state[symbol] = 0

        elif signal == "SHORT":
            self.position_state[symbol] = -1

        if timestamp is not None:
            self.last_trade_time[symbol] = timestamp

    def market_tick(self, prices_dict, timestamp_utc):
        """
        Analyse current watchlist prices and return intended broker signals.

        Possible signals:
            BUY   - Open a long position or cover an internally tracked short.
            SELL  - Liquidate an internally tracked long position.
            SHORT - Open a short position.
            HOLD  - No action.

        Only actionable signals are returned.
        """
        signals = {}

        if not isinstance(prices_dict, dict):
            return signals

        timestamp = self._parse_timestamp(timestamp_utc)

        # Update price histories.
        for symbol in self.watchlist:
            price = prices_dict.get(symbol)

            if isinstance(price, bool):
                continue

            try:
                price = float(price)
            except (TypeError, ValueError):
                continue

            if not isfinite(price) or price <= 0:
                continue

            previous_price = self.last_prices.get(symbol)

            # Ignore duplicate or unchanged ticks.
            if previous_price is not None and price == previous_price:
                continue

            self.history.setdefault(
                symbol, deque(maxlen=self.history_limit)
            ).append(price)

            self.last_prices[symbol] = price

        # Analyse assets and generate trading decisions.
        opportunities = []

        for symbol in self.watchlist:
            analysis = self._analyse_asset(symbol)

            if analysis is None:
                continue

            opportunities.append((symbol, analysis))

        # Process strongest opportunities first.
        opportunities.sort(
            key=lambda item: abs(item[1]["score"]),
            reverse=True
        )

        for symbol, analysis in opportunities:
            score = analysis["score"]
            trend = analysis["trend"]
            short_momentum = analysis.get("short_momentum", 0.0)
            long_momentum = analysis.get("long_momentum", 0.0)

            current_state = self.position_state.get(symbol, 0)

            if self._is_on_cooldown(symbol, timestamp):
                continue

            signal = None

            bullish_confirmation = (
                trend == 1
                and short_momentum > self.minimum_price_change
                and long_momentum > 0
            )

            bearish_confirmation = (
                trend == -1
                and short_momentum < -self.minimum_price_change
                and long_momentum < 0
            )

            # Currently long: protect capital and exit on deterioration.
            if current_state == 1:
                if (
                    score < self.exit_threshold
                    or bearish_confirmation
                ):
                    signal = "SELL"

            # Currently short: cover when the bearish thesis fails.
            elif current_state == -1:
                if (
                    score > self.reversal_threshold
                    and bullish_confirmation
                ):
                    signal = "BUY"

            # Currently flat: seek a sufficiently strong directional setup.
            else:
                if (
                    score >= self.entry_threshold
                    and bullish_confirmation
                ):
                    signal = "BUY"

                elif (
                    score <= -self.entry_threshold
                    and bearish_confirmation
                ):
                    signal = "SHORT"

            if signal is not None:
                signals[symbol] = signal
                self._record_signal(symbol, signal, timestamp)

        return signals

    def get_portfolio_value(self, prices_dict=None):
        """Portfolio valuation is handled by the central broker engine."""
        return 0.0
