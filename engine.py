#!/usr/bin/env python3
"""
engine.py - AI Trading Tournament engine
========================================

This is the part that actually runs the tournament. It is a normal Python
program that keeps running on the server whether or not anyone has the
dashboard open:

    python engine.py            run forever (the normal way)
    python engine.py --once     run a single cycle and stop (for testing)

Every cycle it:
  1. works out whether the US stock market is open (New York time, weekends,
     holidays and half days included);
  2. downloads prices for every stock any bot is watching or holding;
  3. hands every bot the same snapshot of market data plus its own account;
  4. carries out each bot's orders at the same prices, with the same fee;
  5. checks every account against the risk rules;
  6. saves everything to tournament_state.json, which the dashboard reads.

Bots live in gemini_bot.py, claude_bot.py, chatgpt_bot.py, grok_bot.py and
fable_bot.py in this folder. If a bot file is replaced, the engine notices
within a minute and loads the new version; the bot's account and positions
carry on.

Saved files (state, logs, each bot's own folder) go in this folder, or in the
folder named by the TOURNAMENT_DATA_DIR environment variable if it is set.

All the rules are in the SETTINGS block below.
"""

import copy
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import time
import traceback
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

try:
    from zoneinfo import ZoneInfo
    NEW_YORK = ZoneInfo("America/New_York")
except Exception:          # no timezone database installed
    NEW_YORK = None

UTC = timezone.utc
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _path(name):
    return os.path.join(BASE_DIR, name)


# Where saved files go (separate from the code when TOURNAMENT_DATA_DIR is set).
DATA_DIR = os.path.abspath(os.environ.get("TOURNAMENT_DATA_DIR") or BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)


def _data(name):
    return os.path.join(DATA_DIR, name)


# =============================================================================
# SETTINGS
# =============================================================================
# (display name, python file without .py, class name inside that file)
BOTS = [
    ("Gemini", "gemini_bot", "GeminiBot"),
    ("Claude", "claude_bot", "ClaudeBot"),
    ("ChatGPT", "chatgpt_bot", "ChatGPTBot"),
    ("Grok", "grok_bot", "GrokBot"),
    ("Fable", "fable_bot", "FableBot"),
]

STARTING_CASH = 100.0          # US dollars per bot
FEE_RATE = 0.001               # 0.1% of the value of every order
MIN_ORDER_VALUE = 1.0          # smallest order, in dollars
MAX_GROSS_EXPOSURE = 1.0       # longs + shorts may not exceed 1x equity (no leverage)
MARGIN_CALL_EXPOSURE = 1.5     # if price moves push it past 1.5x, the engine
                               # closes positions until it is back to 1x
MIN_PRICE = 1.0                # no buying or shorting stocks under $1
MIN_SHORT_PRICE = 5.0          # no shorting stocks under $5
ALLOW_ETFS = False             # stocks only (no ETFs) when False
MAX_WATCHLIST = 30             # stocks each bot can watch at once
MAX_ORDERS_PER_TICK = 20       # orders each bot can send per cycle
STALE_PRICE_MINUTES = 30       # refuse orders on a price older than this
                               # while the market is open (halted/illiquid)

POLL_SECONDS_OPEN = 20         # cycle length while the market is open
POLL_SECONDS_CLOSED = 60       # cycle length while it is closed
CLOSED_PRICE_REFRESH_SECONDS = 900   # price refresh when closed
MOVERS_REFRESH_SECONDS = 900         # market movers lists refresh
DAILY_HISTORY_PERIOD = "6mo"         # daily candles given to bots
SLOW_BOT_SECONDS = 5.0               # a bot taking longer is flagged
EQUITY_SNAPSHOT_SECONDS = 300        # chart point every 5 min when open
MAX_LEDGER_ROWS = 2000
MAX_HISTORY_POINTS = 20000
DOWNLOAD_CHUNK = 80                  # tickers per Yahoo request

ENGINE_STALE_SECONDS = 180           # heartbeat older than this = not running
AUTO_START_FROM_DASHBOARD = os.environ.get("TOURNAMENT_AUTOSTART", "1") == "1"
                                     # dashboard starts the engine if it isn't running

STATE_FILE = _data("tournament_state.json")
LOCK_FILE = _data("engine.lock")
COMMAND_FILE = _data("engine_commands.json")
UNIVERSE_CACHE = _data("universe_cache.json")
LOG_FILE = _data("engine.log")
ENGINE_STDOUT = _data("engine_output.log")
BOT_DATA_DIR = _data("bot_data")
LOG_MAX_BYTES = int(os.environ.get("TOURNAMENT_LOG_MAX_BYTES", "5000000"))

MAX_PRICE_BACKOFF_SECONDS = 300      # slowest price refresh if Yahoo throttles us
SPLIT_GAP_RECHECK = 0.40             # re-check split records if a held stock's
                                     # price jumps this much against its history

NASDAQ_SYMBOL_URLS = [
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
]

# NYSE / Nasdaq calendar (New York dates)
HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
HALF_DAYS = {"2026-11-27", "2026-12-24", "2027-11-26"}   # close at 1pm New York

ACTIONS = ("BUY", "SELL", "SHORT", "COVER")
SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")
NOT_A_STOCK_RE = re.compile(r"\b(warrants?|rights?|units?|notes? due|debentures?)\b", re.I)

RULES = {
    "currency": "USD",
    "starting_cash": STARTING_CASH,
    "fee_rate": FEE_RATE,
    "min_order_value": MIN_ORDER_VALUE,
    "max_gross_exposure": MAX_GROSS_EXPOSURE,
    "margin_call_exposure": MARGIN_CALL_EXPOSURE,
    "min_price": MIN_PRICE,
    "min_short_price": MIN_SHORT_PRICE,
    "etfs_allowed": ALLOW_ETFS,
    "max_watchlist": MAX_WATCHLIST,
    "max_orders_per_tick": MAX_ORDERS_PER_TICK,
    "stale_price_minutes": STALE_PRICE_MINUTES,
    "poll_seconds_open": POLL_SECONDS_OPEN,
    "poll_seconds_closed": POLL_SECONDS_CLOSED,
    "slow_bot_seconds": SLOW_BOT_SECONDS,
}


# =============================================================================
# Small helpers
# =============================================================================
def utc_now():
    return datetime.now(UTC)


def iso(dt):
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def parse_iso(text):
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        return None


