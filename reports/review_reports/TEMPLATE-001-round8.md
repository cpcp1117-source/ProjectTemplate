# TEMPLATE-001 Round 8 Review

## Verdict

Not Ready. Claude 背景回覆路徑已通過真實測試；Codex App Server 路徑仍逾時，完整自動測試尚有一項失敗。未發布、未合併。

## Reviewed Version

Baseline commit: `a6a6075741ec8668b2610268b3887e20e8b4d888`，加上目前工作目錄變更。逐檔 SHA-256 見 `reports/test_reports/TEMPLATE-001-round8-manifest.json`。此裁定僅適用該版本。

## Validation Evidence

- 原生 Windows host：`python -X utf8 -m unittest discover -s tests -v`，162 項，160 通過、1 失敗、1 略過。原 sandbox 的 taskkill 錯誤在 host 未重現。
- Claude 真實背景工作 `cd91c729d5428d86`：completed，result_text 為 `CONTROLLER_SMOKE_OK`，已驗證 start/status/result。
- Claude 真實寫檔工作 `c2e3d2d30136daf5`：completed，last_tool 為 Write，result_text 為 `CONTROLLER_WRITE_OK`；獨立讀取 `.collaboration/controller-proof.json` 確認 `actor=claude`、`status=ok`、`task_id=TEMPLATE-001`。該驗證產物已存在，保持 gitignored。
- Codex App Server 真實工作 `ff8dfee1c5207923`：成功建立 thread 與 turn，90 秒後 failed，error 為 `codex turn ended with status: interrupted`。
- Raw logs/state 保存在 gitignored `.collaboration/controller/jobs/`，測試原始輸出在 `G:/AI Collaboration/work/round8-tests.log`。不將原始登入與 session 資料提交 Git。

## Findings

| Severity | Owner | Finding | Required Fix |
|---|---|---|---|
| Major | Claude | `_appserver_adapter.py` 的 `turn/completed` 分支設定 terminal_status 後 continue；正常先收到 turn/start response 時，後續完成通知不會 return，最終逾時。實際逾時已重現；通知接收本身未寫入原始協定 log，不能宣稱已由 log 證明因果。 | 收到對應 thread/turn 的完成通知後立即回傳；保留 response-before-notification 與 notification-before-response 的測試。 |
| Major | Claude | Claude worker 的 stderr 收尾與 `proc.wait()` 沒有 deadline，取消/逾時只 kill 直接子程序，未證明整個自有程序樹清理完成。 | 全生命週期使用共同 deadline，清理與 pipe reap 必須有界，終態與解除 lock 前確認自有程序清理；加入失效情境測試。 |
| Major | Claude | start 驗證 task 後 worker 重新讀取原始 task_file，沒有不可變任務快照；版本 manifest 只有路徑清單，沒有 dirty content hash。 | 開始時建立任務快照及內容 hash；worker 執行快照；保存 dirty 內容 hash 以綁定驗收版本。 |
| Minor | Claude | 唯一失敗測試在 `invoke_probe.ps1` 註解內仍含 `Invoke-Expression` 字串。 | 修正註解或以能辨識實際語法的測試檢查危險呼叫，保持原本防護意圖。 |
| Minor | Claude | README 仍以舊 bidirectional probe 為主要入口；controller 指南尚未整合到預設使用流程。 | 更新 README 為 Codex chat 主導與 host controller 的實際流程，保留歷史失敗證據。 |

## Review Axes

- Spec Fidelity: 部分完成；單一 Codex 入口可派發 Claude 並取回結果，完整 controller 尚未可驗收。
- Engineering Standards: 官方協定基本 handshake 已修正；完成事件與有界程序清理仍需修正。
- Validation Evidence and Operational Risk: 已有真實 Claude Pass 與 Codex timeout；mock tests 不足以覆蓋真實事件順序。

## Recovery

保存目前工作目錄與所有既有變更；不以更換 Task ID 重設修正輪數。依 `G:/AI Collaboration/COLLABORATION.md` 第 7 條，兩輪後停止自動重試；新增修正輪數需要使用者明確調整。下一輪先核對 manifest 與既有 job 終態，再由 Claude 修正、Codex 獨立驗證。
