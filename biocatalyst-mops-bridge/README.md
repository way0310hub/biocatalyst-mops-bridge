# BioCatalyst MOPS Bridge

這個專案每 30 分鐘抓取 TWSE／TPEx 官方公開重大訊息，產生 `public/mops.json`，供 BioCatalyst TW 讀取。

## 啟用方式

1. 將本專案檔案上傳到 `way0310hub/biocatalyst-mops-bridge` 的 `main` 分支。
2. 在 GitHub 的 **Actions** 分頁執行 `Update MOPS data` 一次，確認流程成功。
3. 若要讓網站直接讀取 JSON，Repository 或資料發布位置必須是網站可公開讀取的網址；私人 Repository 的 raw 檔案不能直接給網站匿名讀取。

## 資料原則

- 來源為 TWSE／TPEx Open Data。
- 單一市場來源失敗不會清空既有資料。
- 每筆公告包含公司代號、公告日期、標題、來源與 MOPS 連結。
- 目前已接入上市與上櫃重大訊息；興櫃端點需待 TPEx 公開 API 提供可驗證的對應資料集後再加入。
