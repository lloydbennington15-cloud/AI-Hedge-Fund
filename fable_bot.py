"""
FableBot - Overnight-Harvest Trend Portfolio
============================================

The idea in plain English
-------------------------
With a 0.1% fee on every order, the bots that trade least usually keep the
most. So this bot holds a small basket of strong stocks and trades rarely.
Where it differs from a plain momentum bot is in WHAT it looks for and WHEN
it trades:

1. WHAT it buys. Each stock is scored on four things that research has
   repeatedly linked to further gains, all measured from daily prices:
     * 6-month and 3-month momentum, divided by volatility (steady climbers
       beat wild ones) and skipping the latest week (which tends to reverse);
     * closeness to its 6-month high (stocks near their highs tend to keep
       going);
     * smoothness - gains made on many small up-days, not one or two jumps;
     * a fresh "good news" day: a big jump on at least double the normal
       volume that has held since (the classic after-earnings drift).
   Stocks that just had a "bad news" day, or that broke below their 50-day
   average, are excluded. Very similar stocks (correlation above 0.75) are
   not held together, so the basket isn't one bet made six times.

2. WHEN it trades. Studies of US stocks find that momentum stocks earn
   their gains overnight (close to next open), not during the day. So:
     * it BUYS in the last 40 minutes of the session, using today's price
       action as a final check (and catching stocks breaking out today);
     * it SELLS what it no longer wants in the morning, after the opening
       rush, so it has captured the overnight move first.
   Day one is the exception: it invests as soon as it has enough data.

3. HOW MUCH. The health of the whole market sets the plan:
     RISK-ON  - 6 stocks, 96% invested
     NEUTRAL  - 5 stocks, 70% invested
     RISK-OFF - 3 strong stocks (27%) + 3 shorts of weak stocks (27%)
   Calmer stocks get bigger positions (each carries similar risk), within
   limits. A change of mood must show up on two mornings running before the
   bot acts on it, so one odd day doesn't cause needless trading.

4. PROTECTION, every cycle: a trailing stop 2.8 daily ranges below each
   stock's best price; an immediate exit on a bad-news-sized drop; shorts
   are covered on a squeeze; a 5-day ban on re-buying anything stopped out;
   and if the account falls 10% from its peak it invests less until it
   recovers.

It scans the market in the background (30 stocks at a time) and remembers
which stocks are too small to bother with. Everything is saved in its own
storage folder, so restarts lose nothing.
"""

import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from statistics import median


SEEDS = [
    # Technology and semiconductors
    "NVDA", "AAPL", "MSFT", "AVGO", "AMD", "TSM", "ASML", "ORCL", "CRM", "ADBE",
    "INTC", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC", "MRVL", "ARM", "SMCI",
    "DELL", "HPQ", "HPE", "IBM", "CSCO", "ANET", "NOW", "SNOW", "PLTR", "PANW",
    "CRWD", "ZS", "NET", "DDOG", "MDB", "FTNT", "OKTA", "TEAM", "WDAY", "INTU",
    "ADSK", "CDNS", "SNPS", "ANSS", "ON", "MPWR", "NXPI", "ADI", "MCHP", "STX",
    "WDC", "NTAP", "PSTG", "GLW", "APH", "TEL", "KEYS", "TER", "ENTG", "COHR",
    "LITE", "CIEN", "CRDO", "ALAB", "CLS", "FLEX", "JBL", "VRT", "NBIS", "CRWV",
    "APP", "TTD", "HUBS", "TWLO", "DOCU", "ZM", "GTLB", "S", "PATH", "U",
    # Internet, media, consumer platforms
    "AMZN", "META", "GOOGL", "NFLX", "TSLA", "SHOP", "UBER", "LYFT", "ABNB", "BKNG",
    "DASH", "EBAY", "ETSY", "PINS", "SNAP", "RDDT", "SPOT", "ROKU", "RBLX", "EA",
    "TTWO", "DIS", "WBD", "CMCSA", "CHTR", "T", "VZ", "TMUS", "DUOL", "CVNA",
    "CHWY", "W", "PTON", "DKNG", "EXPE", "MAR", "HLT", "RCL", "CCL", "NCLH",
    # Financials and payments
    "JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW", "BLK", "BX", "KKR",
    "APO", "AXP", "V", "MA", "COF", "PYPL", "XYZ", "COIN", "HOOD", "SOFI",
    "AFRM", "UPST", "NU", "IBKR", "CME", "ICE", "SPGI", "MCO", "MSCI", "PGR",
    "ALL", "TRV", "CB", "AIG", "MET", "PRU", "USB", "PNC", "TFC", "FITB",
    # Healthcare
    "LLY", "UNH", "JNJ", "PFE", "MRK", "ABBV", "AMGN", "GILD", "VRTX", "REGN",
    "ISRG", "MRNA", "BMY", "CVS", "CI", "ELV", "HUM", "HCA", "DXCM", "TMO",
    "DHR", "ABT", "MDT", "SYK", "BSX", "EW", "ZTS", "IDXX", "ALNY", "BIIB",
    "ILMN", "NVO", "AZN", "HIMS", "NTRA", "INSM", "EXEL", "PODD", "TEM", "VEEV",
    # Consumer and retail
    "WMT", "COST", "HD", "LOW", "TGT", "NKE", "SBUX", "MCD", "CMG", "LULU",
    "KO", "PEP", "PG", "CL", "MDLZ", "PM", "MO", "EL", "ULTA", "ROST",
    "TJX", "DG", "DLTR", "BBY", "ORLY", "AZO", "YUM", "DPZ", "WING", "CAVA",
    "ONON", "DECK", "CROX", "ANF", "AEO", "GAP", "KR", "SFM", "CELH", "MNST",
    # Industrials, energy, materials, utilities, real estate
    "CAT", "DE", "BA", "GE", "GEV", "HON", "LMT", "RTX", "NOC", "GD",
    "UPS", "FDX", "UNP", "CSX", "ETN", "PH", "EMR", "ITW", "MMM", "CMI",
    "PWR", "URI", "TT", "CARR", "JCI", "AXON", "TDG", "HWM", "RKLB", "ASTS",
    "XOM", "CVX", "COP", "OXY", "SLB", "HAL", "DVN", "EOG", "MPC", "VLO",
    "PSX", "KMI", "WMB", "LNG", "FANG", "CCJ", "OKLO", "SMR", "VST", "CEG",
    "NRG", "NEE", "DUK", "SO", "AEP", "FSLR", "ENPH", "LIN", "APD", "SHW",
    "FCX", "NEM", "NUE", "STLD", "CLF", "AA", "MP", "ALB", "DOW", "DD",
    "AMT", "PLD", "EQIX", "DLR", "SPG", "O", "WELL", "F", "GM", "RIVN",
    # Popular high-activity names
    "MSTR", "MARA", "RIOT", "CLSK", "IONQ", "RGTI", "QBTS", "SOUN", "BBAI", "AI",
    "LCID", "NIO", "XPEV", "LI", "BABA", "PDD", "JD", "BIDU", "SE", "MELI",
    "GRAB", "CPNG", "TOST", "CART", "GME", "AMC", "JOBY", "ACHR", "LUNR", "OPEN",
]