def to_new_york(dt):
    if NEW_YORK is not None:
        return dt.astimezone(NEW_YORK)
    # Fallback without a timezone database: US daylight saving rules.
    utc = dt.astimezone(UTC)
    march = datetime(utc.year, 3, 8, 7, tzinfo=UTC)
    dst_start = march + timedelta(days=(6 - march.weekday()) % 7)
    nov = datetime(utc.year, 11, 1, 6, tzinfo=UTC)
    dst_end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    offset = -4 if dst_start <= utc < dst_end else -5
    return utc.astimezone(timezone(timedelta(hours=offset)))


def log(message):
    line = f"{iso(utc_now())}  {message}"
    print(line, flush=True)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def atomic_write_json(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def normalize_symbol(value):
    if value is None:
        return ""
    return str(value).strip().upper().replace(".", "-").replace("/", "-")


def number(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# =============================================================================
# Market calendar
# =============================================================================
def market_status(now):
    ny = to_new_york(now)
    day = ny.strftime("%Y-%m-%d")
    trading_day = ny.weekday() < 5 and day not in HOLIDAYS
    open_t = ny.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = ny.replace(hour=13 if day in HALF_DAYS else 16, minute=0,
                         second=0, microsecond=0)
    is_open = trading_day and open_t <= ny < close_t
    if is_open:
        session = "open"
    elif ny.weekday() >= 5:
        session = "weekend"
    elif day in HOLIDAYS:
        session = "holiday"
    elif ny < open_t:
        session = "pre-market"
    else:
        session = "after-hours"
    return {
        "is_open": is_open,
        "session": session,
        "new_york_date": day,
        "new_york_time": ny.strftime("%H:%M:%S"),
        "is_trading_day": trading_day,
        "is_half_day": day in HALF_DAYS,
        "opens_at_utc": iso(open_t) if trading_day else None,
        "closes_at_utc": iso(close_t) if trading_day else None,
    }


# =============================================================================
# The whole market: every listed stock on NYSE, Nasdaq and the other US exchanges
# =============================================================================
def parse_symbol_directory(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {}
    header = lines[0].split("|")
    out = {}
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        row = dict(zip(header, line.split("|")))
        raw = row.get("Symbol") or row.get("ACT Symbol")
        if not raw or row.get("Test Issue") == "Y":
            continue
        if row.get("ETF") == "Y" and not ALLOW_ETFS:
            continue
        name = (row.get("Security Name") or "").strip()
        if NOT_A_STOCK_RE.search(name):
            continue
        sym = normalize_symbol(raw)
        if SYMBOL_RE.match(sym):
            out[sym] = name
    return out


def load_universe(force=False):
    cache = read_json(UNIVERSE_CACHE)
    if cache and not force and time.time() - cache.get("saved_at", 0) < 86400:
        return cache["symbols"], "official exchange lists (cached today)"
    try:
        symbols = {}
        for url in NASDAQ_SYMBOL_URLS:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                symbols.update(parse_symbol_directory(r.read().decode("utf-8", "replace")))
        if len(symbols) < 1000:
            raise ValueError(f"only {len(symbols)} symbols found")
        atomic_write_json(UNIVERSE_CACHE, {"saved_at": time.time(), "symbols": symbols})
        return symbols, "official exchange lists (downloaded today)"
    except Exception as e:
        log(f"Could not download the stock list: {e}")
        if cache:
            return cache["symbols"], "official exchange lists (older copy - today's download failed)"
        return None, "stock list unavailable - any valid-looking ticker with a price is accepted"


# =============================================================================
# Market data from Yahoo Finance (no API key needed)
# =============================================================================
class YahooData:
    """All network access to market data goes through here."""

    def __init__(self):
        import yfinance  # noqa: F401  (fail early with a clear error if missing)
        self.splits = {}          # symbol -> [(date, ratio), ...] from the daily data
        self.throttled = False    # True when Yahoo answered "too many requests"

    @staticmethod
    def _splits(frame):
        out = []
        try:
            if frame is None or "Stock Splits" not in frame.columns:
                return out
            for ts, val in frame["Stock Splits"].items():
                ratio = number(val)
                if ratio and ratio > 0 and abs(ratio - 1.0) > 1e-9:
                    out.append((ts.strftime("%Y-%m-%d"), ratio))
        except Exception:
            pass
        return out

    @staticmethod
    def _frames(data, symbols):
        out = {}
        if data is None or getattr(data, "empty", True):
            return out
        cols = data.columns
        if getattr(cols, "nlevels", 1) > 1:
            for level in range(cols.nlevels):
                values = set(cols.get_level_values(level))
                hits = [s for s in symbols if s in values]
                if hits:
                    for s in hits:
                        try:
                            out[s] = data.xs(s, axis=1, level=level)
                        except Exception:
                            pass
                    break
        elif len(symbols) == 1:
            out[symbols[0]] = data
        return out

    @staticmethod
    def _rows(frame, intraday):
        if frame is None or "Close" not in frame.columns:
            return []
        frame = frame.dropna(subset=["Close"])
        if frame.empty:
            return []
        idx = frame.index
        try:
            idx = idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")
        except Exception:
            pass

        def column(name):
            return frame[name].tolist() if name in frame.columns else [None] * len(frame)

        o, h, lo, c, v = (column(n) for n in ("Open", "High", "Low", "Close", "Volume"))
        rows = []
        for i, ts in enumerate(idx):
            close = number(c[i])
            if close is None or close <= 0:
                continue
            stamp = ts.isoformat() if intraday else ts.strftime("%Y-%m-%d")
            rows.append((stamp,
                         number(o[i]) or close, number(h[i]) or close,
                         number(lo[i]) or close, close, int(number(v[i]) or 0)))
        return rows

    @staticmethod
    def _looks_throttled(text):
        text = str(text).lower()
        return "rate limit" in text or "too many requests" in text or "429" in text

    def _download(self, symbols, period, interval, actions=False):
        import yfinance as yf
        results, errors = {}, []
        for start in range(0, len(symbols), DOWNLOAD_CHUNK):
            chunk = symbols[start:start + DOWNLOAD_CHUNK]
            try:
                data = yf.download(tickers=" ".join(chunk), period=period, interval=interval,
                                   group_by="ticker", auto_adjust=True, prepost=False,
                                   actions=actions, threads=True, progress=False)
                for sym, frame in self._frames(data, chunk).items():
                    rows = self._rows(frame, intraday=(interval != "1d"))
                    if rows:
                        results[sym] = rows
                        if actions:
                            self.splits[sym] = self._splits(frame)
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}")
                if self._looks_throttled(e):
                    self.throttled = True
            try:                                   # per-stock failures yfinance kept quiet
                failures = getattr(getattr(yf, "shared", None), "_ERRORS", None) or {}
                if any(self._looks_throttled(v) for v in failures.values()):
                    self.throttled = True
            except Exception:
                pass
        return results, errors

    def intraday(self, symbols):
        """Today's 1-minute candles (the latest session if closed)."""
        self.throttled = False
        return self._download(symbols, "1d", "1m")

    def daily(self, symbols):
        """About six months of daily candles, plus any stock-split records."""
        return self._download(symbols, DAILY_HISTORY_PERIOD, "1d", actions=True)

    def movers(self):
        import yfinance as yf
        if not hasattr(yf, "screen"):
            return {}, ["this yfinance version has no screener - run: pip install -U yfinance"]
        out, errors = {}, []
        for key in ("day_gainers", "day_losers", "most_actives", "small_cap_gainers"):
            try:
                try:
                    res = yf.screen(key, count=25)
                except TypeError:
                    res = yf.screen(key)
                quotes = res.get("quotes", []) if isinstance(res, dict) else []
                out[key] = [{
                    "symbol": normalize_symbol(q.get("symbol")),
                    "name": q.get("shortName") or q.get("longName") or "",
                    "price": number(q.get("regularMarketPrice")),
                    "change_pct": number(q.get("regularMarketChangePercent")),
                    "volume": number(q.get("regularMarketVolume")),
                    "market_cap": number(q.get("marketCap")),
                } for q in quotes if q.get("symbol")]
            except Exception as e:
                errors.append(f"{key}: {type(e).__name__}: {e}")
        return out, errors


# =============================================================================
# Accounts and order execution
# =============================================================================
def new_account():
    return {"cash": STARTING_CASH, "positions": {}, "fees_paid": 0.0,
            "realized_pnl": 0.0, "closed_trades": 0, "winning_trades": 0,
            "orders_filled": 0, "orders_rejected": 0, "margin_calls": 0,
            "peak_equity": STARTING_CASH, "max_drawdown": 0.0}


def account_snapshot(acc, prices):
    long_value = short_value = 0.0
    positions = {}
    for sym, pos in acc["positions"].items():
        price = prices.get(sym) or pos["avg_price"]
        units = pos["units"]
        value = units * price
        if units > 0:
            long_value += value
        else:
            short_value += -value
        positions[sym] = {
            "side": "LONG" if units > 0 else "SHORT",
            "units": units,
            "avg_price": pos["avg_price"],
            "price": price,
            "value": value,
            "unrealized_pnl": (price - pos["avg_price"]) * units,
        }
    equity = acc["cash"] + long_value - short_value
    gross = long_value + short_value
    room = max(0.0, MAX_GROSS_EXPOSURE * equity - gross)
    return {
        "cash": acc["cash"],
        "equity": equity,
        "positions": positions,
        "long_exposure": long_value,
        "short_exposure": short_value,
        "gross_exposure": gross,
        "max_new_buy": max(0.0, min(acc["cash"] / (1 + FEE_RATE), room)),
        "max_new_short": room,
        "fees_paid": acc["fees_paid"],
        "realized_pnl": acc["realized_pnl"],
    }


def parse_orders(raw):
    """Turn whatever a bot returned into clean orders, plus rejections."""
    orders, rejected = [], []

    def reject(item, reason):
        rejected.append({"order": item, "status": "REJECTED", "reason": reason})

    if raw is None:
        return orders, rejected
    if isinstance(raw, dict):
        if "symbol" in raw or "action" in raw:
            raw = [raw]
        else:
            reject(str(raw)[:120], "Return a list of orders, e.g. "
                   "[{'symbol': 'AAPL', 'action': 'BUY', 'amount': 10}]")
            return orders, rejected
    if not isinstance(raw, (list, tuple)):
        reject(str(raw)[:120], "Return a list of order dicts (or an empty list)")
        return orders, rejected
    for item in raw:
        if not isinstance(item, dict):
            reject(str(item)[:120], "Each order must be a dict")
            continue
        sym = normalize_symbol(item.get("symbol"))
        action = str(item.get("action", "")).strip().upper()
        amount = item.get("amount")
        clean = {"symbol": sym, "action": action, "amount": amount}
        if not sym:
            reject(clean, "Missing symbol")
            continue
        if action not in ACTIONS:
            reject(clean, f"Action must be one of {', '.join(ACTIONS)}")
            continue
        if amount is None or str(amount).strip().upper() == "ALL":
            amount = "ALL"
        else:
            amount = number(amount)
            if amount is None or amount <= 0:
                reject(clean, "Amount must be a positive dollar value or 'ALL'")
                continue
        if action in ("BUY", "SHORT") and amount == "ALL":
            reject(clean, f"{action} needs a dollar amount")
            continue
        clean["amount"] = amount
        orders.append(clean)
    return orders, rejected


# =============================================================================
# Bots
# =============================================================================
class BotSlot:
    def __init__(self, name, module, class_name):
        self.name = name
        self.module = module
        self.class_name = class_name
        self.file = _path(module + ".py")
        self.instance = None
        self.mtime = None
        self.error = None
        self.error_time = None
        self.status = ""
        self.watchlist = []
        self.rejected = {}
        self.last_results = []
        self.last_tick_seconds = 0.0
        self.slow_ticks = 0
        self.storage_dir = os.path.join(BOT_DATA_DIR, name)


# =============================================================================
# The engine
# =============================================================================
class Engine:
    def __init__(self, data_source=None):
        self.data = data_source or YahooData()
        self.state = self._load_state()
        self.slots = [BotSlot(*b) for b in BOTS]
        for slot in self.slots:
            self.state["accounts"].setdefault(slot.name, new_account())
            self.state["ledgers"].setdefault(slot.name, [])
            saved = self.state["bots"].get(slot.name, {})
            slot.watchlist = list(saved.get("watchlist", []))
        self.universe = None
        self.universe_view = MappingProxyType({})
        self.universe_source = "not loaded yet"
        self.universe_day = None
        self.prices = {k: v for k, v in self.state.get("prices", {}).items() if number(v)}
        self.price_times = {k: parse_iso(v) for k, v in self.state.get("price_times", {}).items()}
        self.price_times = {k: v for k, v in self.price_times.items() if v}
        self.intraday = {}
        self.daily = {}
        self.daily_checked = {}
        self.movers = {}
        self.movers_time = 0.0
        self.last_price_fetch = 0.0
        self.last_snapshot = 0.0
        self.last_bot_file_check = 0.0
        self.data_errors = []
        self.last_fetch_utc = None
        self.today = ""               # New York date of the current cycle
        self.price_interval = 0.0     # extra seconds between price refreshes (0 = every cycle)
        self.fetch_ok_streak = 0
        self.gap_recheck = {}
        self.cycles_since_close = 0
        if self.state["history"]:
            last = parse_iso(self.state["history"][-1].get("t"))
            self.last_snapshot = last.timestamp() if last else 0.0
        if BASE_DIR not in sys.path:
            sys.path.insert(0, BASE_DIR)

    # ------------------------------------------------------------ state
    @staticmethod
    def _fresh_state():
        return {"version": 1, "created_utc": iso(utc_now()), "accounts": {},
                "ledgers": {}, "history": [], "bots": {}, "engine": {},
                "prices": {}, "price_times": {}, "summary": {}, "positions": {},
                "daily_summaries": [], "day_open": {}}

    def _load_state(self):
        state = read_json(STATE_FILE)
        if not isinstance(state, dict) or state.get("version") != 1:
            state = self._fresh_state()
        for key, default in self._fresh_state().items():
            state.setdefault(key, default)
        return state

    def _reset(self):
        log("RESET: all accounts back to the starting cash")
        keep = self.state.get("engine", {})
        self.state = self._fresh_state()
        self.state["engine"] = keep
        for slot in self.slots:
            self.state["accounts"][slot.name] = new_account()
            self.state["ledgers"][slot.name] = []
            shutil.rmtree(slot.storage_dir, ignore_errors=True)
            slot.mtime = None            # forces a fresh bot object
            slot.watchlist, slot.rejected, slot.last_results = [], {}, []
            slot.error, slot.slow_ticks = None, 0
        self.last_snapshot = 0.0

    # ------------------------------------------------------------ commands
    def _process_command(self):
        cmd = read_json(COMMAND_FILE)
        if not isinstance(cmd, dict) or not cmd.get("id"):
            return
        try:
            os.remove(COMMAND_FILE)              # each command runs once only
        except OSError:
            pass
        eng = self.state["engine"]
        sent = parse_iso(cmd.get("sent_utc"))
        if cmd["id"] == eng.get("last_command_id") or sent is None or \
                (utc_now() - sent).total_seconds() > 600:
            return                               # already done, or stale
        eng["last_command_id"] = cmd["id"]
        what = cmd.get("command")
        log(f"Command received: {what}")
        if what == "pause":
            eng["paused"] = True
        elif what == "resume":
            eng["paused"] = False
        elif what == "reset":
            self._reset()
        elif what == "reload_bots":
            for slot in self.slots:
                slot.mtime = None

    # ------------------------------------------------------------ bots
    def _reload_changed_bots(self, now):
        if now.timestamp() - self.last_bot_file_check < 30 and all(s.mtime for s in self.slots):
            return
        self.last_bot_file_check = now.timestamp()
        for slot in self.slots:
            if not os.path.exists(slot.file):
                if slot.instance is not None or slot.error is None:
                    slot.instance = None
                    slot.error = f"{os.path.basename(slot.file)} not found in {BASE_DIR}"
                    slot.error_time = iso(now)
                slot.mtime = "missing"
                continue
            mtime = os.path.getmtime(slot.file)
            if mtime == slot.mtime:
                continue
            slot.mtime = mtime
            try:
                name = f"tournament_{slot.module}_{int(mtime * 1000)}"
                spec = importlib.util.spec_from_file_location(name, slot.file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                cls = getattr(module, slot.class_name, None)
                if cls is None:                    # tolerate a differently named class
                    found = [obj for obj in vars(module).values()
                             if isinstance(obj, type) and obj.__module__ == module.__name__
                             and callable(getattr(obj, "market_tick", None))]
                    if len(found) != 1:
                        raise AttributeError(f"{os.path.basename(slot.file)} must contain a "
                                             f"class called {slot.class_name}")
                    cls = found[0]
                    log(f"{slot.name}: using class {cls.__name__} "
                        f"(expected the name {slot.class_name})")
                os.makedirs(slot.storage_dir, exist_ok=True)
                slot.instance = cls()
                slot.error = None
                slot.status = ""
                log(f"Loaded {slot.name} from {os.path.basename(slot.file)}")
                self._update_watchlist(slot)
            except Exception:
                slot.instance = None
                slot.error = "Could not load the bot:\n" + traceback.format_exc(limit=4)[-1500:]
                slot.error_time = iso(now)
                log(f"{slot.name} failed to load: {slot.error.splitlines()[-1]}")

    def _symbol_allowed(self, sym):
        if not SYMBOL_RE.match(sym):
            return False, "not a valid ticker format"
        if self.universe is not None and sym not in self.universe:
            return False, ("not on the US stock lists (unknown ticker, or an "
                           "ETF/warrant/unit/note, which are not allowed)")
        return True, ""

    def _update_watchlist(self, slot):
        raw = getattr(slot.instance, "watchlist", [])
        if isinstance(raw, str):
            raw = [raw]
        accepted, rejected = [], {}
        if not isinstance(raw, (list, tuple, set)):
            rejected["(watchlist)"] = "watchlist must be a list of ticker strings"
            raw = []
        for item in raw:
            sym = normalize_symbol(item)
            if not sym or sym in accepted:
                continue
            ok, reason = self._symbol_allowed(sym)
            if not ok:
                rejected[sym] = reason
            elif len(accepted) >= MAX_WATCHLIST:
                rejected[sym] = f"watchlist limit is {MAX_WATCHLIST} stocks"
            else:
                accepted.append(sym)
        slot.watchlist = accepted
        slot.rejected = dict(list(rejected.items())[:50])

    # ------------------------------------------------------------ data
    def _tracked_symbols(self):
        symbols = set()
        for slot in self.slots:
            symbols.update(slot.watchlist)
            symbols.update(self.state["accounts"][slot.name]["positions"].keys())
        return sorted(symbols)

    def _refresh_universe(self, market):
        if market["new_york_date"] == self.universe_day:
            return
        self.universe_day = market["new_york_date"]
        symbols, source = load_universe()
        self.universe = symbols
        self.universe_view = MappingProxyType(dict(symbols or {}))
        self.universe_source = source
        log(f"Stock list: {len(symbols or {})} symbols - {source}")

    def _fetch_prices(self, symbols, market, now):
        rows, errors = self.data.intraday(symbols)
        self.last_price_fetch = now.timestamp()
        self.last_fetch_utc = iso(now)
        self.data_errors = errors[-5:]
        for sym, bars in rows.items():
            self.intraday[sym] = tuple(bars)
            last = bars[-1]
            self.prices[sym] = last[4]
            self.price_times[sym] = parse_iso(last[0]) or now
        if errors:
            log("Price download problems: " + " | ".join(errors[-3:]))

        # If Yahoo says "too many requests", refresh prices less often for a
        # while (cycles carry on with the last prices), then speed back up.
        throttled = bool(getattr(self.data, "throttled", False)) or \
            (len(symbols) >= 10 and not rows)
        if throttled:
            self.price_interval = min(float(MAX_PRICE_BACKOFF_SECONDS),
                                      max(40.0, self.price_interval * 2))
            self.fetch_ok_streak = 0
            self.data_errors = (self.data_errors + [
                f"Yahoo is limiting requests - prices now refresh every "
                f"{self.price_interval:.0f}s"])[-5:]
            log(f"Yahoo throttling detected: price refresh slowed to {self.price_interval:.0f}s")
        elif self.price_interval:
            self.fetch_ok_streak += 1
            if self.fetch_ok_streak >= 10:
                self.fetch_ok_streak = 0
                self.price_interval = 0.0 if self.price_interval <= 40 else self.price_interval / 2
                log(f"Price refresh speeding back up ({self.price_interval:.0f}s extra delay)")

        # A held stock whose price has jumped far from its own history may
        # have split: download its history (and split records) again.
        stamp = now.timestamp()
        for slot in self.slots:
            for sym in self.state["accounts"][slot.name]["positions"]:
                bars = self.daily.get(sym)
                price = self.prices.get(sym)
                if not bars or not price or not bars[-1][4]:
                    continue
                if (abs(price / bars[-1][4] - 1) > SPLIT_GAP_RECHECK
                        and stamp - self.gap_recheck.get(sym, 0) > 900):
                    self.gap_recheck[sym] = stamp
                    self.daily_checked.pop(sym, None)

    def _ensure_daily(self, symbols, market, now):
        """Daily candles are downloaded once per New York day, and once more
        after the close so they include the session that just finished.
        Bots always receive COMPLETED days only (never a part-finished today)."""
        today = market["new_york_date"]
        finished_today = market["session"] == "after-hours" and market["is_trading_day"]
        key = today + ("+close" if finished_today else "")
        stamp = now.timestamp()
        need = []
        for s in symbols:
            checked = self.daily_checked.get(s)
            if checked is None or (checked[0] != key and (checked[2] or stamp - checked[1] > 1800)):
                need.append(s)
        if not need:
            return
        rows, errors = self.data.daily(need)
        for s in need:
            self.daily_checked[s] = (key, stamp, True) if s in rows else ("", stamp, False)
        for sym, bars in rows.items():
            bars = list(bars)
            while bars and (bars[-1][0] > today or (bars[-1][0] == today and not finished_today)):
                bars.pop()
            if bars:
                self.daily[sym] = tuple(bars)
        if errors:
            log("Daily history problems: " + " | ".join(errors[-3:]))
        self._apply_splits(need, today, now)

    def _apply_splits(self, symbols, today, now):
        """Keep holdings whole across stock splits: when a held stock splits,
        the share count is multiplied and the average price divided, so the
        position's value does not jump."""
        events = getattr(self.data, "splits", None) or {}
        for sym in symbols:
            for date, ratio in events.get(sym, ()):
                if date > today:
                    continue
                for slot in self.slots:
                    pos = self.state["accounts"][slot.name]["positions"].get(sym)
                    if not pos:
                        continue
                    opened = pos.get("opened")
                    done = pos.setdefault("splits", [])
                    if not opened or date <= opened or date in done:
                        continue
                    pos["units"] *= ratio
                    pos["avg_price"] /= ratio
                    done.append(date)
                    log(f"SPLIT {sym} x{ratio:g} on {date}: adjusted {slot.name}'s position")
                    self._ledger(slot.name, now, {
                        "symbol": sym, "action": "SPLIT", "status": "ADJUSTED",
                        "units": pos["units"], "price": self.prices.get(sym),
                        "reason": f"stock split ({ratio:g} new shares per old share) on {date}: "
                                  f"share count adjusted, value unchanged"})

    def _refresh_movers(self, market, now):
        due = now.timestamp() - self.movers_time >= MOVERS_REFRESH_SECONDS
        if due and (market["is_open"] or not self.movers):
            movers, errors = self.data.movers()
            self.movers_time = now.timestamp()
            if movers:
                for lst in movers.values():
                    for q in lst:
                        ok, _ = self._symbol_allowed(q["symbol"])
                        q["tradable"] = ok and (q["price"] or 0) >= MIN_PRICE
                self.movers = movers
            if errors:
                log("Market movers problems: " + " | ".join(errors[-2:]))

    # ------------------------------------------------------------ orders
    def _price_ok(self, sym, market, now):
        price = self.prices.get(sym)
        if not price:
            return None, "no price available yet - keep it on your watchlist and try next tick"
        when = self.price_times.get(sym)
        if market["is_open"] and (when is None or
                                  (now - when).total_seconds() > STALE_PRICE_MINUTES * 60):
            return None, f"price is more than {STALE_PRICE_MINUTES} minutes old (halted or not trading)"
        return price, None

    def _fill(self, acc, sym, action, amount, price, reason=None):
        """Apply a validated order. Returns the result dict."""
        pos = acc["positions"].get(sym)
        units_held = pos["units"] if pos else 0.0
        realized = None
        if action == "BUY":
            units = amount / price
            fee = amount * FEE_RATE
            acc["cash"] -= amount + fee
            new_units = units_held + units
            avg = ((units_held * pos["avg_price"]) if pos else 0.0) + units * price
            entry = dict(pos) if pos else {"opened": self.today}
            entry.update(units=new_units, avg_price=avg / new_units)
            acc["positions"][sym] = entry
            value = amount
        elif action == "SHORT":
            units = amount / price
            fee = amount * FEE_RATE
            acc["cash"] += amount - fee
            held = abs(units_held)
            avg = ((held * pos["avg_price"]) if pos else 0.0) + units * price
            entry = dict(pos) if pos else {"opened": self.today}
            entry.update(units=units_held - units, avg_price=avg / (held + units))
            acc["positions"][sym] = entry
            value = amount
        elif action == "SELL":
            units = min(units_held, units_held if amount == "ALL" else amount / price)
            value = units * price
            fee = value * FEE_RATE
            acc["cash"] += value - fee
            realized = (price - pos["avg_price"]) * units - fee
            self._reduce(acc, sym, units_held - units, price)
        else:  # COVER
            held = abs(units_held)
            units = min(held, held if amount == "ALL" else amount / price)
            value = units * price
            fee = value * FEE_RATE
            acc["cash"] -= value + fee
            realized = (pos["avg_price"] - price) * units - fee
            self._reduce(acc, sym, -(held - units), price)
        acc["fees_paid"] += fee
        acc["orders_filled"] += 1
        if realized is not None:
            acc["realized_pnl"] += realized
            acc["closed_trades"] += 1
            acc["winning_trades"] += 1 if realized > 0 else 0
        return {"symbol": sym, "action": action, "status": "FILLED",
                "reason": reason, "price": price, "units": units,
                "value": value, "fee": fee,
                "realized_pnl": realized}

    @staticmethod
    def _reduce(acc, sym, remaining_units, price):
        if abs(remaining_units * price) < 0.01:
            acc["positions"].pop(sym, None)
        else:
            acc["positions"][sym]["units"] = remaining_units

    def _execute(self, slot, order, market, now, visible):
        acc = self.state["accounts"][slot.name]
        sym, action, amount = order["symbol"], order["action"], order["amount"]
        base = {"symbol": sym, "action": action, "requested": amount}

        def reject(reason, log_it=True):
            acc["orders_rejected"] += 1
            res = dict(base, status="REJECTED", reason=reason)
            if log_it:
                self._ledger(slot.name, now, res)
            return res

        if not market["is_open"]:
            return reject("market is closed", log_it=False)
        if sym not in visible:
            return reject("not on your watchlist and not held - add it to `watchlist` first")
        price, why = self._price_ok(sym, market, now)
        if price is None:
            return reject(why)

        snap = account_snapshot(acc, self.prices)
        pos = acc["positions"].get(sym)
        held = pos["units"] if pos else 0.0

        if action in ("BUY", "SHORT"):
            if amount < MIN_ORDER_VALUE:
                return reject(f"minimum order is ${MIN_ORDER_VALUE:.2f}")
            if price < MIN_PRICE:
                return reject(f"stocks under ${MIN_PRICE:.2f} can't be traded")
            if action == "BUY" and held < 0:
                return reject("you are short this stock - COVER it before buying")
            if action == "SHORT" and held > 0:
                return reject("you own this stock - SELL it before shorting")
            if action == "SHORT" and price < MIN_SHORT_PRICE:
                return reject(f"stocks under ${MIN_SHORT_PRICE:.2f} can't be shorted")
            limit = snap["max_new_buy"] if action == "BUY" else snap["max_new_short"]
            if amount > limit + 1e-9:
                return reject(f"too large - the most you can {action.lower()} right now is ${limit:.2f}")
            if snap["cash"] < amount * FEE_RATE:
                return reject("not enough cash for the fee")
        elif action == "SELL":
            if held <= 0:
                return reject("you don't own this stock")
            if amount != "ALL" and amount < MIN_ORDER_VALUE and amount < held * price:
                return reject(f"minimum order is ${MIN_ORDER_VALUE:.2f} (or use 'ALL')")
        else:  # COVER
            if held >= 0:
                return reject("you are not short this stock")
            if amount != "ALL" and amount < MIN_ORDER_VALUE and amount < abs(held) * price:
                return reject(f"minimum order is ${MIN_ORDER_VALUE:.2f} (or use 'ALL')")
            want = abs(held) * price if amount == "ALL" else min(amount, abs(held) * price)
            affordable = acc["cash"] / (1 + FEE_RATE)
            if want > affordable + 1e-9:
                if affordable < MIN_ORDER_VALUE:
                    return reject("not enough cash to cover")
                amount = affordable

        result = self._fill(acc, sym, action, amount, price)
        result["requested"] = order["amount"]
        self._ledger(slot.name, now, result)
        return result

    def _ledger(self, name, now, result):
        row = {"time_utc": iso(now)}
        row.update({k: result.get(k) for k in ("symbol", "action", "status", "price",
                                                "units", "value", "fee", "realized_pnl",
                                                "reason", "requested")})
        ledger = self.state["ledgers"][name]
        ledger.insert(0, row)
        del ledger[MAX_LEDGER_ROWS:]

    def _margin_check(self, slot, market, now):
        acc = self.state["accounts"][slot.name]
        if not acc["positions"]:
            return
        snap = account_snapshot(acc, self.prices)
        equity = snap["equity"]
        if equity > 0 and snap["gross_exposure"] <= MARGIN_CALL_EXPOSURE * equity:
            return
        acc["margin_calls"] += 1
        log(f"MARGIN CALL for {slot.name}: equity ${equity:.2f}, "
            f"exposure ${snap['gross_exposure']:.2f}")
        ranked = sorted(snap["positions"].items(), key=lambda kv: -abs(kv[1]["value"]))
        for sym, p in ranked:
            snap = account_snapshot(acc, self.prices)
            if snap["equity"] > 0 and snap["gross_exposure"] <= MAX_GROSS_EXPOSURE * snap["equity"]:
                break
            action = "SELL" if p["units"] > 0 else "COVER"
            res = self._fill(acc, sym, action, "ALL", p["price"],
                             reason="MARGIN CALL - closed by the engine")
            self._ledger(slot.name, now, res)
            slot.last_results.append(res)

    # ------------------------------------------------------------ running a bot
    def _build_data(self, slot, acc, visible, market, now):
        snap = account_snapshot(acc, self.prices)
        return {
            "timestamp_utc": iso(now),
            "tick": self.state["engine"].get("tick", 0),
            "market": dict(market),
            "account": snap,
            "prices": {s: self.prices[s] for s in visible if s in self.prices},
            "price_times_utc": {s: iso(self.price_times[s]) for s in visible
                                if s in self.price_times},
            "intraday_bars": {s: list(self.intraday.get(s, ())) for s in visible},
            "daily_bars": {s: list(self.daily.get(s, ())) for s in visible},
            "movers": copy.deepcopy(self.movers),
            "universe": self.universe_view,
            "universe_complete": self.universe is not None,
            "watchlist_status": {
                "accepted": list(slot.watchlist),
                "rejected": dict(slot.rejected),
                "limit": MAX_WATCHLIST,
                "no_price_yet": [s for s in slot.watchlist if s not in self.prices],
            },
            "last_order_results": copy.deepcopy(slot.last_results),
            "rules": dict(RULES),
            "storage_dir": slot.storage_dir,
        }

    def _run_bot(self, slot, market, now):
        if slot.instance is None:
            return
        acc = self.state["accounts"][slot.name]
        visible = set(slot.watchlist) | set(acc["positions"])
        data = self._build_data(slot, acc, visible, market, now)
        started = time.perf_counter()
        try:
            raw = slot.instance.market_tick(data)
        except Exception:
            raw = []
            slot.error = traceback.format_exc(limit=4)[-1500:]
            slot.error_time = iso(now)
            log(f"{slot.name} raised an error: {slot.error.strip().splitlines()[-1]}")
        slot.last_tick_seconds = time.perf_counter() - started
        if slot.last_tick_seconds > SLOW_BOT_SECONDS:
            slot.slow_ticks += 1
        try:
            slot.status = str(getattr(slot.instance, "status", "") or "")[:400]
        except Exception:
            slot.status = ""

        orders, results = parse_orders(raw)
        for r in results:
            acc["orders_rejected"] += 1
            self._ledger(slot.name, now, {"symbol": "", "action": "", "status": "REJECTED",
                                          "reason": r["reason"], "requested": str(r["order"])[:80]})
        if len(orders) > MAX_ORDERS_PER_TICK:
            for o in orders[MAX_ORDERS_PER_TICK:]:
                results.append(dict(o, status="REJECTED",
                                    reason=f"more than {MAX_ORDERS_PER_TICK} orders in one tick"))
            orders = orders[:MAX_ORDERS_PER_TICK]
        for order in orders:
            results.append(self._execute(slot, order, market, now, visible))
        slot.last_results = results
        try:
            self._update_watchlist(slot)
        except Exception:
            slot.error = "Could not read watchlist:\n" + traceback.format_exc(limit=2)[-800:]
            slot.error_time = iso(now)

    # ------------------------------------------------------------ bookkeeping
    def _update_stats(self, market, now):
        summary, positions = {}, {}
        for slot in self.slots:
            acc = self.state["accounts"][slot.name]
            snap = account_snapshot(acc, self.prices)
            eq = snap["equity"]
            if market["is_open"]:
                acc["peak_equity"] = max(acc["peak_equity"], eq)
                if acc["peak_equity"] > 0:
                    acc["max_drawdown"] = max(acc["max_drawdown"],
                                              1 - eq / acc["peak_equity"])
            summary[slot.name] = {
                "equity": eq,
                "return_pct": (eq / STARTING_CASH - 1) * 100,
                "cash": acc["cash"],
                "long_exposure": snap["long_exposure"],
                "short_exposure": snap["short_exposure"],
                "open_positions": len(acc["positions"]),
                "fees_paid": acc["fees_paid"],
                "realized_pnl": acc["realized_pnl"],
                "closed_trades": acc["closed_trades"],
                "win_rate_pct": (100 * acc["winning_trades"] / acc["closed_trades"]
                                 if acc["closed_trades"] else 0.0),
                "orders_filled": acc["orders_filled"],
                "orders_rejected": acc["orders_rejected"],
                "max_drawdown_pct": acc["max_drawdown"] * 100,
                "margin_calls": acc["margin_calls"],
            }
            positions[slot.name] = [dict(p, symbol=s) for s, p in snap["positions"].items()]
        self.state["summary"] = summary
        self.state["positions"] = positions

    def _daily_summary(self, market, now):
        """Shortly after each close, record the day's results for every bot.
        The dashboard shows these under "Daily summaries"."""
        today = market["new_york_date"]
        summary = self.state["summary"]
        if market["is_open"]:
            self.cycles_since_close = 0
            if self.state.get("day_open", {}).get("date") != today:
                self.state["day_open"] = {"date": today,
                                          "equity": {n: s["equity"] for n, s in summary.items()}}
            return
        if not (market["session"] == "after-hours" and market["is_trading_day"]):
            return
        done = self.state.setdefault("daily_summaries", [])
        if done and done[-1]["date"] == today:
            return
        self.cycles_since_close += 1
        if self.cycles_since_close < 3:              # let the closing prices settle
            return
        previous = done[-1]["bots"] if done else {}
        opened = self.state.get("day_open", {})
        opened = opened.get("equity", {}) if opened.get("date") == today else {}
        if not previous:                             # first summary: start from the last
            opens_at = market.get("opens_at_utc") or ""   # chart point before today's open
            earlier = [h for h in self.state["history"] if h.get("t", "") < opens_at]
            if earlier:
                opened = dict(opened, **{k: v for k, v in earlier[-1].items() if k != "t"})
        bots = {}
        for name, s in summary.items():
            start = previous.get(name, {}).get("equity") or opened.get(name) or STARTING_CASH
            rows = [r for r in self.state["ledgers"].get(name, [])
                    if (parse_iso(r.get("time_utc")) and
                        to_new_york(parse_iso(r["time_utc"])).strftime("%Y-%m-%d") == today)]
            fills = [r for r in rows if r.get("status") == "FILLED"]
            orders = {}
            for r in fills:
                orders[r["action"]] = orders.get(r["action"], 0) + 1
            held = sorted(self.state["positions"].get(name, []), key=lambda p: p["unrealized_pnl"])
            def brief(p):
                return {"symbol": p["symbol"], "side": p["side"], "pnl": round(p["unrealized_pnl"], 2)}
            bot = self.state["bots"].get(name, {})
            bots[name] = {
                "equity": round(s["equity"], 2),
                "day_change": round(s["equity"] - start, 2),
                "day_pct": round((s["equity"] / start - 1) * 100, 2) if start else 0.0,
                "total_pct": round(s["return_pct"], 2),
                "orders": orders,
                "traded_value": round(sum(r.get("value") or 0 for r in fills), 2),
                "fees": round(sum(r.get("fee") or 0 for r in fills), 3),
                "realized": round(sum(r.get("realized_pnl") or 0 for r in fills), 2),
                "rejected": sum(1 for r in rows if r.get("status") == "REJECTED"),
                "margin_calls": sum(1 for r in fills if "MARGIN CALL" in str(r.get("reason"))),
                "open_positions": len(held),
                "long": round(s["long_exposure"], 2), "short": round(s["short_exposure"], 2),
                "cash": round(s["cash"], 2),
                "best": brief(held[-1]) if held else None,
                "worst": brief(held[0]) if len(held) > 1 else None,
                "error": bool(bot.get("error")) and str(bot.get("error_time_utc", ""))[:10] >= today,
                "loaded": bool(bot.get("loaded")),
            }
        done.append({"date": today, "made_utc": iso(now), "half_day": market["is_half_day"],
                     "bots": bots})
        del done[:-60]
        log(f"Daily summary recorded for {today}")

    def _snapshot_history(self, market, now):
        gap = EQUITY_SNAPSHOT_SECONDS if market["is_open"] else 3600
        if now.timestamp() - self.last_snapshot < gap:
            return
        self.last_snapshot = now.timestamp()
        point = {"t": iso(now)}
        for name, s in self.state["summary"].items():
            point[name] = round(s["equity"], 4)
        self.state["history"].append(point)
        del self.state["history"][:-MAX_HISTORY_POINTS]

    def _save(self, market, now, symbols):
        eng = self.state["engine"]
        eng.update({
            "heartbeat_utc": iso(now),
            "pid": os.getpid(),
            "market": market,
            "symbols_tracked": len(symbols),
            "last_price_fetch_utc": self.last_fetch_utc,
            "data_errors": self.data_errors,
            "universe_source": self.universe_source,
            "universe_size": len(self.universe or {}),
            "price_refresh_seconds": max(float(POLL_SECONDS_OPEN), self.price_interval),
            "throttled": bool(self.price_interval),
            "movers_updated_utc": iso(datetime.fromtimestamp(self.movers_time, UTC))
            if self.movers_time else None,
        })
        for slot in self.slots:
            self.state["bots"][slot.name] = {
                "loaded": slot.instance is not None,
                "file": os.path.basename(slot.file),
                "error": slot.error,
                "error_time_utc": slot.error_time,
                "status": slot.status,
                "watchlist": slot.watchlist,
                "rejected_watchlist": slot.rejected,
                "last_tick_seconds": round(slot.last_tick_seconds, 3),
                "slow_ticks": slot.slow_ticks,
            }
        keep = set(symbols)
        self.state["prices"] = {s: p for s, p in self.prices.items() if s in keep}
        self.state["price_times"] = {s: iso(t) for s, t in self.price_times.items() if s in keep}
        atomic_write_json(STATE_FILE, self.state)

    # ------------------------------------------------------------ one cycle
    def tick(self, now=None):
        now = now or utc_now()
        self.state["engine"]["tick"] = self.state["engine"].get("tick", 0) + 1
        self._process_command()
        market = market_status(now)
        self.today = market["new_york_date"]
        self._refresh_universe(market)
        self._reload_changed_bots(now)

        symbols = self._tracked_symbols()
        since_fetch = now.timestamp() - self.last_price_fetch
        due = since_fetch >= CLOSED_PRICE_REFRESH_SECONDS
        if symbols and ((market["is_open"] and since_fetch >= self.price_interval)
                        or due or not self.prices):
            self._fetch_prices(symbols, market, now)
        refresh_lock()
        self._ensure_daily(symbols, market, now)
        self._refresh_movers(market, now)
        refresh_lock()

        if market["is_open"]:
            for slot in self.slots:
                self._margin_check(slot, market, now)
        if not self.state["engine"].get("paused"):
            for slot in self.slots:
                self._run_bot(slot, market, now)
        self._update_stats(market, now)
        self._daily_summary(market, now)
        self._snapshot_history(market, now)
        self._save(market, now, self._tracked_symbols())
        return market

    # ------------------------------------------------------------ forever
    def run_forever(self):
        log(f"Engine starting in {BASE_DIR} (pid {os.getpid()})")
        while True:
            started = time.time()
            try:
                market = self.tick()
                wait = POLL_SECONDS_OPEN if market["is_open"] else POLL_SECONDS_CLOSED
            except Exception:
                log("Cycle failed, retrying in 30s:\n" + traceback.format_exc(limit=6))
                wait = 30
            refresh_lock()
            time.sleep(max(1.0, wait - (time.time() - started)))


# =============================================================================
# Single-instance lock (only one engine may run at a time)
# =============================================================================
def lock_age():
    lock = read_json(LOCK_FILE)
    if not isinstance(lock, dict):
        return None
    hb = parse_iso(lock.get("heartbeat_utc"))
    return (utc_now() - hb).total_seconds() if hb else None


def acquire_lock():
    for _ in range(2):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                json.dump({"pid": os.getpid(), "heartbeat_utc": iso(utc_now()),
                           "token": uuid.uuid4().hex}, f)
            return True
        except FileExistsError:
            age = lock_age()
            if age is not None and age < ENGINE_STALE_SECONDS:
                return False
            try:
                os.remove(LOCK_FILE)          # stale lock from a crashed engine
            except FileNotFoundError:
                pass
    return False


def refresh_lock():
    try:
        atomic_write_json(LOCK_FILE, {"pid": os.getpid(), "heartbeat_utc": iso(utc_now())})
    except Exception:
        pass


def release_lock():
    lock = read_json(LOCK_FILE)
    if isinstance(lock, dict) and lock.get("pid") == os.getpid():
        try:
            os.remove(LOCK_FILE)
        except FileNotFoundError:
            pass


def main():
    if not acquire_lock():
        print("Another engine is already running (engine.lock is fresh). Exiting.")
        return
    try:
        engine = Engine()
        if "--once" in sys.argv:
            engine.tick()
            print(json.dumps(engine.state["summary"], indent=2))
        else:
            engine.run_forever()
    except KeyboardInterrupt:
        log("Engine stopped by keyboard")
    finally:
        release_lock()


if __name__ == "__main__":
    main()