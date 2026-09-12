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
3. Alerts fire only on matching in-stock; `/stock` shows matching now; `/mute` `/unmute` `/reset`
4. Token lives in local `config.json` (gitignored) — commit only `config.example.json`

## Release day 12/9/2026
- PC never sleeps, start.bat running, dashboard open
- Safari/Chrome pre-logged into Apple ID, card saved, bag pre-filled
- Telegram unmuted; on alert tap bag link and Apple Pay within seconds

## Notes
- All 32 SKUs preloaded (ZA/A HK models), English labels only
- Uses HK English store only (apple.com/hk/shop), UTF-8 everywhere
- Primary endpoint is pickup-message (works, tested); fulfillment-messages is fallback
- Pre-order 8pm HKT Sept 12, on sale Sept 18, limit 2 Pro + 2 Pro Max per customer
