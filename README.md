# iphone-hk-prices

iPhone 香港歷代發售價 bar chart（2008–2026）。

單一 `index.html`，用 ECharts（CDN）畫橫向 bar chart，手機 Safari 直接開到睇：

- 預設由新到舊排列；y 軸係年月 `(2026-Sep)`，長條入面寫型號＋容量
- **按年份 / 按系列**：按系列將全部 Pro 歸一組、全部 Pro Max 歸一組，方便跨代比較
- **系列篩選** chips：只睇某一系列（例如只睇 Pro）
- **二手價** toggle：同系列最新款 × 0.7ⁿ（n = 足年數，上限 7）；8 年或以上車款斜體
- 右上角**淺色 / 深色 / 系統**主題切換（記住選擇）

## 價格基準

香港官方 SIM-free 淨機**發售價（最低容量）**，上台合約價及之後的官方調價不計。
iPhone 2G 從未在港正式發售；摺疊屏iPhone Duo 暫未公佈香港官方定價，故未收錄。

## 點樣睇

直接用瀏覽器開 `index.html`（需上網載入 ECharts CDN）。
想 Send 去 iPhone：AirDrop / Send 檔案過去用 Safari 開即可。

## 資料來源

Apple 香港 newsroom 及當年香港傳媒紀錄（unwire.hk、ePrice.HK、DCFever、MobileMagazine 等），2026-09 核實。
個別早期型號以 Apple HK 網上商店淨機價為準（3G 時代初為 3HK 上台，後有淨機價）。
