# iPhone 18 Pro / Pro Max HK stock bot (local, English only)

Monitor + Telegram alert + web dashboard. No auto-buy: no open-source tool
achieves unattended Apple ID 2FA + Apple Pay checkout.

## Run
1. Double-click `start.bat`
2. Open http://localhost:9120 (same PC) or http://YOUR-PC-IP:9120 (phone on same wifi)

## Web GUI
- Table of all 32 variants x 6 HK stores, auto-refresh every 10s
- Filter: All / In stock only / Pro only / Pro Max only
- Beep toggle on stock; bot also auto-opens the bag page on your PC
- `Last checked` + error line shows Apple API health

## Telegram (@iphone18hk_bot)
1. Message the bot `/start` — you are auto-registered with default All/All/All/All
2. `/watch` wizard: Model → Storage → Color → Pickup, multi-select taps, default All, tapping All clears other picks in that group (All-wins)
3. Alerts fire once per variant when it flips out-of-stock → in-stock (all stores count as one pool; staying in-stock or going out-of-stock never alerts); `/stock` shows matching now; `/mute` `/unmute` `/reset`; `/pickup on|off` shows/hides store lists (hidden by default)
4. Buy links open the pre-selected config in the browser (deleted Store app recommended); then tap: No trade-in → Continue → No AppleCare+ → Add to bag → pickup (trade-in/AppleCare can't be pre-linked)
5. Token lives in local `config.json` (gitignored) — commit only `config.example.json`
6. Groups: works if you add the bot to a group (one shared filter per group, commands + buttons fine), but each friend is better off starting their own chat with the bot so everyone keeps a personal filter; BotFather `/setprivacy` → DISABLE if the group should hear non-command chatter (not needed today)

## Release day 12/9/2026
- PC never sleeps, start.bat running, dashboard open
- Safari/Chrome pre-logged into Apple ID, card saved, bag pre-filled
- Telegram unmuted; on alert tap bag link and Apple Pay within seconds

## Notes
- All 32 SKUs preloaded (ZA/A HK models), English labels only
- Uses HK English store only (apple.com/hk/shop), UTF-8 everywhere
- Primary endpoint is pickup-message (works, tested); fulfillment-messages is fallback
- Pre-order 8pm HKT Sept 12, on sale Sept 18, limit 2 Pro + 2 Pro Max per customer
- **Anti-block:** Apple's Akamai shield returns HTTP 541 to `python-requests` (TLS fingerprint) ahead of pre-orders. Apple calls go through `curl_cffi` impersonating real Chrome 124; UA pool rotates, session is rebuilt every 40 healthy polls and after any 403/429/541, and poll jitter is ±12s. 403/429/541 back off up to 15 min.
- **Pre-order holding:** until the store opens Apple serves HTTP 503 with its `cv:preorder` shield page to *every* client (real browsers included). The bot detects this, does not alert or count it as failure, keeps polling, and never backs off — so it catches the drop the moment pre-orders go live. Dashboard shows `Apple store holding (pre-order not open yet)`.
