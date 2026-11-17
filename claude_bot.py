"""
ClaudeBot (Opus) - version 2: researched, diversified trend portfolio
=====================================================================

What changed at the halfway point, and why
------------------------------------------
Version 1 lost about 4% in its first week, and its trade log shows why:
  * Its ranking favoured the steepest six-month climbers, and in a full
    market scan those turned out to be small biotech companies. It ended up
    holding five of them at once - effectively one bet, on stocks that jump
    on drug-trial and regulator news that no price chart can foresee.
  * When its scan widened from 180 well-known stocks to the whole market,
    it replaced its entire portfolio after one day.
  * In a "neutral" market it kept 40% in cash, so even its good picks
    couldn't make up much ground.

Version 2 keeps the core idea (strong, steady trends, low trading costs) and
fixes those weaknesses:
  1. STEADIER STOCKS ONLY: at least $50m traded a day, a price of $10+, no
     single-day move over 15% in the last three months, and volatility
     under 65% a year. This rules out "binary event" stocks.
  2. DIVERSIFIED: no two holdings whose daily moves are more than 65%
     alike (measured over 60 days), so a sector shock can't hit everything.
  3. BUY STRENGTH ON A DIP: among strong six-month trends, it prefers stocks
     that pulled back over the last week - a well-documented short-term
     effect that also avoids buying right after a spike.
  4. READS THE NEWS (allowed from the halfway point): a quick, time-limited
     lookup of each candidate's and holding's next results date and recent
     headlines on Yahoo Finance. It won't buy within 5 trading days of a
     results announcement, sells a holding before its results, skips stocks
     with alarming headlines (investigations, lawsuits, downgrades, failed
     trials...) or an unusual burst of news, and leaves a holding if two or
     more such headlines appear.
  5. MORE INVESTED, FEWER SWAPS: 7 stocks at 85% in a neutral market (8 at
     97% in a strong one); holdings stay while still in the top 60 and at
     least two days old.
  6. DECIDES ONCE A DAY, LATE: 35 minutes before the close, when the day's
     news is known. Stop-losses still run all session.

In a falling market ("bear": under 35% of stocks in uptrends and the big
stocks down over a month) it holds 3 strong stocks and shorts 4 weak ones.

News lookups run in a separate, short-lived process with a hard 4-second
limit, so a slow reply from Yahoo can never hold up the engine or the other
bots. If Yahoo doesn't answer, the bot carries on without news.
"""

import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15"}

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


# A small, separate Python process does each batch of news / earnings lookups.
# It has a hard time limit and is always finished or stopped before
# market_tick returns, so a slow Yahoo reply can never hold up the engine.
_LOOKUP_CHILD = r"""
import json, sys
import yfinance as yf
for sym in sys.argv[1:]:
    out = {"symbol": sym, "earn": [], "news": [], "ok": True}
    try:
        t = yf.Ticker(sym)
        try:
            cal = t.calendar
            dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
            out["earn"] = [str(d)[:10] for d in (dates or [])]
        except Exception as e:
            out["ok"] = False
            out["error"] = "calendar: " + str(e)[:100]
        try:
            items = t.get_news(count=20) if hasattr(t, "get_news") else t.news
            for a in items or []:
                c = a.get("content") if isinstance(a.get("content"), dict) else a
                when = c.get("pubDate") or c.get("displayTime") or a.get("providerPublishTime")
                out["news"].append([str(when), str(c.get("title") or "")[:200]])
        except Exception as e:
            out["error"] = (out.get("error", "") + " news: " + str(e)[:100]).strip()
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)[:120]
    print(json.dumps(out), flush=True)
"""

