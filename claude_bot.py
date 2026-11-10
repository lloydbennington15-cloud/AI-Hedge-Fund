import math
import time
from collections import deque
from datetime import datetime, timedelta, timezone


class ClaudeBot:
    """
    ClaudeBot v4 - Low-Turnover Long/Short Trend Basket (24/7 server engine).

    Core idea: with a 0.1% fee on every trade, the biggest controllable edge
    is trading RARELY. The bot holds a small basket of the strongest-trending
    stocks for hours or days, can short the weakest when the whole market is
    falling, and only acts when a trend is clear on 15-minute candles.

    Signals only - the engine's broker does sizing, fees and accounting.
    A signal is sent only when the bot wants a position to change.

    Indicators per stock (15-minute candles built from engine timestamps):
        fast EMA 12 candles (3 h), slow EMA 40 candles (10 h, ~1.5 trading
        days), ATR 14 candles, 2-hour Efficiency Ratio (cleanliness of move).

    LONG  when: fast EMA > slow EMA, price above both, slow EMA rising over
                the last hour, the move is reasonably clean, the price isn't
                overstretched, and at least half the watchlist trends up.
    SHORT when: the mirror image, and at least 60% of the watchlist trends
                down (shorting fights the market's natural upward drift, so
                it needs a clearly weak market).
    EXIT  when: the trend flips (checked each 15-minute candle), or the
                trailing stop (4 ATRs behind the best price) is hit, or the
                stock reports earnings that evening (gap protection).

    Positions are held overnight - that is where much of the stock market's
    long-run return comes from, and it halves the number of fees paid -
    except into a known earnings report. Trading decisions are only made
    during regular US hours (9:30am-4pm New York), never on thin pre-market
    or after-hours prices. At most 4 positions, 1 new position per candle,
    and 1 entry per stock per day.
    """

    def __init__(self):
        # Large, liquid, easy-to-short stocks across several sectors.
        self.watchlist = ["NVDA", "AMD", "AVGO", "MU", "TSLA", "AAPL", "MSFT",
                          "AMZN", "META", "GOOGL", "NFLX", "PLTR", "COIN",
                          "ORCL", "CRM", "JPM", "XOM", "LLY", "UNH", "COST"]

        # Signal words sent to the engine.
        self.SIG_OPEN_LONG = "BUY"
        self.SIG_CLOSE_LONG = "SELL"
        self.SIG_OPEN_SHORT = "SHORT"
        self.SIG_CLOSE_SHORT = "BUY"     # buy-to-cover; change here if the
                                         # engine expects e.g. "COVER"

        # Strategy parameters.
        self.BAR_SECONDS = 900           # 15-minute candles
        self.FAST_SPAN = 12
        self.SLOW_SPAN = 40
        self.ATR_PERIOD = 14
        self.SLOPE_LOOKBACK = 4          # slow EMA direction over 1 hour
        self.ER_WINDOW = 8               # efficiency ratio over 2 hours
        self.MIN_ER = 0.20
        self.WARMUP_BARS = 16            # 4 hours of live candles per stock
        self.STOP_ATR_MULT = 4.0
        self.MIN_STOP_PCT = 0.008        # skip stocks too quiet to beat fees
        self.MAX_STRETCH_ATR = 3.0
        self.COOLDOWN_BARS = 4           # 1 hour before re-entering a stock
        self.MAX_POSITIONS = 4
        self.MAX_NEW_PER_BAR = 1
        self.MAX_ENTRIES_PER_DAY = 1     # per stock
        self.LONG_BREADTH = 0.50
        self.SHORT_BREADTH = 0.60
        self.ALLOW_SHORTS = True
        self.SPIKE_LIMIT = 0.12          # ignore unconfirmed 12%+ jumps
        self.SPIKE_CONFIRM = 0.02

        # Earnings dates (New York date of the report). If the report is
        # after the close, the stock is closed out by 3:55pm that day and
        # not bought or shorted that day. Covers the tournament window.
        self.EARNINGS = {"NVDA": "2026-11-17"}

        # US market calendar (New York time, minutes after midnight).
        self.SESSION_OPEN = 9 * 60 + 30
        self.SESSION_CLOSE = 16 * 60
        self.HALF_DAY_CLOSE = 13 * 60
        self.NO_ENTRY_BEFORE = 9 * 60 + 45
        self.ENTRY_CUTOFF_MINUTES = 30       # no new trades in the last 30 min
        self.EARNINGS_EXIT_MINUTES = 5       # exit this long before the close
        self.HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01",
                         "2027-01-18", "2027-02-15"}
        self.HALF_DAYS = {"2026-11-27", "2026-12-24"}

        try:
            from zoneinfo import ZoneInfo
            self._new_york = ZoneInfo("America/New_York")
        except Exception:
            self._new_york = None

        # Internal state.
        self.tick_count = 0
        self.last_prices = {}
        self._pending = {}
        self._state = {}
        self._positions = {}             # sym -> {"side","entry","extreme","stop"}
        self._entries_today = {}
        self._day = None
        self.signal_log = []

    # ======================================================================
    # Main entry point
    # ======================================================================
    def market_tick(self, prices_dict, timestamp_utc=None):
        """Receive {symbol: price} for the watchlist plus a UTC timestamp.
        Returns {symbol: "BUY" | "SELL" | "SHORT"} only when something
        should change; an empty dict means hold everything as it is."""
        if not isinstance(prices_dict, dict):
            return {}

        now = self._parse_time(timestamp_utc)
        self.tick_count += 1
        weekday, mins, day = self._new_york_time(now)
        if day != self._day:
            self._day = day
            self._entries_today = {}
        bar_id = int(now // self.BAR_SECONDS)

        closed = set()
        for sym, raw in prices_dict.items():
            if sym not in self._state:
                self._state[sym] = self._new_state()
            price = self._clean_price(sym, raw)
            if price is None:
                continue
            if self._update_bar(sym, price, bar_id):
                closed.add(sym)

        # Never act on pre-market / after-hours / holiday prices.
        if not self._in_session(weekday, mins, day):
            return {}

        signals = {}
        for sym in list(self._positions):
            price = self.last_prices.get(sym)
            if price is None:
                continue
            sig = self._manage_position(sym, price, sym in closed, mins, day)
            if sig:
                signals[sym] = sig

        if (closed and len(self._positions) < self.MAX_POSITIONS
                and self._may_enter(mins, day)):
            up_share, down_share = self._breadth()
            candidates = []
            for sym in closed:
                if sym in self._positions or sym in signals:
                    continue
                if self.EARNINGS.get(sym) == day:
                    continue
                side, score = self._entry_signal(sym, up_share, down_share)
                if side:
                    candidates.append((score, sym, side))
            candidates.sort(reverse=True)
            opened = 0
            for _, sym, side in candidates:
                if (len(self._positions) >= self.MAX_POSITIONS
                        or opened >= self.MAX_NEW_PER_BAR):
                    break
                signals[sym] = self._open(sym, side)
                opened += 1

        return signals

    def get_portfolio_value(self, prices_dict=None):
        """The central broker values the portfolio from its own books."""
        return 0.0

    # ======================================================================
    # Time and market calendar
    # ======================================================================
    def _parse_time(self, ts):
        """Accepts ISO strings ('2026-11-10T17:24:00Z', with or without an
        offset or fractional seconds), epoch seconds/milliseconds, or a
        datetime. Falls back to the server clock if unreadable."""
        try:
            if ts is None:
                return time.time()
            if isinstance(ts, datetime):
                dt = ts
            elif isinstance(ts, (int, float)):
                v = float(ts)
                return v / 1000.0 if v > 1e11 else v
            else:
                text = str(ts).strip()
                try:
                    v = float(text)
                    return v / 1000.0 if v > 1e11 else v
                except ValueError:
                    pass
                text = text.replace("Z", "+00:00").replace(" UTC", "+00:00")
                if "." in text:
                    head, _, tail = text.partition(".")
                    digits, rest = "", ""
                    for i, ch in enumerate(tail):
                        if ch.isdigit():
                            digits += ch
                        else:
                            rest = tail[i:]
                            break
                    text = head + "." + (digits[:6] or "0") + rest
                dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return time.time()

    def _new_york_time(self, now):
        if self._new_york is not None:
            dt = datetime.fromtimestamp(now, tz=self._new_york)
        else:
            # No timezone database: US daylight saving runs from the 2nd
            # Sunday of March to the 1st Sunday of November.
            utc = datetime.fromtimestamp(now, tz=timezone.utc)
            march = datetime(utc.year, 3, 8, 7, tzinfo=timezone.utc)
            dst_start = march + timedelta(days=(6 - march.weekday()) % 7)
            nov = datetime(utc.year, 11, 1, 6, tzinfo=timezone.utc)
            dst_end = nov + timedelta(days=(6 - nov.weekday()) % 7)
            offset = -4 if dst_start <= utc < dst_end else -5
            dt = utc.astimezone(timezone(timedelta(hours=offset)))
        return dt.weekday(), dt.hour * 60 + dt.minute, dt.strftime("%Y-%m-%d")

    def _close_time(self, day):
        return self.HALF_DAY_CLOSE if day in self.HALF_DAYS else self.SESSION_CLOSE

    def _in_session(self, weekday, mins, day):
        if weekday >= 5 or day in self.HOLIDAYS:
            return False
        return self.SESSION_OPEN <= mins < self._close_time(day)

    def _may_enter(self, mins, day):
        cutoff = self._close_time(day) - self.ENTRY_CUTOFF_MINUTES
        return self.NO_ENTRY_BEFORE <= mins < cutoff

    # ======================================================================
    # Price hygiene
    # ======================================================================
    def _clean_price(self, sym, raw):
        try:
            p = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(p) or p <= 0:
            return None
        last = self.last_prices.get(sym)
        if last is not None and abs(p / last - 1) > self.SPIKE_LIMIT:
            pending = self._pending.get(sym)
            if pending is not None and abs(p / pending - 1) <= self.SPIKE_CONFIRM:
                self._pending.pop(sym, None)       # confirmed genuine jump
            else:
                self._pending[sym] = p
                return None
        else:
            self._pending.pop(sym, None)
        self.last_prices[sym] = p
        return p

    # ======================================================================
    # Candles and indicators
    # ======================================================================
    def _new_state(self):
        return {"bar_id": None, "high": None, "low": None, "close": None,
                "prev_close": None, "after_gap": False, "bars": 0,
                "ema_fast": None, "ema_slow": None, "atr": None,
                "slow_hist": deque(maxlen=self.SLOPE_LOOKBACK + 1),
                "closes": deque(maxlen=self.ER_WINDOW + 1),
                "last_exit_bar": -10**9}

    def _update_bar(self, sym, price, bar_id):
        s = self._state[sym]
        if s["bar_id"] is None:
            s["bar_id"] = bar_id
            s["high"] = s["low"] = s["close"] = price
            return False
        if bar_id <= s["bar_id"]:
            s["high"] = max(s["high"], price)
            s["low"] = min(s["low"], price)
            s["close"] = price
            return False
        completed = self._close_bar(s)
        s["bar_id"] = bar_id
        s["high"] = s["low"] = s["close"] = price
        return completed

    def _close_bar(self, s):
        h, l, c, pc = s["high"], s["low"], s["close"], s["prev_close"]
        if h == l and (pc is None or c == pc):          # frozen: market shut
            if pc is not None:
                s["after_gap"] = True
            return False
        if pc is None or s["after_gap"]:
            tr = h - l
        else:
            tr = max(h - l, abs(h - pc), abs(l - pc))
        s["after_gap"] = False

        s["atr"] = tr if s["atr"] is None else s["atr"] + (tr - s["atr"]) / self.ATR_PERIOD
        if s["ema_fast"] is None:
            s["ema_fast"] = s["ema_slow"] = c
        else:
            s["ema_fast"] += 2.0 / (self.FAST_SPAN + 1) * (c - s["ema_fast"])
            s["ema_slow"] += 2.0 / (self.SLOW_SPAN + 1) * (c - s["ema_slow"])
        s["slow_hist"].append(s["ema_slow"])
        s["closes"].append(c)
        s["prev_close"] = c
        s["bars"] += 1
        return True

    def _efficiency_ratio(self, s):
        p = list(s["closes"])
        if len(p) < 2:
            return 0.0
        path = sum(abs(p[i] - p[i - 1]) for i in range(1, len(p)))
        return abs(p[-1] - p[0]) / path if path > 0 else 0.0

    def _breadth(self):
        up = down = total = 0
        for s in self._state.values():
            if s["bars"] < self.WARMUP_BARS or s["ema_fast"] is None:
                continue
            total += 1
            if s["ema_fast"] > s["ema_slow"]:
                up += 1
            elif s["ema_fast"] < s["ema_slow"]:
                down += 1
        if total == 0:
            return 0.0, 0.0
        return up / total, down / total

    # ======================================================================
    # Trading decisions
    # ======================================================================
    def _entry_signal(self, sym, up_share, down_share):
        s = self._state[sym]
        if (s["bars"] < self.WARMUP_BARS
                or len(s["slow_hist"]) <= self.SLOPE_LOOKBACK
                or len(s["closes"]) <= self.ER_WINDOW):
            return None, 0.0
        if s["bars"] - s["last_exit_bar"] < self.COOLDOWN_BARS:
            return None, 0.0
        if self._entries_today.get(sym, 0) >= self.MAX_ENTRIES_PER_DAY:
            return None, 0.0

        price = self.last_prices.get(sym)
        atr = s["atr"]
        if price is None or not atr or atr <= 0:
            return None, 0.0
        if self.STOP_ATR_MULT * atr / price < self.MIN_STOP_PCT:
            return None, 0.0
        er = self._efficiency_ratio(s)
        if er < self.MIN_ER:
            return None, 0.0

        fast, slow = s["ema_fast"], s["ema_slow"]
        strength = (fast - slow) / atr
        stretch = (price - fast) / atr
        rising = s["slow_hist"][-1] > s["slow_hist"][0]
        falling = s["slow_hist"][-1] < s["slow_hist"][0]

        if (fast > slow and price > fast and rising
                and stretch <= self.MAX_STRETCH_ATR
                and up_share >= self.LONG_BREADTH):
            return "LONG", strength * er
        if (self.ALLOW_SHORTS and fast < slow and price < fast and falling
                and -stretch <= self.MAX_STRETCH_ATR
                and down_share >= self.SHORT_BREADTH):
            return "SHORT", -strength * er
        return None, 0.0

    def _open(self, sym, side):
        price = self.last_prices[sym]
        atr = self._state[sym]["atr"]
        if side == "LONG":
            stop, signal = price - self.STOP_ATR_MULT * atr, self.SIG_OPEN_LONG
        else:
            stop, signal = price + self.STOP_ATR_MULT * atr, self.SIG_OPEN_SHORT
        self._positions[sym] = {"side": side, "entry": price,
                                "extreme": price, "stop": stop}
        self._entries_today[sym] = self._entries_today.get(sym, 0) + 1
        self.signal_log.append({"tick": self.tick_count, "day": self._day,
                                "symbol": sym, "signal": signal,
                                "side": side, "price": price})
        return signal

    def _close(self, sym, price, reason):
        pos = self._positions.pop(sym)
        self._state[sym]["last_exit_bar"] = self._state[sym]["bars"]
        signal = self.SIG_CLOSE_LONG if pos["side"] == "LONG" else self.SIG_CLOSE_SHORT
        move = price / pos["entry"] - 1
        if pos["side"] == "SHORT":
            move = -move
        self.signal_log.append({"tick": self.tick_count, "day": self._day,
                                "symbol": sym, "signal": signal,
                                "side": pos["side"], "price": price,
                                "reason": reason,
                                "move_pct": round(move * 100, 3)})
        return signal

    def _manage_position(self, sym, price, bar_closed, mins, day):
        pos = self._positions[sym]
        s = self._state[sym]
        atr = s["atr"]

        if pos["side"] == "LONG":
            if price > pos["extreme"]:
                pos["extreme"] = price
            if atr:
                pos["stop"] = max(pos["stop"], pos["extreme"] - self.STOP_ATR_MULT * atr)
            if price <= pos["stop"]:
                return self._close(sym, price, "STOP")
        else:
            if price < pos["extreme"]:
                pos["extreme"] = price
            if atr:
                pos["stop"] = min(pos["stop"], pos["extreme"] + self.STOP_ATR_MULT * atr)
            if price >= pos["stop"]:
                return self._close(sym, price, "STOP")

        if (self.EARNINGS.get(sym) == day
                and mins >= self._close_time(day) - self.EARNINGS_EXIT_MINUTES):
            return self._close(sym, price, "EARNINGS")

        if bar_closed and s["ema_fast"] is not None:
            if pos["side"] == "LONG" and s["ema_fast"] < s["ema_slow"]:
                return self._close(sym, price, "TREND_END")
            if pos["side"] == "SHORT" and s["ema_fast"] > s["ema_slow"]:
                return self._close(sym, price, "TREND_END")
        return None