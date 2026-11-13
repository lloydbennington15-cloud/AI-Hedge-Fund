"""
lloyd_bot.py - Lloyd's own account in the tournament
====================================================

This "bot" makes no decisions of its own. It passes on the orders Lloyd
places from the dashboard's trading desk, so the engine treats Lloyd exactly
like every other contestant: same cash, same prices, same fee, same rules,
same trading hours.

How it works
  * The dashboard drops each instruction (an order, a cancellation, a
    watchlist change) as a small file in this bot's `inbox` folder.
  * Every engine cycle this bot reads the inbox and keeps a list of orders
    waiting to be sent.
  * An order is sent to the engine once the market is open AND the engine has
    a price for that stock from the current session. So an order placed while
    the market is closed goes through at the next open, and an order for a
    stock that isn't on the watchlist yet waits the one cycle it takes for its
    price to arrive.
  * Stop-losses and take-profits: Lloyd can attach them to an order or set them
    on a position. Like every bot's own stops, they're checked once per engine
    cycle while the market is open; when one is crossed this bot sends a
    SELL (or COVER) for the whole position at that cycle's price.
  * It writes `view.json` every cycle: the account, the watchlist with prices,
    today's minute-by-minute prices, the market movers lists, waiting orders
    and recent results. It writes `daily.json` (about six months of daily
    prices) whenever that changes. The trading desk shows both files, so
    Lloyd sees exactly the market data the bots receive, and nothing more.
"""

import json
import os
import re

ACTIONS = ("BUY", "SELL", "SHORT", "COVER")
SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


