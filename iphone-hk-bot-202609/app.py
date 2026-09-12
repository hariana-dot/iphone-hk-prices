"""iPhone HK stock bot: polls Apple fulfillment API, Telegram alerts, tiny dashboard."""
import json
import random
import re
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

BASE = Path(__file__).parent
CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
PUBLIC = BASE / "public"
LOG = BASE / "logs" / "stock.ndjson"
LOG.parent.mkdir(exist_ok=True)
WATCH_FILE = BASE / "watch.json"

STATE = {"last_check": None, "error": None, "fails": 0, "rows": []}
PREV = {}
SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/json, text/plain, */*",
    "X-AOS-UI-Fetch-Call-1": "true",
    "X-Skip-Redirect": "true",
    "Referer": "https://www.apple.com/hk/shop/buy-iphone/iphone-18-pro",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
})

API = "https://www.apple.com/hk/shop/retail/pickup-message"
FALLBACK_API = "https://www.apple.com/hk/shop/fulfillment-messages"
BAG = CONFIG.get("bag_url", "https://www.apple.com/hk/shop/bag")

# ---- filter dimensions ----
ALL = "ALL"
MODELS = ["Pro", "Pro Max"]
STORAGES = ["256GB", "512GB", "1TB", "2TB"]
COLORS = ["Black", "Silver", "Burgundy", "Glacier"]
# R-code -> short label (from tested logs; API may return same 6)
STORES_KNOWN = [
    ("R428", "ifc mall"),
    ("R499", "Canton Road"),
    ("R409", "Causeway Bay"),
    ("R485", "Festival Walk"),
    ("R673", "apm"),
    ("R610", "New Town Plaza"),
]
STEPS = ["model", "storage", "color", "store"]
STEP_TITLE = {
    "model": "1/4 Model — tap to multi-select (default All)",
    "storage": "2/4 Storage — tap to multi-select (default All)",
    "color": "3/4 Color — tap to multi-select (default All)",
    "store": "4/4 Pickup — tap to multi-select (default All)",
}

WATCH = {}   # chat_id(str) -> {models:[],storages:[],colors:[],stores:[],muted:bool}
DRAFT = {}   # chat_id(str) -> {step:int, models:set, storages:set, colors:set, stores:set}


def load_watch():
    global WATCH
    try:
        if WATCH_FILE.is_file():
            WATCH = json.loads(WATCH_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print("watch load failed:", e, flush=True)
        WATCH = {}


def save_watch():
    try:
        WATCH_FILE.write_text(json.dumps(WATCH, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print("watch save failed:", e, flush=True)


def blank_sel():
    return {ALL}


def get_watch(cid):
    cid = str(cid)
    w = WATCH.get(cid)
    if not w:
        w = {"models": [ALL], "storages": [ALL], "colors": [ALL], "stores": [ALL], "muted": False}
        WATCH[cid] = w
        save_watch()
    for k in ("models", "storages", "colors", "stores"):
        if not w.get(k):
            w[k] = [ALL]
    return w


def toggle_sel(sel, val):
    """All-wins: tapping All clears others; tapping specific while All set replaces All."""
    sel = set(sel) if sel else {ALL}
    if val == ALL:
        return {ALL}
    if ALL in sel:
        return {val}
    if val in sel:
        sel.discard(val)
        return sel if sel else {ALL}
    sel.add(val)
    return sel


def norm_all(lst):
    # if All present with others, take All only
    if ALL in lst:
        return [ALL]
    return sorted(lst) if lst else [ALL]


def fmt_sel(lst):
    lst = norm_all(lst)
    return "All" if lst == [ALL] else "+".join(lst)


def watch_summary(cid):
    w = get_watch(cid)
    return ("Watch: Model[%s] Storage[%s] Color[%s] Pickup[%s]%s" % (
        fmt_sel(w["models"]), fmt_sel(w["storages"]),
        fmt_sel(w["colors"]), fmt_sel(w["stores"]),
        " (MUTED)" if w.get("muted") else ""))


def store_code(store_str):
    m = re.search(r"\((R\d+)\)", store_str or "")
    return m.group(1) if m else store_str


def row_attrs(row):
    label = row.get("label", "")
    model = "Pro Max" if "Pro Max" in label else "Pro"
    storage = next((s for s in STORAGES if s in label), "?")
    color = next((c for c in COLORS if label.endswith(c) or (" " + c + " ") in (" " + label + " ")), "?")
    if color == "?":  # fallback: last word
        color = label.split()[-1] if label else "?"
    return model, storage, color, store_code(row.get("store", ""))


def match_row(w, row):
    if row.get("status") != "in-stock":
        return False
    model, storage, color, code = row_attrs(row)
    if ALL not in w["models"] and model not in w["models"]:
        return False
    if ALL not in w["storages"] and storage not in w["storages"]:
        return False
    if ALL not in w["colors"] and color not in w["colors"]:
        return False
    if ALL not in w["stores"] and code not in w["stores"]:
        return False
    return True


def tg_enabled():
    tg = CONFIG.get("telegram", {})
    return bool(tg.get("enabled") and tg.get("token") and tg.get("token") != "REPLACE_ME")


def tg_api(method, payload, timeout=20):
    tg = CONFIG.get("telegram", {})
    url = "https://api.telegram.org/bot%s/%s" % (tg["token"], method)
    r = requests.post(url, json=payload, timeout=timeout)
    return r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {}


def tg_send(cid, text, markup=None):
    if not tg_enabled():
        return
    try:
        payload = {"chat_id": cid, "text": text}
        if markup:
            payload["reply_markup"] = markup
        tg_api("sendMessage", payload, timeout=15)
    except Exception as e:
        print("telegram failed:", e, flush=True)


def tg_answer(cb_id):
    try:
        tg_api("answerCallbackQuery", {"callback_query_id": cb_id}, timeout=10)
    except Exception:
        pass


def broadcast(text, markup=None, only_matching_row=None):
    """Send to all registered watchers (respect mute + filter if row given)."""
    sent = 0
    for cid, w in list(WATCH.items()):
        if w.get("muted"):
            continue
        if only_matching_row is not None and not match_row(w, only_matching_row):
            continue
        tg_send(cid, text, markup)
        sent += 1
    if sent == 0 and only_matching_row is None:
        # fallback to legacy single chat_id (pre-watch registration)
        legacy = str(CONFIG.get("telegram", {}).get("chat_id", ""))
        if legacy and legacy != "REPLACE_ME":
            tg_send(legacy, text, markup)
    return sent


def kb_for_step(cid):
    d = DRAFT[str(cid)]
    step = STEPS[d["step"]]
    if step == "model":
        opts = [(m, m) for m in MODELS]
        sel = d["models"]
    elif step == "storage":
        opts = [(s, s) for s in STORAGES]
        sel = d["storages"]
    elif step == "color":
        opts = [(c, c) for c in COLORS]
        sel = d["colors"]
    else:
        opts = [("%s %s" % (code, name), code) for code, name in STORES_KNOWN]
        sel = d["stores"]
    rows = []
    row = [{"text": ("✅ " if ALL in sel else "") + "All", "callback_data": "t:%s:%s" % (step, ALL)}]
    rows.append(row)
    for label, val in opts:
        rows.append([{"text": ("✅ " if val in sel and ALL not in sel else "") + label,
                      "callback_data": "t:%s:%s" % (step, val)}])
    nav = []
    if d["step"] > 0:
        nav.append({"text": "⬅ Back", "callback_data": "nav:back"})
    if d["step"] < len(STEPS) - 1:
        nav.append({"text": "Next ➡", "callback_data": "nav:next"})
    else:
        nav.append({"text": "💾 Save", "callback_data": "nav:save"})
    rows.append(nav)
    rows.append([{"text": "❌ Cancel", "callback_data": "nav:cancel"}])
    return {"inline_keyboard": rows}


def draft_text(cid):
    d = DRAFT[str(cid)]
    step = STEPS[d["step"]]
    return ("%s\n\nModel[%s] Storage[%s] Color[%s] Pickup[%s]\nAll-wins: tapping All ignores other picks in that group." % (
        STEP_TITLE[step], fmt_sel(sorted(d["models"])), fmt_sel(sorted(d["storages"])),
        fmt_sel(sorted(d["colors"])), fmt_sel(sorted(d["stores"]))))


def start_draft(cid):
    w = get_watch(cid)
    DRAFT[str(cid)] = {"step": 0, "models": set(w["models"]), "storages": set(w["storages"]),
                       "colors": set(w["colors"]), "stores": set(w["stores"])}


MAIN_KB = {"inline_keyboard": [
    [{"text": "🎯 Watch setup", "callback_data": "menu:watch"}],
    [{"text": "📦 Stock now", "callback_data": "menu:stock"}],
    [{"text": "🔕 Mute / 🔊 Unmute", "callback_data": "menu:mute"}],
]}


def handle_text(cid, text):
    text = (text or "").strip()
    if text.startswith("/start"):
        get_watch(cid)
        tg_send(cid, "iPhone 18 HK bot on :9120.\n" + watch_summary(cid) +
                "\n\n/start menu · /watch setup · /stock now · /mute /unmute · /reset", MAIN_KB)
    elif text.startswith("/watch"):
        start_draft(cid)
        tg_send(cid, draft_text(cid), kb_for_step(cid))
    elif text.startswith("/stock"):
        send_stock_now(cid)
    elif text.startswith("/mute"):
        get_watch(cid)["muted"] = True
        save_watch()
        tg_send(cid, "Muted. " + watch_summary(cid))
    elif text.startswith("/unmute"):
        get_watch(cid)["muted"] = False
        save_watch()
        tg_send(cid, "Unmuted. " + watch_summary(cid))
    elif text.startswith("/reset"):
        WATCH[str(cid)] = {"models": [ALL], "storages": [ALL], "colors": [ALL], "stores": [ALL], "muted": False}
        save_watch()
        tg_send(cid, "Reset to All. " + watch_summary(cid))
    else:
        tg_send(cid, "Commands: /watch /stock /mute /unmute /reset", MAIN_KB)


def send_stock_now(cid):
    w = get_watch(cid)
    rows = [r for r in STATE.get("rows", []) if match_row(w, r)]
    if not rows:
        n_all = sum(1 for r in STATE.get("rows", []) if r.get("status") == "in-stock")
        tg_send(cid, "No matching in-stock now. (%d in-stock overall, last check %s)\n%s" % (
            n_all, STATE.get("last_check"), watch_summary(cid)))
        return
    lines = ["%s @ %s" % (r["label"], r["store"]) for r in rows[:20]]
    extra = "" if len(rows) <= 20 else "\n…+%d more" % (len(rows) - 20)
    tg_send(cid, "IN STOCK %d matching:\n%s%s\n%s" % (len(rows), "\n".join(lines), extra, BAG))


def handle_callback(cid, data, cb_id, msg_id=None):
    tg_answer(cb_id)
    cid = str(cid)
    if data == "menu:watch":
        start_draft(cid)
        tg_send(cid, draft_text(cid), kb_for_step(cid))
        return
    if data == "menu:stock":
        send_stock_now(cid)
        return
    if data == "menu:mute":
        w = get_watch(cid)
        w["muted"] = not w.get("muted")
        save_watch()
        tg_send(cid, ("Muted. " if w["muted"] else "Unmuted. ") + watch_summary(cid))
        return
    if data.startswith("t:"):
        _, step, val = data.split(":", 2)
        d = DRAFT.get(cid)
        if not d or STEPS[d["step"]] != step:
            return
        key = {"model": "models", "storage": "storages", "color": "colors", "store": "stores"}[step]
        d[key] = toggle_sel(d[key], val)
        try:
            tg_api("editMessageText", {"chat_id": cid, "message_id": msg_id,
                                       "text": draft_text(cid), "reply_markup": kb_for_step(cid)}, timeout=10)
        except Exception:
            tg_send(cid, draft_text(cid), kb_for_step(cid))
        return
    if data == "nav:next":
        d = DRAFT.get(cid)
        if d and d["step"] < len(STEPS) - 1:
            d["step"] += 1
            tg_send(cid, draft_text(cid), kb_for_step(cid))
        return
    if data == "nav:back":
        d = DRAFT.get(cid)
        if d and d["step"] > 0:
            d["step"] -= 1
            tg_send(cid, draft_text(cid), kb_for_step(cid))
        return
    if data == "nav:cancel":
        DRAFT.pop(cid, None)
        tg_send(cid, "Cancelled. " + watch_summary(cid), MAIN_KB)
        return
    if data == "nav:save":
        d = DRAFT.pop(cid, None)
        if not d:
            return
        WATCH[cid] = {"models": norm_all(sorted(d["models"])), "storages": norm_all(sorted(d["storages"])),
                      "colors": norm_all(sorted(d["colors"])), "stores": norm_all(sorted(d["stores"])),
                      "muted": get_watch(cid).get("muted", False)}
        save_watch()
        tg_send(cid, "Saved. " + watch_summary(cid) +
                "\nYou will be alerted only on matching in-stock.", MAIN_KB)
        return


def bot_loop():
    if not tg_enabled():
        print("telegram disabled (config.json). Wizard offline.", flush=True)
        return
    print("telegram wizard polling @iphone18hk_bot …", flush=True)
    offset = 0
    while True:
        try:
            resp = tg_api("getUpdates", {"offset": offset, "timeout": 30}, timeout=40)
            for u in resp.get("result", []):
                offset = max(offset, u.get("update_id", 0) + 1)
                msg = u.get("message") or {}
                cb = u.get("callback_query")
                if cb:
                    cid = str((cb.get("message") or {}).get("chat", {}).get("id") or cb.get("from", {}).get("id"))
                    handle_callback(cid, cb.get("data", ""), cb.get("id"),
                                    (cb.get("message") or {}).get("message_id"))
                elif msg.get("text"):
                    cid = str(msg.get("chat", {}).get("id"))
                    get_watch(cid)
                    handle_text(cid, msg.get("text", ""))
        except Exception as e:
            print("bot poll err:", e, flush=True)
            time.sleep(5)


def fetch_stock(parts):
    params = [("parts.%d" % i, p) for i, p in enumerate(parts)]
    params += [("searchNearby", "true"), ("store", CONFIG.get("anchor_store", "R428"))]
    err = None
    for url in (API, FALLBACK_API):
        try:
            r = SESSION.get(url, params=params, timeout=20)
            if r.status_code in (403, 429) or r.status_code == 541:
                err = "%s: http %s" % (url, r.status_code)
                continue
            r.raise_for_status()
            return r.json(), None
        except Exception as e:
            err = "%s: %s" % (url, e)
    return None, err


def parse_pickup_message(data, parts):
    """Parser for /shop/retail/pickup-message shape: body.stores[].partsAvailability."""
    rows = []
    try:
        stores = data["body"]["stores"]
    except (KeyError, TypeError):
        return None
    for s in stores:
        avail = s.get("partsAvailability", {})
        for p in parts:
            info = avail.get(p, {})
            disp = info.get("pickupDisplay", "")
            if disp == "available":
                status = "in-stock"
            elif disp in ("unavailable", "ineligible"):
                status = "out-of-stock"
            elif not info:
                status = "unknown"
            else:
                status = "unknown"
            rows.append({
                "store": "%s (%s)" % (s.get("storeName", "?"), s.get("storeNumber", "?")),
                "part": p,
                "label": CONFIG["parts"].get(p, p),
                "status": status,
            })
    return rows


def parse_fulfillment(data, parts):
    """Parser for /shop/fulfillment-messages shape: body.content.pickupMessage.stores."""
    rows = []
    try:
        stores = data["body"]["content"]["pickupMessage"]["stores"]
    except (KeyError, TypeError):
        return None
    for s in stores:
        avail = s.get("partsAvailability", {})
        for p in parts:
            info = avail.get(p, {})
            if not info:
                status = "unknown"
            elif not info.get("storeSearchEnabled", True):
                status = "unknown"
            else:
                disp = info.get("pickupDisplay", "")
                status = "in-stock" if disp == "available" else "out-of-stock"
            rows.append({
                "store": "%s (%s)" % (s.get("storeName", "?"), s.get("storeNumber", "?")),
                "part": p,
                "label": CONFIG["parts"].get(p, p),
                "status": status,
            })
    return rows


def parse(data, parts):
    rows = parse_pickup_message(data, parts)
    if rows is not None:
        return rows
    return parse_fulfillment(data, parts)


def poll_loop():
    backoff_until = 0
    while True:
        parts = [p for p in CONFIG["parts"] if not p.startswith("EXAMPLE")]
        if not parts:
            STATE["error"] = "config.json has no real part numbers"
            time.sleep(15)
            continue
        if time.time() < backoff_until:
            time.sleep(5)
            continue
        data, err = fetch_stock(parts)
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        STATE["last_check"] = now
        if err or data is None:
            STATE["fails"] += 1
            STATE["error"] = err or "parse failed"
            n = STATE["fails"]
            if n == 5:
                broadcast("HK bot: Apple API failing 5x in a row (%s)" % STATE["error"])
            backoff_until = time.time() + min(2 ** min(n, 4) * 60, 8 * 60)
        else:
            rows = parse(data, parts)
            if rows is None:
                STATE["fails"] += 1
                STATE["error"] = "unexpected Apple response shape"
            else:
                STATE["fails"] = 0
                STATE["error"] = None
                STATE["rows"] = rows
                for r in rows:
                    key = (r["part"], r["store"])
                    old = PREV.get(key)
                    if r["status"] == "in-stock" and old != "in-stock":
                        msg = "IN STOCK: %s @ %s (%s) %s" % (r["label"], r["store"], now, BAG)
                        print(msg, flush=True)
                        n = broadcast(msg, only_matching_row=r)
                        if n == 0:
                            print("in-stock but no watcher matches; skipped Telegram", flush=True)
                        if CONFIG.get("open_browser_on_stock"):
                            try:
                                webbrowser.open(BAG)
                            except Exception:
                                pass
                    PREV[key] = r["status"]
                LOG.parent.mkdir(exist_ok=True)
                with open(LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": now, "rows": rows}) + "\n")
        time.sleep(CONFIG.get("interval_sec", 35) + random.uniform(-5, 5))


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/status":
            body = json.dumps({**STATE, "watchers": len(WATCH)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        name = "index.html" if self.path in ("/", "") else self.path.lstrip("/").split("?")[0]
        f = PUBLIC / name
        if name not in ("index.html",) or not f.is_file():
            self.send_response(404)
            self.end_headers()
            return
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    load_watch()
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=bot_loop, daemon=True).start()
    port = CONFIG.get("port", 9120)
    print("dashboard: http://localhost:%d" % port, flush=True)
    print("lan: use your PC ipv4 + port, e.g. http://192.168.x.x:%d" % port, flush=True)
    HTTPServer(("0.0.0.0", port), H).serve_forever()
