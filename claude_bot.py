"""
ClaudeBot - Market-Scanning Trend Portfolio
===========================================

Idea in one paragraph: with a 0.1% fee on every order, trading often is a
reliable way to lose. So this bot trades rarely. Every night it scans the
whole US market using six months of daily prices, ranks every liquid stock by
how strong and how steady its trend is, and once a day it holds a small,
diversified basket of the best ones. How much it invests - and whether it
shorts the weakest stocks - depends on the health of the whole market.
Every position has a trailing stop, so a trend that breaks is cut quickly.

1. SCAN (mostly overnight). The watchlist is rotated through the market, up to
   30 stocks at a time: first a list of well-known liquid stocks, then today's
   market movers, then everything else in the universe. For each stock, from
   daily candles up to yesterday's close:
     * liquidity  - median daily dollar volume over 20 days (>= $25m)
     * trend      - price vs its 50-day and 100-day averages
     * momentum   - ~6-month and ~3-month returns, skipping the latest week
                    (the last week tends to reverse), divided by volatility,
                    so steady climbers rank above wild ones
     * ATR        - the stock's typical daily move, used for its stop
2. MARKET MOOD, from the liquid stocks scanned:
     BULL     - at least half are above their 50-day average and the typical
                large stock is up over 20 days  -> 8 longs, 95% invested
     NEUTRAL  - in between                      -> 5 longs, 60% invested
     BEAR     - under 35% above their 50-day average and falling
                                                -> 2 longs (20%) + 4 shorts (40%)
3. REBALANCE once per trading day (from 10:00am New York, after the opening
   rush). Current holdings are kept while they stay in the top 20, so the
   bot only swaps when something has clearly weakened. Small weight drifts
   are left alone - fees cost more than they'd fix.
4. PROTECT, checked every cycle:
     * longs: sold if they fall 3 x ATR below their best price since bought
     * shorts: covered if they rise 2.5 x ATR above their low, or 12% above
       the entry price
     * a stock that hits a stop can't be re-bought for 3 days
     * if the account falls 8% below its best level, the next rebalance is
       capped at NEUTRAL until it recovers

Everything the bot learns is saved in its storage_dir, so a restart loses
nothing.
"""

import hashlib
import json
import math
import os
import time
from datetime import date
from statistics import median


SEEDS = [
    # Mega and large caps across sectors, plus liquid popular mid caps.
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "TSLA", "ORCL", "CRM",
    "ADBE", "AMD", "INTC", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC", "MRVL",
    "ARM", "SMCI", "DELL", "IBM", "CSCO", "ANET", "NOW", "SNOW", "PLTR", "PANW",
    "CRWD", "ZS", "NET", "DDOG", "MDB", "SHOP", "UBER", "ABNB", "DASH", "COIN",
    "HOOD", "XYZ", "PYPL", "INTU", "ADSK", "WDAY", "TEAM", "TTD", "APP", "RBLX",
    "SNAP", "PINS", "SPOT", "NFLX", "ROKU", "RIVN", "ON", "MPWR", "NXPI", "ADI",
    "MCHP", "TSM", "ASML", "JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW",
    "BLK", "AXP", "V", "MA", "COF", "SOFI", "LLY", "UNH", "JNJ", "PFE",
    "MRK", "ABBV", "AMGN", "GILD", "VRTX", "REGN", "ISRG", "MRNA", "BMY", "CVS",
    "HCA", "DXCM", "WMT", "COST", "HD", "LOW", "TGT", "NKE", "SBUX", "MCD",
    "CMG", "LULU", "KO", "PEP", "PG", "DIS", "XOM", "CVX", "COP", "OXY",
    "SLB", "HAL", "DVN", "EOG", "MPC", "CAT", "DE", "BA", "GE", "HON",
    "LMT", "RTX", "UPS", "FDX", "UNP", "GEV", "VRT", "ETN", "PH", "CCJ",
    "FSLR", "ENPH", "MSTR", "MARA", "RIOT", "CVNA", "AFRM", "UPST", "DKNG", "ONON",
    "DECK", "NU", "BABA", "PDD", "JD", "SE", "MELI", "IONQ", "RGTI", "OKLO",
    "SMR", "HIMS", "RDDT", "ASTS", "NBIS", "ALAB", "CLS", "CRWV", "WBD", "T",
    "VZ", "TMUS", "CMCSA", "CHTR", "F", "GM", "NEE", "DUK", "SO", "AMT",
    "PLD", "EQIX", "SPG", "O", "LIN", "APD", "FCX", "NEM", "DOW", "NUE",
]