RED_FLAGS = (
    "investigat", "probe", "subpoena", "lawsuit", "sues ", "sued", "class action", "fraud",
    "accounting", "restate", "sec charges", "recall", "downgrade", "cut to sell", "cuts guidance",
    "lowers guidance", "lowered guidance", "slashes", "warns", "profit warning", "misses",
    "plunge", "plummet", "tumble", "sinks", "halted", "bankrupt", "chapter 11", "delist",
    "default", "resign", "ousted", "oust", "fired", "steps down", "short seller", "short report",
    "secondary offering", "stock offering", "share offering", "dilution", "fda reject",
    "complete response letter", "clinical hold", "trial fail", "failed trial", "data breach",
    "cyberattack", "layoffs",
)


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return 0.0
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _stdev(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _when(text):
    """Seconds since 1970 from an ISO date string or a number, else None."""
    if text in (None, "", "None"):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


class ClaudeBot:
    STATE_VERSION = 2

    def __init__(self):
        self.watchlist = list(SEEDS[:30])
        self.status = "Starting up - scanning the market"

        # ---- which stocks qualify
        self.MIN_DV = 50e6                  # median dollars traded per day (20 days)
        self.MIN_PRICE = 10.0
        self.MAX_VOL = 0.65                 # annualised volatility (60 days)
        self.MAX_JUMP = 0.15                # no single-day move over 15% in 3 months
        self.MIN_HISTORY = 120              # daily candles needed
        self.SHORT_MIN_DV = 80e6
        self.SHORT_MIN_PRICE = 15.0
        self.MIN_LIQUID = 150               # stocks needed before judging the market
        self.DIP_WEIGHT = 0.35              # preference for a recent pullback in an uptrend
        self.MAX_CORR = 0.65                # max similarity between two holdings
        self.TODAY_DROP_LIMIT = -0.04       # don't buy a stock falling 4%+ today
        self.TODAY_RISE_LIMIT = 0.06        # or chase one up 6%+ today

        # ---- portfolio plan by market mood: (longs, long share, shorts, short share)
        self.PLAN = {"BULL": (8, 0.97, 0, 0.0),
                     "NEUTRAL": (7, 0.85, 0, 0.0),
                     "BEAR": (3, 0.30, 4, 0.45)}
        self.KEEP_RANK = 60                 # a holding stays while ranked in the top 60
        self.MIN_HOLD_DAYS = 2
        self.ADD_BELOW = 0.70               # top up a holding under 70% of its target
        self.TRIM_ABOVE = 1.50              # trim one over 150% of its target

        # ---- timing (minutes before the close / after the open)
        self.DECIDE_FROM = 35               # the daily decision: 35 to 12 minutes
        self.DECIDE_UNTIL = 12              # before the close
        self.NEWS_CHECK_AFTER_OPEN = 30     # morning check of holdings' news
        self.STOP_GRACE = 5                 # ignore the first 5 minutes for stops

        # ---- protection
        self.LONG_STOP_ATR = 3.0
        self.SHORT_STOP_ATR = 2.5
        self.SHORT_MAX_LOSS = 0.10
        self.COOLDOWN_DAYS = 3
        self.BRAKE_ON = 0.10
        self.BRAKE_OFF = 0.05
        self.EARNINGS_BUY_DAYS = 5          # don't buy if results are due within 5 days
        self.NEWS_HOURS = 48
        self.NEWS_BURST = 12                # 12+ headlines in 48h = unusual attention

        # ---- research (news and earnings dates from Yahoo)
        self.RESEARCH = True
        self.RESEARCH_SECONDS = 4.0         # hard limit per batch
        self.RESEARCH_BATCH = 6
        self.RESEARCH_EVERY_OPEN = 3        # while open: one batch every 3 cycles
        self.NEWS_MAX_AGE_HELD = 2 * 3600
        self.NEWS_MAX_AGE = 6 * 3600
        self.EARN_MAX_AGE = 20 * 3600

        self._S = None
        self._dir = None
        self._scan = {}
        self._rets = {}
        self._version = 0
        self._rank_cache = (None, None)
        self._queue, self._queue_day, self._qpos, self._priority_len = [], None, 0, 0
        self._batch = {}
        self._skip_today = set()
        self._ticks = 0
        self._last_save = 0.0
        self._last_scan_save = 0.0
        self._research_fail = 0
        self._research_pause_until = 0.0

    # =====================================================================
    # Engine entry point
    # =====================================================================
    def market_tick(self, data):
        problems, orders = [], []
        try:
            ctx = self._context(data)
            if self._S is None:
                self._load(data.get("storage_dir"))
            self._roll_day(ctx)
        except Exception as e:
            self.status = f"Couldn't read the market data ({type(e).__name__}: {e}) - holding"[:300]
            return []
        self._ticks += 1

        def guarded(name, fn, *args):
            try:
                return fn(*args)
            except Exception as e:
                problems.append(f"{name}: {type(e).__name__}: {e}")
                return None

        guarded("scan", self._ingest, data, ctx)
        deciding = False
        if ctx["open"]:
            guarded("equity", self._track_equity, data)
            if ctx["from_open"] >= self.STOP_GRACE:
                orders += guarded("stops", self._stops, data, ctx) or []
            busy = {o["symbol"] for o in orders}
            if ctx["from_open"] >= self.NEWS_CHECK_AFTER_OPEN:
                orders += guarded("news check", self._news_check, data, ctx, busy) or []
            busy = {o["symbol"] for o in orders}
            deciding = (self._S["last_decision"] != ctx["day"]
                        and self.DECIDE_UNTIL <= ctx["to_close"] <= self.DECIDE_FROM)
            if deciding:
                orders += guarded("decision", self._decide, data, ctx, busy) or []
            busy = {o["symbol"] for o in orders}
            orders += guarded("orders", self._work_pending, data, ctx, busy,
                              max(0, 20 - len(orders))) or []
        if not deciding:
            guarded("research", self._research, data, ctx)
        guarded("watchlist", self._plan_watchlist, data, ctx)
        guarded("status", self._set_status, data, ctx)
        if problems:
            self.status = ("Problem: " + " | ".join(problems) + " || " + (self.status or ""))[:300]
        guarded("save", self._save)
        return orders[:20]

    # =====================================================================
    # Clock
    # =====================================================================
    def _context(self, data):
        market = data["market"]
        now = _when(data["timestamp_utc"]) or time.time()
        to_close, from_open = 10 ** 6, -1.0
        if market.get("is_open"):
            opens = _when(market.get("opens_at_utc"))
            closes = _when(market.get("closes_at_utc"))
            if opens and closes:
                to_close = (closes - now) / 60.0
                from_open = (now - opens) / 60.0
            else:
                hh, mm = market["new_york_time"].split(":")[:2]
                mins = int(hh) * 60 + int(mm)
                from_open = mins - 570
                to_close = (780 if market.get("is_half_day") else 960) - mins
        return {"now": now, "day": market["new_york_date"], "open": bool(market.get("is_open")),
                "session": market.get("session", ""), "to_close": to_close,
                "from_open": from_open}

    def _roll_day(self, ctx):
        S = self._S
        if S["day"] != ctx["day"]:
            S["day"] = ctx["day"]
            S["pending"] = {}
            self._skip_today = set()
            self._batch = {}
            self._queue_day = None
            if ctx["open"] or ctx["session"] == "pre-market":
                pass

    def _trading_days_until(self, day, target):
        """Trading days from `day` to `target` (0 = same day)."""
        try:
            d0 = date.fromisoformat(day)
            d1 = date.fromisoformat(target)
        except ValueError:
            return 10 ** 6
        if d1 < d0:
            return -1
        n, d = 0, d0
        while d < d1:
            d += timedelta(days=1)
            if d.weekday() < 5 and d.isoformat() not in HOLIDAYS:
                n += 1
        return n

    # =====================================================================
    # Scanning (six months of daily prices, completed days only)
    # =====================================================================
    def _metrics(self, bars, day):
        rows = [b for b in bars if str(b[0]) < day]
        n = len(rows)
        if n < self.MIN_HISTORY:
            return None
        c = [float(r[4]) for r in rows]
        h = [float(r[2]) for r in rows]
        lo = [float(r[3]) for r in rows]
        v = [float(r[5]) for r in rows]
        p = c[-1]
        if p <= 0 or min(c[-120:]) <= 0:
            return None
        rets = [c[i] / c[i - 1] - 1 for i in range(1, n)]
        vol = _stdev(rets[-60:]) * math.sqrt(252)
        tr = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
              for i in range(n - 14, n)]
        look = min(n - 1, 126)
        return {
            "d": rows[-1][0], "p": round(p, 4),
            "dv": round(_median([c[i] * v[i] for i in range(n - 20, n)])),
            "vol": round(vol, 4),
            "jump": round(max(abs(r) for r in rets[-63:]), 4),
            "s50": round(sum(c[-50:]) / 50, 4),
            "s100": round(sum(c[-100:]) / 100, 4),
            "m6": round(c[-6] / c[-1 - look] - 1, 4),
            "m3": round(c[-6] / c[-64] - 1, 4),
            "r5": round(c[-1] / c[-6] - 1, 4),
            "r20": round(c[-1] / c[-21] - 1, 4),
            "atr": round(sum(tr) / len(tr) / p, 5),
            "_rets": [round(r, 5) for r in rets[-60:]],
        }

    def _ingest(self, data, ctx):
        day = ctx["day"]
        held = set(data["account"]["positions"])
        for sym, bars in (data.get("daily_bars") or {}).items():
            old = self._scan.get(sym)
            if not bars or sym in self._skip_today or (old and old.get("sd") == day
                                                       and (sym in self._rets or old["dv"] < self.MIN_DV)):
                self._batch.pop(sym, None)
                continue
            try:
                m = self._metrics(bars, day)
            except Exception:
                m = None
            self._batch.pop(sym, None)
            if m is None or (m["dv"] < 5e6 and sym not in held):
                self._skip_today.add(sym)
                self._scan.pop(sym, None)
                continue
            rets = m.pop("_rets")
            if m["dv"] >= self.MIN_DV or sym in held:
                self._rets[sym] = rets
            m["sd"] = day
            self._scan[sym] = m
            self._version += 1
        for sym in list(self._batch):
            self._batch[sym] += 1
            if self._batch[sym] >= 3:
                self._skip_today.add(sym)
                self._batch.pop(sym)
        for sym in (data.get("watchlist_status") or {}).get("rejected", {}):
            self._skip_today.add(sym)
            self._batch.pop(sym, None)

    def _build_queue(self, data, day):
        universe = data.get("universe") or {}
        complete = bool(data.get("universe_complete")) and len(universe) > 0
        movers = [q["symbol"] for lst in (data.get("movers") or {}).values() for q in lst
                  if q.get("tradable") and q.get("symbol")]
        known = sorted((s for s, m in self._scan.items() if m.get("dv", 0) >= self.MIN_DV),
                       key=lambda s: -self._scan[s]["dv"])
        first, seen = [], set()
        for s in SEEDS + known + movers:
            if s not in seen and (not complete or s in universe):
                first.append(s)
                seen.add(s)
        rest = []
        if complete:
            rest = sorted((s for s in universe if s not in seen),
                          key=lambda s: hashlib.md5((s + day).encode()).hexdigest())
        self._queue, self._priority_len, self._qpos, self._queue_day = first + rest, len(first), 0, day

    def _scanned_today(self, sym, day):
        m = self._scan.get(sym)
        return (m is not None and m.get("sd") == day) or sym in self._skip_today

    # =====================================================================
    # Ranking and market mood
    # =====================================================================
    def _rank(self, day):
        key = (day, self._version, len(self._S["info"]))
        if self._rank_cache[0] == key:
            return self._rank_cache[1]
        R = {"n": 0, "longs": [], "shorts": [], "breadth": 0.0, "med20": 0.0, "latest": ""}
        good = [m for m in self._scan.values() if "jump" in m]
        if good:
            latest = R["latest"] = max(m["d"] for m in good)
            U = {s: m for s, m in self._scan.items()
                 if m.get("d") == latest and "jump" in m
                 and m["dv"] >= self.MIN_DV and m["p"] >= self.MIN_PRICE}
            R["n"] = len(U)
            if len(U) >= 30:
                mom = {s: (0.5 * m["m6"] + 0.5 * m["m3"]) / max(m["vol"], 0.15) for s, m in U.items()}
                rev = {s: m["r5"] / max(m["vol"] / math.sqrt(52), 0.01) for s, m in U.items()}

                def z(vals):
                    mean = sum(vals.values()) / len(vals)
                    sd = _stdev(list(vals.values())) or 1.0
                    return {k: _clip((x - mean) / sd, -3, 3) for k, x in vals.items()}
                zm, zr = z(mom), z(rev)
                longs, shorts = [], []
                for s, m in U.items():
                    calm = m["vol"] <= self.MAX_VOL and m["jump"] <= self.MAX_JUMP
                    if calm and m["p"] > m["s50"] > m["s100"] and m["m6"] > 0 and zm[s] > 0:
                        longs.append((zm[s] - self.DIP_WEIGHT * zr[s], s))
                    if (calm and m["p"] < m["s50"] < m["s100"] and m["m6"] < 0 and zm[s] < 0
                            and m["dv"] >= self.SHORT_MIN_DV and m["p"] >= self.SHORT_MIN_PRICE
                            and m["r5"] > -0.10):
                        shorts.append((-zm[s] + self.DIP_WEIGHT * zr[s], s))
                longs.sort(reverse=True)
                shorts.sort(reverse=True)
                top = sorted(U.values(), key=lambda m: -m["dv"])[:150]
                R.update(longs=longs, shorts=shorts,
                         breadth=sum(1 for m in U.values() if m["p"] > m["s50"]) / len(U),
                         med20=_median([m["r20"] for m in top]))
        self._rank_cache = (key, R)
        return R

    def _mood(self, R):
        if R["breadth"] >= 0.50 and R["med20"] >= 0:
            return "BULL"
        if R["breadth"] < 0.35 and R["med20"] < 0:
            return "BEAR"
        return "NEUTRAL"

    def _cooling(self, sym, day):
        when = self._S["cool"].get(sym)
        return bool(when) and self._trading_days_until(when, day) <= self.COOLDOWN_DAYS

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
    # Research: earnings dates and headlines
    # =====================================================================
    def _info(self, sym):
        return self._S["info"].get(sym)

    def _earnings_within(self, sym, day, days):
        info = self._info(sym)
        if not info:
            return False
        for d in info.get("earn", []):
            n = self._trading_days_until(day, d)
            if 0 <= n <= days:
                return True
        return False

    def _news_flags(self, sym, now, hours=None):
        """(headlines in the window, red-flag headlines, a sample red flag)."""
        info = self._info(sym)
        if not info:
            return 0, 0, ""
        hours = hours or self.NEWS_HOURS
        count = flags = 0
        sample = ""
        for when, title in info.get("news", []):
            t = _when(when)
            if t is None or now - t > hours * 3600:
                continue
            count += 1
            low = " " + title.lower() + " "
            if any(k in low for k in RED_FLAGS):
                flags += 1
                sample = sample or title
        return count, flags, sample

    def _research_targets(self, data, ctx):
        S, now = self._S, ctx["now"]
        held = list(data["account"]["positions"])
        R = self._rank(ctx["day"])
        candidates = [s for _, s in R["longs"][:30]]
        if (S.get("mood") or "") == "BEAR" or R["breadth"] < 0.4:
            candidates += [s for _, s in R["shorts"][:12]]
        want = []
        for sym in held + candidates:
            info = S["info"].get(sym) or {}
            age = now - info.get("t", 0)
            limit = self.NEWS_MAX_AGE_HELD if sym in held else self.NEWS_MAX_AGE
            if info.get("eday") != ctx["day"] and now - info.get("et", 0) > self.EARN_MAX_AGE:
                limit = 0
            if age > limit and sym not in want:
                want.append(sym)
        return want

    def _research(self, data, ctx):
        if not self.RESEARCH or time.time() < self._research_pause_until:
            return
        if ctx["open"] and self._ticks % self.RESEARCH_EVERY_OPEN:
            return
        want = self._research_targets(data, ctx)[:self.RESEARCH_BATCH]
        if not want:
            return
        results = self._lookup(want)
        now = ctx["now"]
        got = 0
        for r in results:
            sym = r.get("symbol")
            if sym not in want:
                continue
            got += 1 if r.get("ok") else 0
            news = []
            for when, title in r.get("news", [])[:20]:
                t = _when(when)
                if t is not None and now - t <= 72 * 3600:
                    news.append([t, title])
            info = self._S["info"].setdefault(sym, {})
            info.update(t=now, news=news, ok=bool(r.get("ok")))
            if r.get("ok"):
                info.update(earn=r.get("earn", []), et=now, eday=ctx["day"])
        if got == 0:
            self._research_fail += 1
            if self._research_fail >= 3:
                self._research_pause_until = time.time() + 1800
                self._research_fail = 0
        else:
            self._research_fail = 0
        cutoff = now - 4 * 86400                        # forget stale entries
        for sym in [s for s, i in self._S["info"].items() if i.get("t", 0) < cutoff]:
            self._S["info"].pop(sym)

    def _lookup(self, symbols):
        """Runs the lookup child process; returns whatever finished in time."""
        try:
            done = subprocess.run([sys.executable, "-c", _LOOKUP_CHILD] + list(symbols),
                                  capture_output=True, timeout=self.RESEARCH_SECONDS)
            out = done.stdout
        except subprocess.TimeoutExpired as e:
            out = e.stdout or b""
        except Exception:
            return []
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        results = []
        for line in (out or "").splitlines():
            try:
                results.append(json.loads(line))
            except ValueError:
                pass
        return results

    # =====================================================================
    # Protection
    # =====================================================================
    def _track_equity(self, data):
        S = self._S
        eq = data["account"]["equity"]
        S["peak"] = max(S["peak"], eq)
        if eq < S["peak"] * (1 - self.BRAKE_ON):
            S["brake"] = True
        elif eq >= S["peak"] * (1 - self.BRAKE_OFF):
            S["brake"] = False

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
                t = S["trail"][sym] = {"side": pos["side"], "x": price, "since": ctx["day"]}
            atr = (self._scan.get(sym) or {}).get("atr") or 0.025
            if pos["side"] == "LONG":
                t["x"] = max(t["x"], price)
                gap = _clip(self.LONG_STOP_ATR * atr, 0.04, 0.12)
                if price <= t["x"] * (1 - gap):
                    orders.append({"symbol": sym, "action": "SELL", "amount": "ALL"})
            else:
                t["x"] = min(t["x"], price)
                gap = _clip(self.SHORT_STOP_ATR * atr, 0.04, 0.10)
                if price >= t["x"] * (1 + gap) or price >= pos["avg_price"] * (1 + self.SHORT_MAX_LOSS):
                    orders.append({"symbol": sym, "action": "COVER", "amount": "ALL"})
        for o in orders:
            S["cool"][o["symbol"]] = ctx["day"]
            S["pending"].pop(o["symbol"], None)
        return orders

    def _news_check(self, data, ctx, busy):
        """Once each morning: close a holding with a burst of bad headlines."""
        S = self._S
        if S["last_news_check"] == ctx["day"]:
            return []
        S["last_news_check"] = ctx["day"]
        orders = []
        for sym, pos in data["account"]["positions"].items():
            if sym in busy:
                continue
            _, flags, sample = self._news_flags(sym, ctx["now"], hours=24)
            if flags >= 2:
                orders.append({"symbol": sym, "action": "SELL" if pos["side"] == "LONG" else "COVER",
                               "amount": "ALL"})
                S["cool"][sym] = ctx["day"]
                S["log"].insert(0, f"{ctx['day']}: left {sym} on bad news ({sample[:60]})")
        return orders

    # =====================================================================
    # The daily decision (35 to 12 minutes before the close)
    # =====================================================================
    def _decide(self, data, ctx, busy):
        S, day, now = self._S, ctx["day"], ctx["now"]
        R = self._rank(day)
        if R["n"] < self.MIN_LIQUID:
            return []
        S["last_decision"] = day
        mood = self._mood(R)
        if S["brake"] and mood == "BULL":
            mood = "NEUTRAL"
        S.update(mood=mood, breadth=R["breadth"], med20=R["med20"])
        n_long, long_share, n_short, short_share = self.PLAN[mood]

        account = data["account"]
        equity = account["equity"]
        positions = account["positions"]
        prices = data.get("prices") or {}
        long_rank = {s: i for i, (_, s) in enumerate(R["longs"])}
        short_rank = {s: i for i, (_, s) in enumerate(R["shorts"])}
        orders, keep_long, keep_short = [], [], []

        def leave(sym, pos, why):
            orders.append({"symbol": sym, "action": "SELL" if pos["side"] == "LONG" else "COVER",
                           "amount": "ALL"})
            S["log"].insert(0, f"{day}: closed {sym} ({why})")

        for sym, pos in positions.items():
            if sym in busy:
                continue
            m = self._scan.get(sym) or {}
            price = pos["price"]
            held_days = self._trading_days_until((S["trail"].get(sym) or {}).get("since", "2000-01-01"), day)
            young = 0 <= held_days < self.MIN_HOLD_DAYS
            if self._earnings_within(sym, day, 1):
                leave(sym, pos, "results due before the next session")
                continue
            if self._news_flags(sym, now)[1] >= 2:
                leave(sym, pos, "bad news")
                continue
            if pos["side"] == "LONG":
                broken = bool(m) and "s50" in m and price < 0.98 * m["s50"]
                ranked = long_rank.get(sym, 10 ** 6) < self.KEEP_RANK
                if n_long and not broken and (ranked or young):
                    keep_long.append(sym)
                else:
                    leave(sym, pos, "trend broken" if broken else "no longer among the best")
            else:
                broken = bool(m) and "s50" in m and price > m["s50"]
                ranked = short_rank.get(sym, 10 ** 6) < 30
                if n_short and not broken and (ranked or young):
                    keep_short.append(sym)
                else:
                    leave(sym, pos, "trend turned" if broken else "no longer among the weakest")
        for keep, rank, side in ((keep_long, long_rank, "LONG"), (keep_short, short_rank, "SHORT")):
            limit = n_long if side == "LONG" else n_short
            keep.sort(key=lambda s: rank.get(s, 10 ** 6))
            for sym in keep[limit:]:
                leave(sym, positions[sym], "fewer positions in this market mood")
            del keep[limit:]

        def pick(ranked, side, n, held):
            chosen = []
            for _, sym in ranked:
                if len(held) + len(chosen) >= n:
                    break
                if sym in positions or sym in busy or self._cooling(sym, day):
                    continue
                if self._earnings_within(sym, day, self.EARNINGS_BUY_DAYS):
                    continue
                count, flags, _ = self._news_flags(sym, now)
                if flags or count >= self.NEWS_BURST:
                    continue
                m = self._scan[sym]
                price = prices.get(sym)
                if price:
                    move = price / m["p"] - 1
                    if side == "LONG" and not (self.TODAY_DROP_LIMIT < move < self.TODAY_RISE_LIMIT):
                        continue
                    if side == "SHORT" and not (-self.TODAY_RISE_LIMIT < move < -self.TODAY_DROP_LIMIT):
                        continue
                if any(self._corr(sym, other) > self.MAX_CORR for other in held + chosen):
                    continue
                chosen.append(sym)
            return chosen

        new_long = pick(R["longs"][:80], "LONG", n_long, keep_long) if n_long else []
        new_short = pick(R["shorts"][:40], "SHORT", n_short, keep_short) if n_short else []

        S["pending"] = {}
        for names, side, n, share in ((keep_long + new_long, "LONG", n_long, long_share),
                                      (keep_short + new_short, "SHORT", n_short, short_share)):
            if not names:
                continue
            inv = {s: 1.0 / max((self._scan.get(s) or {}).get("vol") or 0.3, 0.15) for s in names}
            total = sum(inv.values())
            equal = share / n
            for s in names:
                weight = _clip(share * len(names) / n * inv[s] / total, 0.7 * equal, 1.3 * equal)
                target = weight * equity
                current = abs(positions[s]["value"]) if s in positions else 0.0
                if current < self.ADD_BELOW * target and target - current >= 1.0:
                    S["pending"][s] = {"side": side, "target": round(target, 2), "tries": 0}
                elif s in positions and current > self.TRIM_ABOVE * target and current - target >= 1.0:
                    orders.append({"symbol": s, "action": "SELL" if side == "LONG" else "COVER",
                                   "amount": round(current - target, 2)})
        del S["log"][30:]
        return orders

    def _work_pending(self, data, ctx, busy, max_orders):
        S = self._S
        if not S["pending"] or ctx["to_close"] < 3:
            return []
        account = data["account"]
        positions = account["positions"]
        prices = data.get("prices") or {}
        cash = account["cash"]
        room = account["max_new_short"]
        orders = []
        for sym, p in list(S["pending"].items()):
            if sym in busy or len(orders) >= max_orders:
                continue
            pos = positions.get(sym)
            if pos and pos["side"] != p["side"]:
                continue
            need = p["target"] - (abs(pos["value"]) if pos else 0.0)
            if need < max(1.0, 0.1 * p["target"]) or p["tries"] >= 8:
                S["pending"].pop(sym)
                continue
            p["tries"] += 1
            if not prices.get(sym):
                continue
            if p["side"] == "LONG":
                amount = min(need, cash / 1.001 - 0.02, room)
            else:
                amount = min(need, room)
            if amount < 1.0:
                continue
            amount = math.floor(amount * 100) / 100.0
            orders.append({"symbol": sym, "action": "BUY" if p["side"] == "LONG" else "SHORT",
                           "amount": amount})
            room -= amount
            cash += -amount * 1.001 if p["side"] == "LONG" else amount * 0.999
            S["trail"][sym] = {"side": p["side"], "x": prices[sym], "since": ctx["day"]}
        return orders

    # =====================================================================
    # Watchlist
    # =====================================================================
    def _plan_watchlist(self, data, ctx):
        S, day = self._S, ctx["day"]
        if self._queue_day != day:
            self._build_queue(data, day)
        held = set(data["account"]["positions"])
        must = []

        def want(sym):
            if sym and sym not in held and sym not in must and len(must) < 24:
                must.append(sym)
        for sym in S["pending"]:
            want(sym)
        if ctx["open"] and ctx["to_close"] <= self.DECIDE_FROM + 30 and S["last_decision"] != day:
            R = self._rank(day)
            plan = self.PLAN.get(self._mood(R), self.PLAN["NEUTRAL"])
            shown = 0
            for _, sym in R["longs"][:60]:
                if shown >= (16 if plan[2] == 0 else 12):
                    break
                if not self._cooling(sym, day) and not self._earnings_within(sym, day, self.EARNINGS_BUY_DAYS):
                    want(sym)
                    shown += 1
            if plan[2]:
                for _, sym in R["shorts"][:8]:
                    want(sym)
        for sym in list(self._batch):
            if self._scanned_today(sym, day):
                self._batch.pop(sym)
        free = 30 - len(must) - len([s for s in self._batch if s not in must])
        limit = self._priority_len if ctx["open"] else len(self._queue)
        while free > 0 and self._qpos < limit:
            sym = self._queue[self._qpos]
            self._qpos += 1
            if self._scanned_today(sym, day) or sym in held or sym in self._batch or sym in must:
                continue
            self._batch[sym] = 0
            free -= 1
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
        researched = sum(1 for s in [x for _, x in R["longs"][:30]] if s in S["info"])
        if R["n"] < self.MIN_LIQUID:
            doing = f"scanning ({R['n']}/{self.MIN_LIQUID} liquid stocks ranked so far)"
        elif S["last_decision"] == ctx["day"]:
            doing = "today's decision made" + (f"; buying {len(S['pending'])}" if S["pending"] else "")
        elif ctx["open"]:
            doing = f"decides {self.DECIDE_FROM} min before the close (in {max(0, ctx['to_close'] - self.DECIDE_FROM):.0f} min)"
        else:
            doing = "market closed - scanning and researching"
        news = "" if self.RESEARCH else " | news off"
        if time.time() < self._research_pause_until:
            news = " | news lookups paused (Yahoo not answering)"
        brake = " | drawdown brake ON" if S.get("brake") else ""
        self.status = (f"Mood {S.get('mood') or 'not judged yet'} (breadth {R['breadth']:.0%}) | "
                       f"{n_long} long, {n_short} short | {R['n']} liquid stocks ranked, top 30 "
                       f"researched: {researched} | {doing}{news}{brake}")[:300]

    def _fresh_state(self):
        return {"version": self.STATE_VERSION, "day": "", "pending": {}, "trail": {}, "cool": {},
                "info": {}, "last_decision": "", "last_news_check": "", "peak": 100.0,
                "brake": False, "mood": None, "breadth": 0.0, "med20": 0.0, "log": []}

    def _load(self, storage_dir):
        self._S = self._fresh_state()
        self._dir = storage_dir
        if not storage_dir:
            return
        try:
            with open(os.path.join(storage_dir, "claude_v2_state.json"), "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("version") == self.STATE_VERSION:
                self._S.update(saved)
        except Exception:
            try:                                         # carry over from the first version
                with open(os.path.join(storage_dir, "claude_state.json"), "r", encoding="utf-8") as f:
                    old = json.load(f)
                self._S["peak"] = float(old.get("peak", 100.0))
                self._S["cool"] = dict(old.get("cooldown", {}))
                for sym, t in (old.get("trail") or {}).items():
                    self._S["trail"][sym] = {"side": t.get("side", "LONG"), "x": t.get("x"),
                                             "since": "2000-01-01"}
            except Exception:
                pass
        try:
            with open(os.path.join(storage_dir, "claude_v2_scan.json"), "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("version") == self.STATE_VERSION:
                self._scan = saved.get("scan", {})
                self._rets = saved.get("rets", {})
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
            self._write("claude_v2_state.json", self._S)
        if now - self._last_scan_save >= 300:
            self._last_scan_save = now
            keep = {s: m for s, m in self._scan.items() if m.get("dv", 0) >= 20e6}
            rets = {s: r for s, r in self._rets.items() if s in keep}
            self._write("claude_v2_scan.json", {"version": self.STATE_VERSION,
                                                "scan": keep, "rets": rets})