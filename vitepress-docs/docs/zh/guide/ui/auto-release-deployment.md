# 正式 Release 自動部署（PoropsK fork）

此 fork 使用 `cfmail Stable Release Update` 更新既有 Worker `mail-api` 和 Direct Upload Pages `cfmail`。控制檔為 `.github/config/cfmail_release.json`，已驗收基準是 v1.12.0／D1 v0.0.9。它不執行上游同步、不初始化或遷移 D1，也不寄測試郵件。

## 更新條件

排程為每天每個整點檢查一次（每小時第 0 分鐘，台北時間 00:00、01:00……23:00；`0 * * * *`）。GitHub 預設按 UTC 解讀排程；台北與 UTC 相差整數小時，因此兩者整點對齊。只有非 draft、非 prerelease 的 `vX.Y.Z` Release 可成為候選；先解析 tag 到固定 commit SHA。以 Pages 正式 deployment 的 commit hash 記錄已部署原始碼，並核對 Worker 版本、D1 版本及已核准基準。Worker 必須是已驗收的初始 version ID，或帶有對應 tag／SHA 註記的後續發布；未盤點的控制台修改及分流部署會停止。

以下變更可自動通過：文件、CSS／樣式、指定副檔名的圖片／字型、Vue 元件僅 style 內容變更，以及 package／Worker 常數的單純版本號更新。候選 `.github/workflows/` 不會被執行或覆蓋本 fork 的控制流程。

DB／SQL／遷移、認證／收信／API 邏輯、模板／script、相依套件／lockfile／build script、binding／設定、未知檔案、symlink／可執行檔、tag 重寫／版本降級、來源或版本不一致、資料庫需更新，均停止。即使 DB_VERSION 不變，DB／遷移檔變動也會停止。這些情況需要獨立盤點、備份與驗證，再更新控制政策，不能直接跳過門檻。

## 設定與執行

沿用六個 repository Secrets：`CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_API_TOKEN`、`BACKEND_TOML`、`FRONTEND_NAME`、`FRONTEND_BRANCH`、`FRONTEND_ENV`。值必須符合控制檔；BACKEND_TOML 使用 `keep_vars=true` 且沒有 vars，以保留 Dashboard 的 JWT／密碼。不要把 Token 放入原始碼。

repository Variable `CFMAIL_AUTO_RELEASE_ENABLED=true` 才允許正式發布。發布同時要求 workflow 在 `main`、有合格新版、且不是 push／check-only。此變數關閉時仍可查核，但不發布。控制政策與正式排程需放在 main；main 保存控制流程基準，候選應用程式從上游固定 SHA checkout，不用 gh repo sync 整包覆盖。

到 Actions → **cfmail Stable Release Update** → **Run workflow**：

1. Branch 選 main。
2. 保留 `check_only=true` 時只查核／建置；`validate_build=true` 可在沒有新版時也驗證建置。
3. 要立即按相同規則更新，可取消 check_only；不會繞過任何門檻。
4. 在 run Summary 查看 `no_update`、`eligible` 或 `blocked` 與 reason。`no_update` 不會重發現有版本。

發布先用 frozen lockfile、Node 24／pnpm 10.10.0 建置，安裝及建置步驟沒有部署 Token。發布前再次 GET 核對來源與正式狀態，再發布 Worker；原 vars／JWT／bindings、D1 與版本通過後才發布 Pages。Pages 帶相同候選 commit hash，最後比對自訂網域及 deployment URL 的 JS 與本次建置完全相同。單一 concurrency 群組限制正式發布並行。

舊 Deploy Backend／Deploy Frontend、page function 及 Upstream Sync 在此 fork 保持停用，避免繞過控制或從旧分支重發；新 workflow 提供手動入口。自動流程不更新 Dashboard 變數、binding 或相容日期；未知配置會停止。

## 停止與回復

需要資料庫或未知程式變更時，查閱 reason，固定該 Release SHA，重新盤點與演練後處理；不要按資料庫初始化。後端發布成功、前端失敗時，下一次會因版本不一致停止，不盲目續跑或回復資料。先查核實際兩端狀態，再決定重發或回退程式。

關閉自動更新：Settings → Secrets and variables → Actions → Variables，將 `CFMAIL_AUTO_RELEASE_ENABLED` 改為 `false`；也可在 Actions 停用這個 workflow。Worker rollback 不會還原 D1／KV 資料。現有正式備份含郵件與金鑰，只保存在私有目錄。

GitHub 排程可能延遲，整點是官方列出的高負載時段，部分排程可能被丟棄；設定整點不保證準時啟動。[官方 schedule 說明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。公開 repo 60 天沒有活動可能被停用。定期查看最近一次成功 run，沒有新版本也應有 `no_update` 結果。本流程檢查版本與 DB health，但不代替每次重大升級的舊信／實際收信驗收。
