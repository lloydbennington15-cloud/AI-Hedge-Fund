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
  * It writes `view.json` every cycle: the account, the watchlist with prices,
    waiting orders and recent results. The trading desk shows that file.
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
        del st["results"][self.MAX_RESULTS:]

        market = data.get("market", {})
        prices = data.get("prices") or {}
        times = data.get("price_times_utc") or {}
        positions = data.get("account", {}).get("positions", {})
        orders = []
        if market.get("is_open"):
            opened = market.get("opens_at_utc") or ""
            for order in list(st["pending"]):
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
        view = {"updated_utc": now, "market": market, "account": data.get("account", {}),
                "watch": watch, "watch_limit": self.WATCH_LIMIT,
                "refused": status.get("rejected", {}),
                "pending": self._st["pending"], "results": self._st["results"][:15]}
        self._write("view.json", view)

    # ------------------------------------------------------------------ saving
    def _load(self, storage_dir):
        self._st = {"watch": [], "pending": [], "results": []}
        self._dir = storage_dir
        if not storage_dir:
            return
        os.makedirs(os.path.join(storage_dir, "inbox"), exist_ok=True)
        try:
            with open(os.path.join(storage_dir, "state.json"), "r", encoding="utf-8") as f:
                saved = json.load(f)
            for key in self._st:
                if isinstance(saved.get(key), list):
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