class LloydBot:
    WATCH_LIMIT = 30
    MAX_RESULTS = 30
    MAX_PENDING = 20

    def __init__(self):
        self.watchlist = []
        self.status = "Waiting for Lloyd's orders"
        self._dir = None
        self._st = None
        self._daily_sig = None

    # ------------------------------------------------------------------ engine
    def market_tick(self, data):
        if self._st is None:
            self._load(data.get("storage_dir"))
        st = self._st
        now = data.get("timestamp_utc", "")
        self._read_inbox(now)

        for result in data.get("last_order_results") or []:
            row = {k: result.get(k) for k in ("symbol", "action", "status", "price", "value",
                                              "fee", "realized_pnl", "reason", "requested")}
            row["time_utc"] = now
            st["results"].insert(0, row)
            arm = st["arm"].pop(row["symbol"], None) if row["action"] in ("BUY", "SHORT") else None
            if arm and row["status"] == "FILLED" and row.get("price"):
                self._arm_stop(row["symbol"], row["action"], row["price"], arm, now)
        del st["results"][self.MAX_RESULTS:]

        market = data.get("market", {})
        prices = data.get("prices") or {}
        times = data.get("price_times_utc") or {}
        positions = data.get("account", {}).get("positions", {})
        for sym in list(st["stops"]):                     # position closed: forget its stops
            if sym not in positions and sym not in st["arm"]:
                st["stops"].pop(sym)
        orders = []
        if market.get("is_open"):
            opened = market.get("opens_at_utc") or ""
            waiting = {o["symbol"] for o in st["pending"]}
            for sym, stop in list(st["stops"].items()):
                pos = positions.get(sym)
                price = prices.get(sym)
                if not pos or not price or str(times.get(sym, "")) < opened or sym in waiting:
                    continue
                reason = self._check_stop(stop, pos["side"], price)
                if reason:
                    action = "SELL" if pos["side"] == "LONG" else "COVER"
                    orders.append({"symbol": sym, "action": action, "amount": "ALL"})
                    st["stops"].pop(sym)
                    st["results"].insert(0, {"time_utc": now, "symbol": sym, "action": action,
                                             "status": "TRIGGERED", "price": price,
                                             "reason": reason})
            busy = {o["symbol"] for o in orders}
            for order in list(st["pending"]):
                if order["symbol"] in busy:
                    st["pending"].remove(order)           # the stop has already closed it
                    continue
                if len(orders) >= 20:
                    break
                sym = order["symbol"]
                held = sym in positions
                fresh = sym in prices and str(times.get(sym, "")) >= opened
                if order["action"] in ("SELL", "COVER") and not held:
                    st["pending"].remove(order)
                    st["results"].insert(0, {"time_utc": now, "symbol": sym,
                                             "action": order["action"], "status": "REJECTED",
                                             "reason": "you don't hold this stock any more",
                                             "requested": order["amount"]})
                    continue
                if fresh or held:
                    orders.append({"symbol": sym, "action": order["action"],
                                   "amount": order["amount"]})
                    st["pending"].remove(order)
                    if order.get("arm"):
                        st["arm"][sym] = order["arm"]

        waiting = [o["symbol"] for o in st["pending"]]
        self.watchlist = list(dict.fromkeys(waiting + st["watch"]))[:self.WATCH_LIMIT]
        if st["pending"]:
            when = "at the open" if not market.get("is_open") else "as soon as prices arrive"
            self.status = f"{len(st['pending'])} order(s) from Lloyd waiting to go {when}"
        elif orders:
            self.status = f"Sent {len(orders)} order(s) from Lloyd"
        else:
            self.status = "Waiting for Lloyd's orders"

        self._write_view(data, now)
        self._save()
        return orders

    # ------------------------------------------------------------------ stops
    def _arm_stop(self, sym, action, fill_price, arm, now):
        """Turn the percentages given with an order into prices, using the
        price the order actually filled at."""
        long = action == "BUY"
        stop = {"set_utc": now, "stop_price": None, "trail_pct": None, "target_price": None}
        try:
            pct = float(arm.get("stop_pct") or 0)
            target = float(arm.get("target_pct") or 0)
        except (TypeError, ValueError):
            return
        if pct > 0 and arm.get("trail"):
            stop["trail_pct"] = pct
        elif pct > 0:
            stop["stop_price"] = round(fill_price * (1 - pct / 100 if long else 1 + pct / 100), 4)
        if target > 0:
            stop["target_price"] = round(fill_price * (1 + target / 100 if long else 1 - target / 100), 4)
        self._st["stops"][sym] = stop

    @staticmethod
    def _check_stop(stop, side, price):
        """Returns a reason if this price triggers the stop, else None.
        A trailing stop follows the best price since it was set."""
        if side == "LONG":
            stop["best"] = max(stop.get("best") or price, price)
            if stop.get("stop_price") and price <= stop["stop_price"]:
                return f"stop-loss: price ${price:.2f} fell to or below ${stop['stop_price']:.2f}"
            if stop.get("trail_pct") and price <= stop["best"] * (1 - stop["trail_pct"] / 100):
                return (f"trailing stop: price ${price:.2f} is {stop['trail_pct']:g}% below "
                        f"its best of ${stop['best']:.2f}")
            if stop.get("target_price") and price >= stop["target_price"]:
                return f"take-profit: price ${price:.2f} reached ${stop['target_price']:.2f}"
        else:
            stop["best"] = min(stop.get("best") or price, price)
            if stop.get("stop_price") and price >= stop["stop_price"]:
                return f"stop-loss: price ${price:.2f} rose to or above ${stop['stop_price']:.2f}"
            if stop.get("trail_pct") and price >= stop["best"] * (1 + stop["trail_pct"] / 100):
                return (f"trailing stop: price ${price:.2f} is {stop['trail_pct']:g}% above "
                        f"its low of ${stop['best']:.2f}")
            if stop.get("target_price") and price <= stop["target_price"]:
                return f"take-profit: price ${price:.2f} reached ${stop['target_price']:.2f}"
        return None

    # ------------------------------------------------------------------ inbox
    def _read_inbox(self, now):
        if not self._dir:
            return
        inbox = os.path.join(self._dir, "inbox")
        try:
            names = sorted(n for n in os.listdir(inbox) if n.endswith(".json"))
        except OSError:
            return
        st = self._st
        for name in names:
            path = os.path.join(inbox, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    msg = json.load(f)
            except Exception:
                msg = None
            try:
                os.remove(path)
            except OSError:
                pass
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            sym = str(msg.get("symbol", "")).strip().upper().replace(".", "-")
            if kind == "order":
                action = str(msg.get("action", "")).upper()
                amount = msg.get("amount")
                if amount != "ALL":
                    try:
                        amount = round(float(amount), 2)
                    except (TypeError, ValueError):
                        continue
                if (action in ACTIONS and SYMBOL_RE.match(sym)
                        and len(st["pending"]) < self.MAX_PENDING):
                    st["pending"].append({"id": str(msg.get("id", name)), "symbol": sym,
                                          "action": action, "amount": amount,
                                          "placed_utc": msg.get("placed_utc") or now})
                    arm = {k: msg.get(k) for k in ("stop_pct", "trail", "target_pct")}
                    if action in ("BUY", "SHORT") and (msg.get("stop_pct") or msg.get("target_pct")):
                        st["pending"][-1]["arm"] = arm
            elif kind == "stop" and SYMBOL_RE.match(sym):
                stop = {"set_utc": now}
                for key in ("stop_price", "trail_pct", "target_price"):
                    try:
                        value = float(msg.get(key)) if msg.get(key) not in (None, "") else None
                    except (TypeError, ValueError):
                        value = None
                    stop[key] = value if value and value > 0 else None
                if any(stop[k] for k in ("stop_price", "trail_pct", "target_price")):
                    st["stops"][sym] = stop
            elif kind == "clear_stop":
                st["stops"].pop(sym, None)
            elif kind == "cancel":
                st["pending"] = [o for o in st["pending"] if o["id"] != str(msg.get("id"))]
            elif kind == "watch" and SYMBOL_RE.match(sym):
                if sym not in st["watch"] and len(st["watch"]) < self.WATCH_LIMIT:
                    st["watch"].append(sym)
            elif kind == "unwatch":
                st["watch"] = [s for s in st["watch"] if s != sym]

    # ------------------------------------------------------------------ view for the desk
    def _write_view(self, data, now):
        if not self._dir:
            return
        market = data.get("market", {})
        today = market.get("new_york_date", "")
        universe = data.get("universe") or {}
        prices = data.get("prices") or {}
        times = data.get("price_times_utc") or {}
        daily = data.get("daily_bars") or {}
        watch = []
        for sym in list(dict.fromkeys(self.watchlist + list(prices))):
            bars = [b for b in daily.get(sym, ()) if str(b[0]) < today]
            prev = float(bars[-1][4]) if bars else None
            price = prices.get(sym)
            watch.append({"symbol": sym, "name": universe.get(sym, ""), "price": price,
                          "price_time_utc": times.get(sym),
                          "prev_close": prev,
                          "change_pct": (price / prev - 1) * 100 if price and prev else None,
                          "on_watchlist": sym in self._st["watch"]})
        status = data.get("watchlist_status") or {}
        intraday = {sym: [[b[0], b[4], b[5]] for b in bars]
                    for sym, bars in (data.get("intraday_bars") or {}).items() if bars}
        view = {"updated_utc": now, "market": market, "account": data.get("account", {}),
                "watch": watch, "watch_limit": self.WATCH_LIMIT,
                "refused": status.get("rejected", {}),
                "pending": self._st["pending"], "results": self._st["results"][:15],
                "stops": self._st["stops"], "arming": self._st["arm"],
                "intraday": intraday, "movers": data.get("movers") or {}}
        self._write("view.json", view)

        history = {sym: [[b[0], b[4], b[5]] for b in bars]
                   for sym, bars in daily.items() if bars}
        signature = sorted((sym, len(rows), rows[-1][0]) for sym, rows in history.items())
        if signature != self._daily_sig:
            self._daily_sig = signature
            self._write("daily.json", {"updated_utc": now, "daily": history})

    # ------------------------------------------------------------------ saving
    def _load(self, storage_dir):
        self._st = {"watch": [], "pending": [], "results": [], "stops": {}, "arm": {}}
        self._dir = storage_dir
        if not storage_dir:
            return
        os.makedirs(os.path.join(storage_dir, "inbox"), exist_ok=True)
        try:
            with open(os.path.join(storage_dir, "state.json"), "r", encoding="utf-8") as f:
                saved = json.load(f)
            for key, empty in list(self._st.items()):
                if isinstance(saved.get(key), type(empty)):
                    self._st[key] = saved[key]
        except Exception:
            pass

    def _write(self, name, payload):
        path = os.path.join(self._dir, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)

    def _save(self):
        if self._dir:
            self._write("state.json", self._st)