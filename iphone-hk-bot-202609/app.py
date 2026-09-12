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

try:
    from curl_cffi import requests as crequests  # Chrome TLS fingerprint (beats Akamai 541)
except Exception:
    crequests = None

BASE = Path(__file__).parent
CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
PUBLIC = BASE / "public"
LOG = BASE / "logs" / "stock.ndjson"
LOG.parent.mkdir(exist_ok=True)
WATCH_FILE = BASE / "watch.json"

STATE = {"last_check": None, "error": None, "fails": 0, "holding": False, "rows": []}
PREV = {}
# ---- anti-541: real Chrome TLS fingerprint via curl_cffi (Akamai flags
# python-requests TLS + metronomic polling, esp. on pre-order day). UA pool
# stays Chrome-124 to match the impersonated TLS. Session rebuilt regularly
# and on every block so tracking cookies never get old.
UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.119 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.119 Safari/537.36",
]
LANG_POOL = ["en-HK,en;q=0.9", "en-US,en;q=0.9", "zh-HK,zh;q=0.9,en;q=0.8", "zh-HK,zh;q=0.9,en;q=0.7"]


def new_session():
    if crequests is not None:
        s = crequests.Session(impersonate="chrome124")
    else:
        s = requests.Session()
    ua = random.choice(UA_POOL)
    s.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": random.choice(LANG_POOL),
        "X-AOS-UI-Fetch-Call-1": "true",
        "X-Skip-Redirect": "true",
        "Referer": "https://www.apple.com/hk/shop/buy-iphone/iphone-18-pro",
        "User-Agent": ua,
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    })
    if "Chrome/" in ua and "Safari/" in ua:
        s.headers["sec-ch-ua"] = '"Chromium";v="124", "Google Chrome";v="124", "Not=A?Brand";v="99"'
        s.headers["sec-ch-ua-mobile"] = "?0"
        s.headers["sec-ch-ua-platform"] = '"macOS"' if "Macintosh" in ua else '"Windows"'
    return s


SESSION = new_session()
POLL_COUNT = 0

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

# ---- buy links (HK English store; slugs verified against page SKUs) ----
URL_COLOR_SLUGS = {"Black": "black", "Silver": "silver", "Glacier": "glacier", "Burgundy": "burgundy"}
STORE_SHORT = {"R428": "ifc", "R499": "Canton", "R409": "CWB",
               "R485": "FestWalk", "R673": "apm", "R610": "NTP"}


def buy_url(label):
    """Pre-selected model+storage+color configure page (browser; Store app may drop the variant path)."""
    model = "Pro Max" if "Pro Max" in label else "Pro"
    storage = next((s for s in STORAGES if s in label), "")
    color = next((c for c in COLORS if c in label), "")
    size = "6.9" if model == "Pro Max" else "6.3"
    slug = URL_COLOR_SLUGS.get(color, "")
    return "https://www.apple.com/hk/shop/buy-iphone/iphone-18-pro/%s-inch-display-%s-%s" % (
        size, storage.lower(), slug)


def grouped_lines(hit_rows, cap=20, show_stores=True):
    """Collapse in-stock rows by variant: one line per model+storage+color with Buy link
    (store list included only if show_stores)."""
    by_part = {}
    order = []
    for r in hit_rows:
        if r["part"] not in by_part:
            by_part[r["part"]] = {"label": r["label"], "stores": []}
            order.append(r["part"])
        code = store_code(r.get("store", ""))
        short = STORE_SHORT.get(code, code)
        if short not in by_part[r["part"]]["stores"]:
            by_part[r["part"]]["stores"].append(short)
    lines = []
    for p in order[:cap]:
        e = by_part[p]
        if show_stores:
            lines.append('%s — %s <a href="%s">Buy</a>' % (
                e["label"], "/".join(e["stores"]), buy_url(e["label"])))
        else:
            lines.append('%s <a href="%s">Buy</a>' % (e["label"], buy_url(e["label"])))
    if len(order) > cap:
        lines.append("…+%d more variants" % (len(order) - cap))
    return lines