class ClaudeBot:
    STATE_VERSION = 1

    def __init__(self):
        self.watchlist = list(SEEDS[:30])
        self.status = "Starting up - scanning the market"

        # Stock filters
        self.MIN_DOLLAR_VOLUME = 25e6
        self.MIN_PRICE = 5.0
        self.SHORT_MIN_DOLLAR_VOLUME = 50e6
        self.SHORT_MIN_PRICE = 10.0
        self.MIN_HISTORY_DAYS = 105
        self.MAX_WEEK_GAIN_TO_BUY = 0.20      # don't chase a stock up 20%+ in a week
        self.MAX_WEEK_DROP_TO_SHORT = 0.20    # don't short one down 20%+ in a week

        # Portfolio by market mood: (number of longs, long exposure,
        #                            number of shorts, short exposure)
        self.PLAN = {"BULL": (8, 0.95, 0, 0.00),
                     "NEUTRAL": (5, 0.60, 0, 0.00),
                     "BEAR": (2, 0.20, 4, 0.40)}
        self.KEEP_RANK = 20                    # holdings kept while in the top 20
        self.REBALANCE_TOLERANCE = 0.30        # ignore weight drifts under 30%
        self.MIN_LIQUID_STOCKS = 120           # needed before trusting the mood
        self.REBALANCE_FROM = 10 * 60          # 10:00 New York
        self.REBALANCE_UNTIL = 15 * 60 + 30    # 15:30 New York

        # Risk
        self.LONG_STOP_ATR = 3.0
        self.SHORT_STOP_ATR = 2.5
        self.MAX_SHORT_LOSS = 0.12
        self.COOLDOWN_DAYS = 3
        self.DRAWDOWN_BRAKE = 0.08
        self.BRAKE_RELEASE = 0.03
        self.MAX_PENDING_TRIES = 6

        self._st = None
        self._file = None
        self._last_save = 0.0
        self._queue = None
        self._queue_day = None
        self._priority_len = 0
        self._queue_pos = 0
        self._batch = {}          # symbol -> cycles waited for its data

    # =====================================================================
    # Engine entry point
    # =====================================================================
    def market_tick(self, data):
        # Each stage is protected separately, so a problem in one can never
        # stop the others - above all, stop-loss orders always go out.
        errors = []
        orders = []
        try:
            if self._st is None:
                self._load(data.get("storage_dir"))
            market = data["market"]
            day = market["new_york_date"]
            hh, mm = market["new_york_time"].split(":")[:2]
            mins = int(hh) * 60 + int(mm)
        except Exception as e:
            self.status = f"Couldn't read the market data ({type(e).__name__}: {e}) - holding"[:300]
            return []
        st = self._st
        if st["day"] != day:
            st["day"] = day
            st["scanned"] = {}
            st["pending"] = {}

        def guarded(name, fn, *args):
            try:
                return fn(*args)
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}: {e}")
                return None

        guarded("scan", self._ingest, data, day)
        if market["is_open"]:
            guarded("equity", self._track_equity, data["account"])
            orders += guarded("stops", self._protect, data, day) or []
            if (st["last_rebalance"] != day
                    and self.REBALANCE_FROM <= mins < self.REBALANCE_UNTIL):
                orders += guarded("rebalance", self._rebalance, data, day,
                                  {o["symbol"] for o in orders}) or []
            busy = {o["symbol"] for o in orders}
            orders += guarded("orders", self._work_pending, data, busy,
                              max(0, 20 - len(orders))) or []
        guarded("watchlist", self._plan_watchlist, data, day, market["is_open"])
        guarded("status", self._set_status, data, day, mins)
        if errors:
            self.status = ("Problem: " + " | ".join(errors) + " || " + (self.status or ""))[:300]
        guarded("save", self._save)
        return orders

    # =====================================================================
    # Scanning
    # =====================================================================
    def _metrics(self, bars, day):
        rows = [b for b in bars if b[0] < day]          # completed days only
        if len(rows) < self.MIN_HISTORY_DAYS:
            return {"ok": False, "d": rows[-1][0] if rows else ""}
        c = [float(r[4]) for r in rows]
        h = [float(r[2]) for r in rows]
        lo = [float(r[3]) for r in rows]
        v = [float(r[5]) for r in rows]
        n = len(c)
        price = c[-1]
        if price <= 0:
            return {"ok": False, "d": rows[-1][0]}
        dollar_volume = median(c[i] * v[i] for i in range(n - 20, n))
        rets = [c[i] / c[i - 1] - 1 for i in range(n - 60, n) if c[i - 1] > 0]
        mean_r = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252)
        long_ret = c[-6] / c[-min(n, 121)] - 1
        mid_ret = c[-6] / c[-64] - 1
        tr = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
              for i in range(n - 14, n)]
        return {
            "ok": True,
            "d": rows[-1][0],
            "p": round(price, 4),
            "dv": round(dollar_volume),
            "s50": round(sum(c[-50:]) / 50, 4),
            "s100": round(sum(c[-100:]) / 100, 4),
            "vol": round(vol, 4),
            "score": round((0.5 * long_ret + 0.5 * mid_ret) / max(vol, 0.15), 4),
            "r5": round(c[-1] / c[-6] - 1, 4),
            "r20": round(c[-1] / c[-21] - 1, 4),
            "atr": round(sum(tr) / len(tr) / price, 5),
        }

    def _ingest(self, data, day):
        st = self._st
        held = set(data["account"]["positions"])
        for sym, bars in data.get("daily_bars", {}).items():
            if not bars or st["scanned"].get(sym) == day:
                continue
            m = self._metrics(bars, day)
            st["scan"][sym] = m
            st["scanned"][sym] = day
            self._batch.pop(sym, None)
        for sym in list(self._batch):
            if st["scanned"].get(sym) == day:
                self._batch.pop(sym, None)
                continue
            self._batch[sym] += 1
            if self._batch[sym] >= 3:                    # no data came - skip today
                st["scanned"][sym] = day
                self._batch.pop(sym, None)
        for sym in data.get("watchlist_status", {}).get("rejected", {}):
            st["scanned"][sym] = day
            self._batch.pop(sym, None)
            st["scan"].pop(sym, None)
        for sym in held:                                 # always know held stocks
            if sym not in st["scan"]:
                st["scanned"].pop(sym, None)

    def _build_queue(self, data, day):
        universe = data.get("universe") or {}
        complete = bool(data.get("universe_complete")) and len(universe) > 0
        movers = []
        for lst in (data.get("movers") or {}).values():
            for q in lst:
                if q.get("tradable") and q.get("symbol"):
                    movers.append(q["symbol"])
        known_liquid = sorted((s for s, m in self._st["scan"].items()
                               if m.get("ok") and m.get("dv", 0) >= self.MIN_DOLLAR_VOLUME),
                              key=lambda s: -self._st["scan"][s]["dv"])
        priority, seen = [], set()
        for s in SEEDS + movers + known_liquid:
            if s not in seen and (not complete or s in universe):
                priority.append(s)
                seen.add(s)
        rest = []
        if complete:
            rest = sorted((s for s in universe if s not in seen),
                          key=lambda s: hashlib.md5((s + day).encode()).hexdigest())
        self._queue = priority + rest
        self._priority_len = len(priority)
        self._queue_pos = 0
        self._queue_day = day

    def _plan_watchlist(self, data, day, is_open):
        st = self._st
        if self._queue is None or self._queue_day != day:
            self._build_queue(data, day)
        held = set(data["account"]["positions"])
        needed = [s for s in st["pending"] if s not in held][:30]
        slots = 30 - len(needed) - len(self._batch)
        # While the market is open, only the priority list is scanned (and only
        # until it's done), to keep the engine's data downloads light.
        limit = self._priority_len if is_open else len(self._queue)
        while slots > 0 and self._queue_pos < limit:
            sym = self._queue[self._queue_pos]
            self._queue_pos += 1
            if st["scanned"].get(sym) == day or sym in held or sym in self._batch:
                continue
            self._batch[sym] = 0
            slots -= 1
        self.watchlist = needed + list(self._batch)

    # =====================================================================
    # Market mood and ranking
    # =====================================================================
    def _fresh_liquid(self):
        scan = self._st["scan"]
        good = {s: m for s, m in scan.items()
                if m.get("ok") and all(k in m for k in ("d", "p", "dv", "s50", "s100", "score", "r5", "r20"))}
        if not good:
            return {}, ""
        latest = max(m["d"] for m in good.values())
        return ({s: m for s, m in good.items()
                 if m["d"] == latest and m["dv"] >= self.MIN_DOLLAR_VOLUME
                 and m["p"] >= self.MIN_PRICE}, latest)

    def _mood(self, liquid):
        if len(liquid) < self.MIN_LIQUID_STOCKS:
            return None, 0.0, 0.0
        breadth = sum(1 for m in liquid.values() if m["p"] > m["s50"]) / len(liquid)
        biggest = sorted(liquid.values(), key=lambda m: -m["dv"])[:150]
        typical_20d = median(m["r20"] for m in biggest)
        if breadth >= 0.50 and typical_20d >= 0:
            mood = "BULL"
        elif breadth < 0.35 and typical_20d < 0:
            mood = "BEAR"
        else:
            mood = "NEUTRAL"
        return mood, breadth, typical_20d

    def _cooling(self, sym, day):
        when = self._st["cooldown"].get(sym)
        if not when:
            return False
        try:
            return (date.fromisoformat(day) - date.fromisoformat(when)).days < self.COOLDOWN_DAYS + 1
        except ValueError:
            return False

    # =====================================================================
    # Daily rebalance
    # =====================================================================
    def _rebalance(self, data, day, already_exiting):
        st = self._st
        liquid, _ = self._fresh_liquid()
        mood, breadth, typical = self._mood(liquid)
        if mood is None:
            return []                                    # not enough data yet
        if st["brake"] and mood == "BULL":
            mood = "NEUTRAL"
        st["mood"], st["breadth"], st["typical_20d"] = mood, breadth, typical

        account = data["account"]
        equity = account["equity"]
        positions = account["positions"]
        n_long, long_exp, n_short, short_exp = self.PLAN[mood]

        longs = sorted(
            (s for s, m in liquid.items()
             if m["p"] > m["s50"] > m["s100"] and m["score"] > 0
             and m["r5"] < self.MAX_WEEK_GAIN_TO_BUY and not self._cooling(s, day)),
            key=lambda s: -liquid[s]["score"])
        shorts = sorted(
            (s for s, m in liquid.items()
             if m["p"] < m["s50"] < m["s100"] and m["score"] < 0
             and m["dv"] >= self.SHORT_MIN_DOLLAR_VOLUME and m["p"] >= self.SHORT_MIN_PRICE
             and m["r5"] > -self.MAX_WEEK_DROP_TO_SHORT and not self._cooling(s, day)),
            key=lambda s: liquid[s]["score"])

        def choose(ranked, held, n):
            rank = {s: i for i, s in enumerate(ranked)}
            keep = sorted((s for s in held if rank.get(s, 10**6) < self.KEEP_RANK),
                          key=lambda s: rank[s])[:n]
            return keep + [s for s in ranked if s not in keep][:n - len(keep)]

        held_long = [s for s, p in positions.items()
                     if p["side"] == "LONG" and s not in already_exiting]
        held_short = [s for s, p in positions.items()
                      if p["side"] == "SHORT" and s not in already_exiting]
        target_long = choose(longs, held_long, n_long) if n_long else []
        target_short = choose(shorts, held_short, n_short) if n_short else []

        orders = []
        for s in held_long:
            if s not in target_long:
                orders.append({"symbol": s, "action": "SELL", "amount": "ALL"})
        for s in held_short:
            if s not in target_short:
                orders.append({"symbol": s, "action": "COVER", "amount": "ALL"})

        st["pending"] = {}
        for targets, side, exposure, n in ((target_long, "LONG", long_exp, n_long),
                                           (target_short, "SHORT", short_exp, n_short)):
            if not n:
                continue
            each = exposure * equity / n
            for s in targets:
                current = abs(positions[s]["value"]) if s in positions else 0.0
                gap = each - current
                if gap > max(1.0, self.REBALANCE_TOLERANCE * each):
                    st["pending"][s] = {"side": side, "target": each, "tries": 0}
                elif -gap > max(1.0, self.REBALANCE_TOLERANCE * each):
                    action = "SELL" if side == "LONG" else "COVER"
                    orders.append({"symbol": s, "action": action, "amount": round(-gap, 2)})
        st["last_rebalance"] = day
        st["targets"] = {"long": target_long, "short": target_short}
        self._last_save = 0.0
        return orders

    def _work_pending(self, data, busy, max_orders):
        st = self._st
        account = data["account"]
        positions = account["positions"]
        prices = data.get("prices", {})
        cash = account["cash"]
        room = account["max_new_short"]                  # gross exposure headroom
        orders = []
        for sym, p in list(st["pending"].items()):
            if sym in busy or len(orders) >= max_orders:
                continue
            pos = positions.get(sym)
            if pos and pos["side"] != p["side"]:
                continue                                 # wait until it's closed
            need = p["target"] - (abs(pos["value"]) if pos else 0.0)
            if need < max(1.0, 0.1 * p["target"]) or p["tries"] >= self.MAX_PENDING_TRIES:
                st["pending"].pop(sym)
                continue
            price = prices.get(sym)
            if not price:
                continue                                 # data arrives next cycle
            if p["side"] == "LONG":
                amount = min(need, cash / 1.001 - 0.01, room)
            else:
                amount = min(need, room)
            if amount < 1.0:
                p["tries"] += 1
                continue
            amount = round(amount, 2)
            orders.append({"symbol": sym,
                           "action": "BUY" if p["side"] == "LONG" else "SHORT",
                           "amount": amount})
            p["tries"] += 1
            room -= amount
            cash += -amount * 1.001 if p["side"] == "LONG" else amount * 0.999
        return orders

    # =====================================================================
    # Protection
    # =====================================================================
    def _track_equity(self, account):
        st = self._st
        equity = account["equity"]
        st["peak"] = max(st["peak"], equity)
        if equity < st["peak"] * (1 - self.DRAWDOWN_BRAKE):
            st["brake"] = True
        elif equity >= st["peak"] * (1 - self.BRAKE_RELEASE):
            st["brake"] = False

    def _protect(self, data, day):
        st = self._st
        orders = []
        positions = data["account"]["positions"]
        for sym in list(st["trail"]):
            if sym not in positions:
                st["trail"].pop(sym)
        for sym, pos in positions.items():
            price = pos["price"]
            trail = st["trail"].get(sym)
            if not trail or trail["side"] != pos["side"]:
                trail = st["trail"][sym] = {"side": pos["side"], "x": price}
            atr = (st["scan"].get(sym) or {}).get("atr") or 0.03
            if pos["side"] == "LONG":
                trail["x"] = max(trail["x"], price)
                if price <= trail["x"] * (1 - self.LONG_STOP_ATR * atr):
                    orders.append({"symbol": sym, "action": "SELL", "amount": "ALL"})
            else:
                trail["x"] = min(trail["x"], price)
                if (price >= trail["x"] * (1 + self.SHORT_STOP_ATR * atr)
                        or price >= pos["avg_price"] * (1 + self.MAX_SHORT_LOSS)):
                    orders.append({"symbol": sym, "action": "COVER", "amount": "ALL"})
        for o in orders:
            st["cooldown"][o["symbol"]] = day
            st["pending"].pop(o["symbol"], None)
        return orders

    # =====================================================================
    # Status, saving and loading
    # =====================================================================
    def _set_status(self, data, day, mins):
        st = self._st
        liquid, _ = self._fresh_liquid()
        today = sum(1 for d in st["scanned"].values() if d == day)
        pos = data["account"]["positions"]
        n_long = sum(1 for p in pos.values() if p["side"] == "LONG")
        n_short = len(pos) - n_long
        mood = st.get("mood") or "not decided yet"
        if st["last_rebalance"] == day:
            plan = "rebalanced today"
        elif data["market"]["is_open"] and mins >= self.REBALANCE_FROM:
            plan = (f"waiting for data ({len(liquid)}/{self.MIN_LIQUID_STOCKS} liquid stocks)"
                    if len(liquid) < self.MIN_LIQUID_STOCKS else "rebalancing")
        else:
            plan = "next rebalance 10:00 New York"
        brake = " | drawdown brake ON" if st["brake"] else ""
        self.status = (f"Mood {mood} (breadth {st.get('breadth', 0):.0%}) | {n_long} long, "
                       f"{n_short} short | {len(liquid)} liquid stocks ranked, {today} scanned "
                       f"today | {plan}{brake}")[:300]

    def _fresh_state(self):
        return {"version": self.STATE_VERSION, "day": "", "scan": {}, "scanned": {},
                "pending": {}, "trail": {}, "cooldown": {}, "last_rebalance": "",
                "peak": 100.0, "brake": False, "mood": None, "breadth": 0.0,
                "typical_20d": 0.0, "targets": {}}

    def _load(self, storage_dir):
        self._st = self._fresh_state()
        if not storage_dir:
            return
        self._file = os.path.join(storage_dir, "claude_state.json")
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("version") == self.STATE_VERSION:
                self._st.update(saved)
        except Exception:
            pass

    def _save(self):
        if not self._file or time.time() - self._last_save < 60:
            return
        self._last_save = time.time()
        try:
            os.makedirs(os.path.dirname(self._file), exist_ok=True)
            tmp = self._file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._st, f)
            os.replace(tmp, self._file)
        except Exception:
            pass
