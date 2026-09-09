# AGENTS.md — iphone-hk-prices

## Project

- **Name:** iphone-hk-prices
- **Description:** iPhone 香港歷代發售價 bar chart（2008–2026），單一 HTML，用 ECharts 畫橫向 bar chart，手機 Safari 睇到。
- **GitHub:** hariana-dot/iphone-hk-prices (private)
- **Obsidian:** `Projects/iphone-hk-prices`

## Folder structure

```text
iphone-hk-prices/
├── index.html   # 圖表本體：MODELS 資料 + ECharts 設定全部 inline，file:// 開到
├── README.md    # 項目說明、價格基準、資料來源
├── AGENTS.md    # 本檔
└── .gitignore
```

## Conventions

- 所有價格資料只放喺 `index.html` 頂部 `MODELS` array（d = 香港發售日，price = HK$ 官方淨機最低容量發售價）。
- 加新型號：喺 `MODELS` 加一行，兩個排序（byYear / byFamily）會自動處理；Pro / Pro Max 同代自動相鄰。
- 改完用瀏覽器開 `index.html` 睇 render + 撳「按系列」掣驗證。
- Commit message 用英文短句。