def format_alert(hit_rows, now, live, show_stores=True):
    if live:
        head = "IN STOCK: %d new combos (%s)" % (len(hit_rows), now)
    else:
        head = "SAMPLE ALERT (test — not real stock)"
    return ("%s\nBuy opens the pre-selected config in your browser.\n"
            "Then tap: No trade-in → Continue → No AppleCare+ → Add to bag → pickup.\n\n%s\n\n"
            "<a href=\"%s\">Open bag</a>" % (head, "\n".join(grouped_lines(hit_rows, show_stores=show_stores)), BAG))

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
        w = {"models": [ALL], "storages": [ALL], "colors": [ALL], "stores": [ALL],
             "muted": False, "show_stores": False, "seen": {}}
        WATCH[cid] = w
        save_watch()
        return w
    for k in ("models", "storages", "colors", "stores"):
        if not w.get(k):
            w[k] = [ALL]
    if "show_stores" not in w:
        w["show_stores"] = False
    if "seen" not in w:
        w["seen"] = {}
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
    return ("Watch: Model[%s] Storage[%s] Color[%s] Pickup[%s] Stores:%s%s" % (
        fmt_sel(w["models"]), fmt_sel(w["storages"]),
        fmt_sel(w["colors"]), fmt_sel(w["stores"]),
        "shown" if w.get("show_stores", False) else "hidden",
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


def tg_send(cid, text, markup=None, parse_mode=None, preview=True):
    if not tg_enabled():
        return
    try:
        payload = {"chat_id": cid, "text": text,
                   "disable_web_page_preview": not preview}
        if markup:
            payload["reply_markup"] = markup
        if parse_mode:
            payload["parse_mode"] = parse_mode
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
    [{"text": "📍 Stores shown/hidden", "callback_data": "menu:stores"}],
]}


def handle_text(cid, text):
    text = (text or "").strip()
    # group chats deliver commands as /cmd@BotName — strip the suffix
    if text.startswith("/"):
        head, sep, rest = text.partition(" ")
        head = head.split("@", 1)[0]
        text = head + (sep + rest if sep else "")
    if text.startswith("/start"):
        get_watch(cid)
        tg_send(cid, "iPhone 18 HK bot on :9120.\n" + watch_summary(cid) +
                "\n\n/start menu · /watch setup · /stock now · /mute /unmute · /reset · /pickup on|off", MAIN_KB)
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
        WATCH[str(cid)] = {"models": [ALL], "storages": [ALL], "colors": [ALL], "stores": [ALL],
                           "muted": False, "show_stores": False, "seen": {}}
        save_watch()
        tg_send(cid, "Reset to All. " + watch_summary(cid))
    elif text.startswith("/pickup"):
        w = get_watch(cid)
        arg = text[len("/pickup"):].strip().lower()
        if arg in ("on", "show", "yes"):
            w["show_stores"] = True
        elif arg in ("off", "hide", "no"):
            w["show_stores"] = False
        else:
            w["show_stores"] = not w.get("show_stores", False)
        save_watch()
        tg_send(cid, ("Pickup locations shown. " if w["show_stores"] else "Pickup locations hidden. ") +
                watch_summary(cid))
    else:
        tg_send(cid, "Commands: /watch /stock /mute /unmute /reset /pickup on|off", MAIN_KB)


LEGACY_SEEN = {}
LEGACY_FILT = {"models": [ALL], "storages": [ALL], "colors": [ALL], "stores": [ALL]}


def _flips_against(rows, filt, seen):
    """Variant-level flip: part in-stock (under filt's store filter) now, but wasn't before.
    All pickup locations count as one pool per variant. Updates seen."""
    cur = {}
    for r in rows:
        if not match_row(filt, r):
            continue
        cur.setdefault(r["part"], []).append(r)
    flipped = []
    for p, rs in cur.items():
        if not seen.get(p):
            flipped.extend(rs)
    for p in list(seen):
        if p not in cur:
            seen[p] = False
    for p in cur:
        seen[p] = True
    return flipped


def send_flips(rows, now):
    """One grouped message per watcher with newly-flipped variants (respects mute)."""
    sent = 0
    for cid in list(WATCH.keys()):
        w = get_watch(cid)
        flipped = _flips_against(rows, w, w.setdefault("seen", {}))
        save_watch()
        if not flipped or w.get("muted"):
            continue
        tg_send(cid, format_alert(flipped, now, True, w.get("show_stores", False)),
                parse_mode="HTML", preview=False)
        sent += 1
    if sent == 0:
        # fallback to legacy single chat_id (pre-watch registration)
        legacy = str(CONFIG.get("telegram", {}).get("chat_id", ""))
        if legacy and legacy != "REPLACE_ME":
            flipped = _flips_against(rows, LEGACY_FILT, LEGACY_SEEN)
            if flipped:
                tg_send(legacy, format_alert(flipped, now, True, True),
                        parse_mode="HTML", preview=False)
                sent = 1
    return sent


