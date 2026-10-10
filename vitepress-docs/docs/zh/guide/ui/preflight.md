# 部署前查核

本次升級的 `cfmail Deployment Preflight` 位於獨立 `upgrade/preflight-v1.12.0` 分支，只在該分支的查核 workflow 變更時執行。它建置固定 v1.12.0 commit，不改變正式升級分支。

流程使用六個部署 Secrets，唯讀驗證 Cloudflare Token、Account、Worker、Pages、Zone 及路由的存取，確認 DB／KV 與正式目標，再以 Node 24 執行 Worker dry-run 及前端 build。Token 與 Worker 變數值不輸出。

成功只代表指定資源可讀、設定與建置通過；沒有測試寫入權限，沒有執行 D1 遷移或發布。正式升級仍需最新備份、schema 驗證、後端及前端依序發布與功能驗收。