def _clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _stdev(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


class FableBot:
    STATE_VERSION = 2

    def __init__(self):
        self.watchlist = list(dict.fromkeys(SEEDS))[:30]
        self.status = "Starting up - scanning the market"

        # ---- what counts as tradable for this bot
        self.MIN_DV = 20e6                 # median daily dollar volume
        self.MIN_PRICE = 5.0
        self.MAX_DAILY_VOL = 0.055         # skip stocks wilder than 5.5% a day
        self.SHORT_MIN_DV = 60e6
        self.SHORT_MIN_PRICE = 10.0
        self.MIN_HISTORY = 70              # daily candles needed
        self.MIN_UNIVERSE = 120            # liquid stocks needed to judge the market

        # ---- portfolio plan: (longs, long exposure, shorts, short exposure)
        self.PLAN = {"RISK_ON": (6, 0.96, 0, 0.00),
                     "NEUTRAL": (5, 0.70, 0, 0.00),
                     "RISK_OFF": (3, 0.27, 3, 0.27)}
        self.MAX_WEIGHT = 0.24
        self.KEEP_RANK = 25                # holdings kept while ranked this high
        self.MAX_CORR = 0.75
        self.CAT_BONUS = 0.5               # score bonus for a fresh good-news day
        self.ADD_BELOW = 0.65              # top up a holding under 65% of target
        self.TRIM_ABOVE = 1.50             # trim a holding over 150% of target

        # ---- timing (minutes)
        self.ENTRY_FROM = 40               # buy window: 40 to 8 minutes before the close
        self.ENTRY_UNTIL = 8
        self.REVIEW_AFTER_OPEN = 20        # morning review from 20 min after the open
        self.STOP_GRACE = 10               # ignore the first 10 minutes for stops
        self.MAX_ENTRY_TRIES = 6

        # ---- protection
        self.STOP_ATR = 2.8
        self.SHORT_STOP_ATR = 2.3
        self.SHORT_MAX_LOSS = 0.10
        self.COOLDOWN_DAYS = 5
        self.BRAKE_ON = 0.10
        self.BRAKE_OFF = 0.05

        # ---- internals
        self._S = None
        self._dir = None
        self._scan = {}            # sym -> metrics as of the last completed day
        self._rets = {}            # sym -> recent daily returns (memory only)
        self._illiq = {}           # sym -> day found too small / no data
        self._version = 0
        self._rank_cache = (None, None)
        self._batch = {}
        self._queue = []
        self._queue_key = None
        self._qpos = 0
        self._skip_today = set()
        self._last_save = 0.0
        self._last_scan_save = 0.0

    # =====================================================================
    # Engine entry point
    # =====================================================================
    def market_tick(self, data):
        orders, problems = [], []
        try:
            ctx = self._context(data)
            if self._S is None:
                self._load(data.get("storage_dir"))
            self._roll_day(ctx)
        except Exception as e:
            self.status = f"Couldn't read the market data ({type(e).__name__}: {e}) - holding"[:300]
            return []

        def guarded(name, fn, *args):
            try:
                return fn(*args)
            except Exception as e:
                problems.append(f"{name}: {type(e).__name__}: {e}")
                return None

        guarded("scan", self._ingest, data, ctx)
        if ctx["open"]:
            guarded("equity", self._track_equity, data, ctx)
            if ctx["from_open"] >= self.STOP_GRACE:
                orders += guarded("stops", self._stops, data, ctx) or []
            busy = {o["symbol"] for o in orders}
            orders += guarded("review", self._review, data, ctx, busy) or []
            busy = {o["symbol"] for o in orders}
            orders += guarded("entries", self._entries, data, ctx, busy) or []
        guarded("watchlist", self._plan_watchlist, data, ctx)
        guarded("status", self._set_status, data, ctx)
        if problems:
            self.status = ("Problem: " + " | ".join(problems) + " || " + (self.status or ""))[:300]
        guarded("save", self._save)
        return orders[:20]

    # =====================================================================
    # Clock
    # =====================================================================
    @staticmethod
    def _parse(ts):
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def _context(self, data):
        market = data["market"]
        now = self._parse(data["timestamp_utc"])
        to_close, from_open = 10 ** 6, -1
        if market.get("is_open"):
            try:
                to_close = (self._parse(market["closes_at_utc"]) - now).total_seconds() / 60.0
                from_open = (now - self._parse(market["opens_at_utc"])).total_seconds() / 60.0
            except Exception:
                hh, mm = market["new_york_time"].split(":")[:2]
                mins = int(hh) * 60 + int(mm)
                from_open = mins - 570
                to_close = (780 if market.get("is_half_day") else 960) - mins
        session_len = max(60.0, to_close + from_open) if market.get("is_open") else 390.0
        return {"day": market["new_york_date"], "open": bool(market.get("is_open")),
                "to_close": to_close, "from_open": from_open, "session_len": session_len,
                "session": market.get("session", "")}

    def _roll_day(self, ctx):
        S = self._S
        if S["day"] != ctx["day"]:
            S["day"] = ctx["day"]
            self._skip_today = set()
            self._batch = {}
            self._queue_key = None
            if S["entry"] and S["entry"].get("day") != ctx["day"]:
                S["entry"] = None

    # =====================================================================
    # Scanning
    # =====================================================================
    def _metrics(self, bars, day):
        rows = [b for b in bars if b[0] < day]          # completed days only
        n = len(rows)
        if n < self.MIN_HISTORY:
            return None
        c = [float(r[4]) for r in rows]
        h = [float(r[2]) for r in rows]
        lo = [float(r[3]) for r in rows]
        v = [float(r[5]) for r in rows]
        p = c[-1]
        if p <= 0 or min(c[-70:]) <= 0:
            return None
        rets = [c[i] / c[i - 1] - 1 for i in range(1, n)]
        vd = max(0.008, 0.5 * _stdev(rets[-20:]) + 0.5 * _stdev(rets[-60:]))
        base_vol = max(0.008, _stdev(rets[-70:-10]))      # volatility before any recent jump
        look = min(n - 1, 126)
        s100 = sum(c[-100:]) / len(c[-100:])
        recent = rets[-60:]
        ups = sum(1 for r in recent if r > 0)
        downs = sum(1 for r in recent if r < 0)
        tr = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
              for i in range(n - 14, n)]

        cat = None
        for i in range(n - 1, n - 11, -1):               # last 10 completed days
            r = c[i] / c[i - 1] - 1
            base = median(v[i - 20:i]) or 1.0
            thr = max(0.04, 2.5 * base_vol)
            if abs(r) < thr or v[i] < 2.0 * base:
                continue
            mid = (c[i] + c[i - 1]) / 2.0
            rng = h[i] - lo[i]
            if r > 0 and r <= 0.30 and (rng <= 0 or (c[i] - lo[i]) >= 0.5 * rng):
                held = all(c[j] >= mid for j in range(i + 1, n))
                cat = {"k": 1, "ago": n - 1 - i, "held": held}
            elif r < 0:
                held = all(c[j] <= mid for j in range(i + 1, n))
                cat = {"k": -1, "ago": n - 1 - i, "held": held}
            if cat:
                break

        return {
            "d": rows[-1][0], "p": round(p, 4),
            "dv": round(median(c[i] * v[i] for i in range(n - 20, n))),
            "medv": round(median(v[-20:])),
            "vd": round(vd, 5),
            "m6": round(c[-6] / c[-1 - look] - 1, 4),
            "m3": round(c[-6] / c[-64] - 1, 4),
            "s20": round(sum(c[-20:]) / 20, 4),
            "s50": round(sum(c[-50:]) / 50, 4),
            "s50p": round(sum(c[-60:-10]) / 50, 4),
            "s100": round(s100, 4),
            "hi": round(max(h[-126:]), 4),
            "up": round((ups - downs) / max(1, len(recent)), 4),
            "r5": round(c[-1] / c[-6] - 1, 4),
            "r20": round(c[-1] / c[-21] - 1, 4),
            "atr": round(sum(tr) / len(tr) / p, 5),
            "cat": cat,
            "_rets": rets[-60:],
        }

    def _ingest(self, data, ctx):
        day = ctx["day"]
        held = data["account"]["positions"]
        for sym, bars in (data.get("daily_bars") or {}).items():
            if not bars:
                continue
            m_old = self._scan.get(sym)
            if sym in self._skip_today or (m_old and m_old.get("sd") == day and sym in self._rets):
                self._batch.pop(sym, None)
                continue
            try:
                m = self._metrics(bars, day)
            except Exception:
                m = None                                 # unusable data for this stock
            self._batch.pop(sym, None)
            if m is None or ((m["dv"] < 3e6 or m["p"] < 2.0) and sym not in held):
                self._illiq[sym] = day
                self._skip_today.add(sym)
                self._scan.pop(sym, None)
                continue
            self._rets[sym] = m.pop("_rets")
            m["sd"] = day
            self._scan[sym] = m
            self._version += 1
        for sym in list(self._batch):
            self._batch[sym] += 1
            if self._batch[sym] >= 3:                    # no data arrived: skip it today
                self._skip_today.add(sym)
                self._batch.pop(sym)
        for sym in (data.get("watchlist_status") or {}).get("rejected", {}):
            self._illiq[sym] = day
            self._skip_today.add(sym)
            self._batch.pop(sym, None)

    def _days_between(self, a, b):
        try:
            return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days
        except Exception:
            return 10 ** 6

    def _build_queue(self, data, day):
        universe = data.get("universe") or {}
        complete = bool(data.get("universe_complete")) and len(universe) > 0
        movers = [q["symbol"] for lst in (data.get("movers") or {}).values() for q in lst
                  if q.get("tradable") and q.get("symbol")]
        liquid = sorted(self._scan, key=lambda s: -self._scan[s].get("dv", 0))
        first, seen = [], set()
        for s in list(SEEDS) + liquid + movers:
            if s not in seen and (not complete or s in universe):
                first.append(s)
                seen.add(s)
        rest = []
        if complete:
            rest = sorted((s for s in universe
                           if s not in seen
                           and self._days_between(self._illiq.get(s, "2000-01-01"), day) > 10),
                          key=lambda s: hashlib.md5(s.encode()).hexdigest())
        self._queue = first + rest
        self._qpos = 0
        self._queue_key = (day, complete)

    def _scanned_today(self, sym, day):
        m = self._scan.get(sym)
        return (m is not None and m.get("sd") == day) or sym in self._skip_today

    # =====================================================================
    # Ranking and market mood
    # =====================================================================
    def _rank(self, day):
        key = (day, self._version)
        if self._rank_cache[0] == key:
            return self._rank_cache[1]
        fresh = [m["d"] for m in self._scan.values()]
        result = {"n": 0, "longs": [], "shorts": [], "base": {}, "breadth": 0.0, "med20": 0.0,
                  "latest": ""}
        if fresh:
            latest = result["latest"] = max(fresh)
            U = {s: m for s, m in self._scan.items()
                 if m["d"] == latest and m["dv"] >= self.MIN_DV and m["p"] >= self.MIN_PRICE}
            result["n"] = len(U)
            if len(U) >= 30:
                def zmap(fn):
                    vals = {s: fn(m) for s, m in U.items()}
                    mean = sum(vals.values()) / len(vals)
                    sd = _stdev(list(vals.values())) or 1.0
                    return {s: _clip((x - mean) / sd, -3.0, 3.0) for s, x in vals.items()}
                za = zmap(lambda m: m["m6"] / (m["vd"] * 11.2))
                zb = zmap(lambda m: m["m3"] / (m["vd"] * 7.9))
                zc = zmap(lambda m: m["p"] / m["hi"] if m["hi"] > 0 else 0.0)
                ze = zmap(lambda m: m["up"])
                base = {s: 0.35 * za[s] + 0.25 * zb[s] + 0.25 * zc[s] + 0.15 * ze[s] for s in U}
                longs, shorts = [], []
                for s, m in U.items():
                    cat = m.get("cat")
                    week_sd = m["vd"] * 2.236
                    trend_up = m["p"] > m["s50"] and (m["s50"] > m["s100"] or m["s50"] > m["s50p"])
                    bad_news = bool(cat and cat["k"] == -1)
                    if (trend_up and m["m6"] > 0 and m["vd"] <= self.MAX_DAILY_VOL and not bad_news):
                        score = base[s]
                        if cat and cat["k"] == 1 and cat["ago"] <= 8 and cat["held"]:
                            score += self.CAT_BONUS
                        elif m["r5"] > 2.5 * week_sd:
                            score -= 0.4                 # stretched with no news behind it
                        longs.append((score, s))
                    if (m["p"] < m["s50"] < m["s100"] and m["m6"] < 0
                            and m["dv"] >= self.SHORT_MIN_DV and m["p"] >= self.SHORT_MIN_PRICE
                            and m["vd"] <= 0.05 and m["r5"] > -2.0 * week_sd
                            and not (cat and cat["k"] == 1)):
                        score = -(0.4 * za[s] + 0.3 * zb[s] + 0.3 * zc[s])
                        if cat and cat["k"] == -1 and cat["ago"] <= 8 and cat["held"]:
                            score += self.CAT_BONUS
                        shorts.append((score, s))
                longs.sort(reverse=True)
                shorts.sort(reverse=True)
                top = sorted(U.values(), key=lambda m: -m["dv"])[:150]
                result.update(longs=longs, shorts=shorts, base=base,
                              breadth=sum(1 for m in U.values() if m["p"] > m["s50"]) / len(U),
                              med20=median(m["r20"] for m in top))
        self._rank_cache = (key, result)
        return result

    def _mood(self, R):
        prev = self._S.get("regime")
        b, m20 = R["breadth"], R["med20"]
        on = (b >= 0.46 and m20 >= -0.015) if prev == "RISK_ON" else (b >= 0.52 and m20 >= 0.0)
        off = (b < 0.42) if prev == "RISK_OFF" else (b < 0.35 and m20 < -0.02)
        return "RISK_ON" if on else ("RISK_OFF" if off else "NEUTRAL")

    def _plan(self):
        regime = self._S.get("regime") or "NEUTRAL"
        if self._S.get("brake") and regime == "RISK_ON":
            regime = "NEUTRAL"
        return self.PLAN[regime]

    def _cooling(self, sym, day):
        when = self._S["cool"].get(sym)
        return bool(when) and self._days_between(when, day) < self.COOLDOWN_DAYS + 2

    def _corr(self, a, b):
        x, y = self._rets.get(a), self._rets.get(b)
        if not x or not y:
            return 0.0
        n = min(len(x), len(y))
        if n < 30:
            return 0.0
        x, y = x[-n:], y[-n:]
        mx, my = sum(x) / n, sum(y) / n
        sxx = sum((u - mx) ** 2 for u in x)
        syy = sum((u - my) ** 2 for u in y)
        if sxx <= 0 or syy <= 0:
            return 0.0
        return sum((x[i] - mx) * (y[i] - my) for i in range(n)) / math.sqrt(sxx * syy)

    # =====================================================================
    # Protection
    # =====================================================================
    def _track_equity(self, data, ctx):
        S = self._S
        equity = data["account"]["equity"]
        S["peak"] = max(S["peak"], equity)
        if equity < S["peak"] * (1 - self.BRAKE_ON):
            S["brake"] = True
        elif equity >= S["peak"] * (1 - self.BRAKE_OFF):
            S["brake"] = False
        if ctx["day"] not in S["open_days"]:
            S["open_days"] = (S["open_days"] + [ctx["day"]])[-12:]

    def _stops(self, data, ctx):
        S = self._S
        positions = data["account"]["positions"]
        orders = []
        for sym in list(S["trail"]):
            if sym not in positions:
                S["trail"].pop(sym)
        for sym, pos in positions.items():
            price = pos["price"]
            t = S["trail"].get(sym)
            if not t or t["side"] != pos["side"]:
                t = S["trail"][sym] = {"side": pos["side"], "x": price}
            m = self._scan.get(sym) or {}
            atr = _clip(m.get("atr") or 0.03, 0.012, 0.06)
            day_move = price / m["p"] - 1 if (m.get("p") and m.get("sd") == ctx["day"]) else 0.0
            shock = max(0.06, 3.2 * (m.get("vd") or 0.02))
            if pos["side"] == "LONG":
                t["x"] = max(t["x"], price)
                if price <= t["x"] * (1 - self.STOP_ATR * atr) or day_move <= -shock:
                    orders.append({"symbol": sym, "action": "SELL", "amount": "ALL"})
            else:
                t["x"] = min(t["x"], price)
                if (price >= t["x"] * (1 + self.SHORT_STOP_ATR * atr)
                        or price >= pos["avg_price"] * (1 + self.SHORT_MAX_LOSS)
                        or day_move >= shock):
                    orders.append({"symbol": sym, "action": "COVER", "amount": "ALL"})
        for o in orders:
            S["cool"][o["symbol"]] = ctx["day"]
            if S["entry"]:
                S["entry"]["targets"].pop(o["symbol"], None)
        return orders

    # =====================================================================
    # Morning review: decide the market mood and sell what no longer belongs
    # =====================================================================
    def _review(self, data, ctx, busy):
        S = self._S
        day = ctx["day"]
        if S["last_review"] == day or ctx["from_open"] < self.REVIEW_AFTER_OPEN:
            return []
        R = self._rank(day)
        if R["n"] < self.MIN_UNIVERSE:
            return []
        reading = self._mood(R)
        if S.get("regime") is None:
            S["regime"] = reading                        # first ever reading
        elif reading == S["regime"]:
            S["pending"] = None
        elif S.get("pending") == reading:                # second morning running
            S["regime"], S["pending"], S["regime_changed"] = reading, None, day
        else:
            S["pending"] = reading
        S["breadth"], S["med20"] = R["breadth"], R["med20"]
        S["last_review"] = day
        n_long, _, n_short, _ = self._plan()

        positions = data["account"]["positions"]
        long_rank = {s: i for i, (_, s) in enumerate(R["longs"])}
        short_rank = {s: i for i, (_, s) in enumerate(R["shorts"])}
        bar = (R["longs"][self.KEEP_RANK - 1][0] - 0.25) if len(R["longs"]) >= self.KEEP_RANK else -9.0
        orders, keep_long = [], []
        for sym, pos in positions.items():
            if sym in busy:
                continue
            m = self._scan.get(sym)
            cat = (m or {}).get("cat")
            if pos["side"] == "LONG":
                leave = False
                if m:
                    weak = (m["d"] == R["latest"] and sym in R["base"]
                            and sym not in long_rank and R["base"][sym] < bar) or \
                           (long_rank.get(sym, 0) >= self.KEEP_RANK and R["base"].get(sym, 9.0) < bar)
                    leave = (m["p"] < 0.985 * m["s50"]
                             or bool(cat and cat["k"] == -1 and cat["ago"] <= 2)
                             or weak)
                if leave or n_long == 0:
                    orders.append({"symbol": sym, "action": "SELL", "amount": "ALL"})
                else:
                    keep_long.append(sym)
            else:
                leave = n_short == 0
                if m and not leave:
                    leave = (m["p"] > m["s50"]
                             or bool(cat and cat["k"] == 1 and cat["ago"] <= 2)
                             or (m["d"] == R["latest"] and short_rank.get(sym, 10 ** 6) >= 20))
                if leave:
                    orders.append({"symbol": sym, "action": "COVER", "amount": "ALL"})
        if len(keep_long) > n_long:                       # plan shrank: drop the weakest
            keep_long.sort(key=lambda s: long_rank.get(s, 10 ** 6))
            for sym in keep_long[n_long:]:
                orders.append({"symbol": sym, "action": "SELL", "amount": "ALL"})
        return orders

    # =====================================================================
    # Entries: build today's targets, then work the orders
    # =====================================================================
    def _today_catalyst(self, sym, m, price, data, ctx):
        """A good-news breakout happening today: big gain, heavy volume,
        closing near the day's high."""
        move = price / m["p"] - 1
        if move < max(0.04, 2.5 * m["vd"]) or move > 0.25:
            return False
        bars = [b for b in (data.get("intraday_bars") or {}).get(sym, ())
                if str(b[0])[:10] == ctx["day"]]
        if len(bars) < 30:
            return False
        volume = sum(float(b[5]) for b in bars)
        hi = max(float(b[2]) for b in bars)
        lo = min(float(b[3]) for b in bars)
        elapsed = _clip(ctx["from_open"] / ctx["session_len"], 0.3, 1.0)
        heavy = m["medv"] > 0 and volume >= 2.0 * m["medv"] * elapsed
        near_high = hi <= lo or (price - lo) >= 0.6 * (hi - lo)
        return heavy and near_high

    def _select(self, data, ctx, busy, in_window):
        S = self._S
        day = ctx["day"]
        R = self._rank(day)
        if R["n"] < self.MIN_UNIVERSE:
            return None
        account = data["account"]
        equity = account["equity"]
        positions = account["positions"]
        prices = data.get("prices") or {}
        n_long, long_exp, n_short, short_exp = self._plan()

        # Wait until the leading candidates have prices (they join the
        # watchlist one cycle before their prices arrive).
        leaders = [s for _, s in R["longs"][:12] if s not in positions]
        priced = sum(1 for s in leaders if s in prices)
        if leaders and priced < min(len(leaders), 8) and ctx["to_close"] > self.ENTRY_UNTIL + 4:
            S["wait"] = S.get("wait", 0) + 1
            if S["wait"] <= 4:
                return None
        S["wait"] = 0

        def pick(ranked, side, n, held):
            cands = []
            for score, sym in ranked:
                if sym in positions or sym in busy or sym not in prices or self._cooling(sym, day):
                    continue
                m = self._scan[sym]
                move = prices[sym] / m["p"] - 1
                jolt = max(0.04, 2.5 * m["vd"])
                if side == "LONG":
                    if move <= -jolt:
                        continue                          # breaking down today
                    hot = in_window and self._today_catalyst(sym, m, prices[sym], data, ctx)
                    if move >= max(0.07, 3.5 * m["vd"]) and not hot:
                        continue                          # unexplained spike: don't chase
                    live = score + (self.CAT_BONUS if hot else 0.0) + 0.08 * _clip(move / m["vd"], -2, 2)
                else:
                    if move >= jolt or prices[sym] < self.SHORT_MIN_PRICE:
                        continue                          # squeezing today
                    live = score - 0.08 * _clip(move / m["vd"], -2, 2)
                cands.append((live, sym))
            cands.sort(reverse=True)
            chosen = []
            for limit in (self.MAX_CORR, 0.90):           # second pass relaxes the similarity rule
                for _, sym in cands:
                    if len(held) + len(chosen) >= n:
                        break
                    if sym in chosen:
                        continue
                    if any(self._corr(sym, other) > limit for other in held + chosen):
                        continue
                    chosen.append(sym)
            return chosen

        held_long = [s for s, p in positions.items() if p["side"] == "LONG" and s not in busy]
        held_short = [s for s, p in positions.items() if p["side"] == "SHORT" and s not in busy]
        new_long = pick(R["longs"], "LONG", n_long, held_long) if n_long else []
        new_short = pick(R["shorts"][:15], "SHORT", n_short, held_short) if n_short else []

        targets, trims = {}, []
        trim_mult = 1.15 if S.get("regime_changed") == day else self.TRIM_ABOVE
        for names, side, n, exposure in ((held_long + new_long, "LONG", n_long, long_exp),
                                         (held_short + new_short, "SHORT", n_short, short_exp)):
            if not names or not n:
                continue
            inv = {s: 1.0 / max((self._scan.get(s) or {}).get("vd") or 0.02, 0.012) for s in names}
            total = sum(inv.values())
            exposure *= min(1.0, len(names) / n)
            equal = exposure / len(names)
            weights = {s: _clip(exposure * inv[s] / total, 0.6 * equal, self.MAX_WEIGHT)
                       for s in names}
            scale = min(1.0, exposure / sum(weights.values()))
            for s in names:
                amount = weights[s] * scale * equity
                current = abs(positions[s]["value"]) if s in positions else 0.0
                if current < self.ADD_BELOW * amount and amount - current >= 1.0:
                    targets[s] = {"side": side, "amt": round(amount, 2)}
                elif s in positions and current > trim_mult * amount and current - amount >= 1.0:
                    trims.append({"symbol": s, "action": "SELL" if side == "LONG" else "COVER",
                                  "amount": round(current - amount, 2)})
        return {"day": day, "targets": targets, "trims": trims, "tries": 0,
                "mode": "close" if in_window else "catch-up"}

    def _entries(self, data, ctx, busy):
        S = self._S
        day = ctx["day"]
        if S["last_review"] != day:
            return []                                     # the review sets the plan first
        in_window = self.ENTRY_UNTIL <= ctx["to_close"] <= self.ENTRY_FROM
        before = [d for d in S["open_days"] if d < day]
        previous_day = before[-1] if before else None
        catch_up = (S["last_entry_day"] not in (day, previous_day)
                    and ctx["from_open"] >= 30 and ctx["to_close"] > self.ENTRY_FROM)
        E = S["entry"]
        if E and E.get("day") == day and E.get("mode") == "catch-up" and in_window and E.get("done"):
            E = None                                      # day one: also use the close window
        if not E or E.get("day") != day:
            if not (in_window or catch_up):
                return []
            E = self._select(data, ctx, busy, in_window)
            if E is None:
                return []
            S["entry"] = E
            S["last_entry_day"] = day
        if E.get("done"):
            return []

        account = data["account"]
        positions = account["positions"]
        prices = data.get("prices") or {}
        orders = [t for t in E.pop("trims", []) if t["symbol"] not in busy]
        cash = account["cash"] + sum(0.999 * t["amount"] for t in orders if t["action"] == "SELL")
        room = account["max_new_short"] + sum(t["amount"] for t in orders)
        open_targets = 0
        for sym, t in list(E["targets"].items()):
            if sym in busy:
                continue
            pos = positions.get(sym)
            if pos and pos["side"] != t["side"]:
                E["targets"].pop(sym)
                continue
            need = t["amt"] - (abs(pos["value"]) if pos else 0.0)
            if need < max(1.0, 0.12 * t["amt"]):
                E["targets"].pop(sym)
                continue
            open_targets += 1
            if sym not in prices:
                continue
            amount = min(need, room, cash / 1.001 - 0.02) if t["side"] == "LONG" else min(need, room)
            if amount < 1.0:
                continue
            amount = math.floor(amount * 100) / 100.0
            orders.append({"symbol": sym, "action": "BUY" if t["side"] == "LONG" else "SHORT",
                           "amount": amount})
            room -= amount
            cash += -amount * 1.001 if t["side"] == "LONG" else amount * 0.999
        E["tries"] += 1
        if open_targets == 0 or E["tries"] >= self.MAX_ENTRY_TRIES:
            E["done"] = True
        return orders

    # =====================================================================
    # Watchlist
    # =====================================================================
    def _plan_watchlist(self, data, ctx):
        S = self._S
        day = ctx["day"]
        universe = data.get("universe") or {}
        complete = bool(data.get("universe_complete")) and len(universe) > 0
        if self._queue_key != (day, complete):
            self._build_queue(data, day)
        held = set(data["account"]["positions"])
        must = []

        def want(sym):
            if sym and sym not in held and sym not in must and len(must) < 30:
                must.append(sym)

        if S["entry"] and not S["entry"].get("done"):
            for sym in S["entry"]["targets"]:
                want(sym)
        active = ctx["open"] or ctx["session"] == "pre-market"
        if active:
            R = self._rank(day)
            for _, sym in R["longs"][:20]:
                if not self._cooling(sym, day):
                    want(sym)
            if (S.get("regime") == "RISK_OFF"):
                for _, sym in R["shorts"][:6]:
                    want(sym)
            if ctx["open"] and ctx["from_open"] >= 0.6 * ctx["session_len"]:
                extra = 0
                for key in ("day_gainers", "most_actives"):
                    for q in (data.get("movers") or {}).get(key, []):
                        if extra >= 6:
                            break
                        sym = q.get("symbol")
                        if (q.get("tradable") and (q.get("price") or 0) >= self.MIN_PRICE
                                and 4.0 <= (q.get("change_pct") or 0) <= 25.0
                                and sym not in self._skip_today and sym not in must
                                and sym not in held):
                            want(sym)
                            extra += 1

        for sym in list(self._batch):                     # drop finished scans
            if self._scanned_today(sym, day):
                self._batch.pop(sym)
        free = 30 - len(must) - len([s for s in self._batch if s not in must])
        new_limit = min(free, 10) if ctx["open"] else free
        while new_limit > 0 and self._qpos < len(self._queue):
            sym = self._queue[self._qpos]
            self._qpos += 1
            if (self._scanned_today(sym, day) or sym in held or sym in self._batch or sym in must):
                continue
            self._batch[sym] = 0
            new_limit -= 1
        self.watchlist = (must + [s for s in self._batch if s not in must])[:30]

    # =====================================================================
    # Status, saving and loading
    # =====================================================================
    def _set_status(self, data, ctx):
        S = self._S
        R = self._rank(ctx["day"])
        positions = data["account"]["positions"]
        n_long = sum(1 for p in positions.values() if p["side"] == "LONG")
        n_short = len(positions) - n_long
        mood = (S.get("regime") or "not judged yet").replace("_", "-")
        E = S["entry"]
        if R["n"] < self.MIN_UNIVERSE:
            doing = f"scanning ({R['n']}/{self.MIN_UNIVERSE} liquid stocks ranked so far)"
        elif not ctx["open"]:
            doing = "market closed - scanning"
        elif E and E.get("day") == ctx["day"] and not E.get("done"):
            doing = "placing today's orders"
        elif E and E.get("day") == ctx["day"] and E.get("mode") == "close":
            doing = "today's buying is done"
        elif ctx["to_close"] > self.ENTRY_FROM:
            doing = (f"holding - buys happen in the last {self.ENTRY_FROM} min "
                     f"(in {max(0, ctx['to_close'] - self.ENTRY_FROM):.0f} min); "
                     "momentum stocks earn their gains overnight")
        else:
            doing = "holding"
        brake = " | drawdown brake ON" if S.get("brake") else ""
        self.status = (f"Mood {mood} (breadth {S.get('breadth', 0):.0%}) | {n_long} long, "
                       f"{n_short} short | {R['n']} liquid stocks ranked | {doing}{brake}")[:300]

    def _fresh_state(self):
        return {"version": self.STATE_VERSION, "day": "", "trail": {}, "cool": {},
                "regime": None, "pending": None, "breadth": 0.0, "med20": 0.0,
                "regime_changed": "",
                "last_review": "", "last_entry_day": "", "entry": None, "open_days": [],
                "peak": 100.0, "brake": False, "wait": 0}

    def _load(self, storage_dir):
        self._S = self._fresh_state()
        if not storage_dir:
            return
        self._dir = storage_dir
        try:
            with open(os.path.join(storage_dir, "fable_state.json"), "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("version") == self.STATE_VERSION:
                self._S.update(saved)
        except Exception:
            pass
        try:
            with open(os.path.join(storage_dir, "fable_scan.json"), "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("version") == self.STATE_VERSION:
                self._scan = saved.get("scan", {})
                self._illiq = saved.get("illiq", {})
                self._version += 1
        except Exception:
            pass

    def _write(self, name, payload):
        os.makedirs(self._dir, exist_ok=True)
        path = os.path.join(self._dir, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)

    def _save(self):
        if not self._dir:
            return
        now = time.time()
        if now - self._last_save >= 45:
            self._last_save = now
            self._write("fable_state.json", self._S)
        if now - self._last_scan_save >= 300:
            self._last_scan_save = now
            keep = {s: m for s, m in self._scan.items() if m.get("dv", 0) >= 8e6}
            self._write("fable_scan.json", {"version": self.STATE_VERSION,
                                            "scan": keep, "illiq": self._illiq})