def send_stock_now(cid):
    w = get_watch(cid)
    rows = [r for r in STATE.get("rows", []) if match_row(w, r)]
    if not rows:
        n_all = sum(1 for r in STATE.get("rows", []) if r.get("status") == "in-stock")
        tg_send(cid, "No matching in-stock now. (%d in-stock overall, last check %s)\n%s" % (
            n_all, STATE.get("last_check"), watch_summary(cid)))
        return
    tg_send(cid, format_alert(rows, STATE.get("last_check"), True, w.get("show_stores", False)),
            parse_mode="HTML", preview=False)


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
    if data == "menu:stores":
        w = get_watch(cid)
        w["show_stores"] = not w.get("show_stores", False)
        save_watch()
        tg_send(cid, ("Pickup locations shown. " if w["show_stores"] else "Pickup locations hidden. ") +
                watch_summary(cid))
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
        cur = get_watch(cid)
        WATCH[cid] = {"models": norm_all(sorted(d["models"])), "storages": norm_all(sorted(d["storages"])),
                      "colors": norm_all(sorted(d["colors"])), "stores": norm_all(sorted(d["stores"])),
                      "muted": cur.get("muted", False), "show_stores": cur.get("show_stores", False),
                      "seen": cur.get("seen", {})}
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


_HOLDING_MARKERS = ('"cv" : "preorder"', '"cv":"preorder"', "shldUrl", "refreshInterval")


def is_preorder_holding(r):
    """Apple serves HTTP 503 with its preorder shield page to every client
    (real browsers included) until the store opens. Not an error — keep polling."""
    if r.status_code != 503:
        return False
    try:
        body = r.text
    except Exception:
        return False
    return any(m in body for m in _HOLDING_MARKERS)


def fetch_stock(parts):
    """Returns (data, err, holding)."""
    global SESSION
    params = [("parts.%d" % i, p) for i, p in enumerate(parts)]
    params += [("searchNearby", "true"), ("store", CONFIG.get("anchor_store", "R428"))]
    err = None
    for url in (API, FALLBACK_API):
        for _attempt in (1, 2):
            try:
                r = SESSION.get(url, params=params, timeout=20)
                if is_preorder_holding(r):
                    return None, None, True  # store not open yet; do not back off
                if r.status_code in (403, 429, 503) or r.status_code == 541:
                    err = "%s: http %s" % (url, r.status_code)
                    SESSION = new_session()  # fresh fingerprint before retry
                    time.sleep(random.uniform(8, 15))
                    continue
                r.raise_for_status()
                return r.json(), None, False
            except Exception as e:
                err = "%s: %s" % (url, e)
                break
    return None, err, False


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
    global SESSION, POLL_COUNT
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
        data, err, holding = fetch_stock(parts)
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        STATE["last_check"] = now
        if holding:
            # Store not open yet — not our fault, don't alert or back off hard,
            # so we catch the moment pre-orders go live.
            STATE["holding"] = True
            STATE["fails"] = 0
            STATE["error"] = "Apple store holding (pre-order not open yet)"
        elif err or data is None:
            STATE["holding"] = False
            STATE["fails"] += 1
            STATE["error"] = err or "parse failed"
            n = STATE["fails"]
            if n == 5:
                broadcast("HK bot: Apple API failing 5x in a row (%s)" % STATE["error"])
            wait = min(2 ** min(n, 4) * 60, 8 * 60)
            if any(k in STATE["error"] for k in ("541", "403", "429")):
                wait = min(wait * 2, 15 * 60)  # Apple shield cooldown needs longer
            backoff_until = time.time() + wait
        else:
            rows = parse(data, parts)
            if rows is None:
                STATE["holding"] = False
                STATE["fails"] += 1
                STATE["error"] = "unexpected Apple response shape"
            else:
                STATE["holding"] = False
                STATE["fails"] = 0
                STATE["error"] = None
                POLL_COUNT += 1
                if POLL_COUNT % 40 == 0:
                    SESSION = new_session()  # rotate fingerprint while healthy
                STATE["rows"] = rows
                for r in rows:
                    PREV[(r["part"], r["store"])] = r["status"]
                n = send_flips(rows, now)
                if n > 0:
                    print("IN STOCK alert sent to %d watcher(s) (%s)" % (n, now), flush=True)
                    if CONFIG.get("open_browser_on_stock"):
                        try:
                            webbrowser.open(BAG)
                        except Exception:
                            pass
                LOG.parent.mkdir(exist_ok=True)
                with open(LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": now, "rows": rows}) + "\n")
        interval = CONFIG.get("interval_sec", 35) + random.uniform(-12, 12)
        if holding:
            interval = max(interval, 50)  # gentler while shielded, still catches the drop
        time.sleep(interval)


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
