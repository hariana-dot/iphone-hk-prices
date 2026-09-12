import json, re, time, urllib.parse, sys
import requests

BASE = "https://www.apple.com/hk-zh/shop/buy-iphone/iphone-18-pro/"
SIZES = [("6.9", "Max"), ("6.3", "Pro")]
CAPS = ["256gb", "512gb", "1tb", "2tb"]
COLORS = ["黑色", "銀色", "冰川色", "布根地紅色"]

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"})

out = {}
for size, model in SIZES:
    for cap in CAPS:
        for color in COLORS:
            raw = f"{size}-吋顯示器-{cap}-{color}"
            url = BASE + urllib.parse.quote(raw)
            label = f"18 {model} {cap.upper()} {color}"
            try:
                r = s.get(url, timeout=25)
                m = re.search(r'"sku":"([A-Z0-9]+ZA/A)"', r.text)
                sku = m.group(1) if m else None
                print(("OK  " if sku else "MISS") + f" {label} -> {sku}", flush=True)
                if sku:
                    out[sku] = label
            except Exception as e:
                print(f"ERR  {label}: {e}", flush=True)
            time.sleep(1.5)

with open("skus.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\nSaved {len(out)}/32 to skus.json", flush=True)
