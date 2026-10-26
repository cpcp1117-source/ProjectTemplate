# TEMPLATE-001 Round 9 Review

## Verdict

Not Ready. 原生 Windows host 執行完整測試：173 項，171 通過、1 失敗、1 略過。已進入使用者授權的下一輪修正，Task ID 不變。

## Findings by Priority

1. **Major / Engineering Standards / Claude** — 程序清理仍不安全。`test_child_holds_pipe_after_parent_result` 實測 5.14 秒，超過 4 秒界限；adapter 先關閉正在被 reader thread 讀取的 pipe，造成等待。worker 的 `_kill_proc_tree` 忽略所有錯誤，main 仍無條件釋放 lock。必須共用自有程序樹的有界清理機制，失敗保留 lock；以孫程序持有 pipe、取消、逾時、清理失敗驗證。影響後續重試與所有檔案驗收。
2. **Major / Spec Fidelity / Claude** — task snapshot 缺少時仍讀原始檔、prompt_file 的字串前綴檢查可接受同前綴兄弟路徑、dirty hashes 未完整處理 CJK／刪除且未進入公開 baseline。必須拒絕缺失或不符快照，正確路徑包含檢查與 NUL 分隔 Git 路徑處理。影響任務範圍與版本綁定驗收。
3. **Major / Engineering Standards / Claude** — App Server 完成通知未比對 thread/turn；部分伺服器請求被忽略，Codex result_text 未儲存。需比對 ID、明確拒絕未知請求、保存最終回答；基本事件順序測試已改善。影響背景審核結果可信度。
4. **Resolved / Claude** — PowerShell 註解誤判的舊測試已通過。
5. **Partial / Claude** — README 已改為 controller 主導流程；程序安全的保證須等實作與獨立驗證成立後更新。

## Evidence and Handoff

Baseline HEAD `a6a6075741ec8668b2610268b3887e20e8b4d888` 加 round9 工作目錄變更。原始輸出 `G:/AI Collaboration/work/round9-tests.log`；修正交接 `G:/AI Collaboration/work/round10-required-corrections.md`。本輪不發布、不合併、不覆寫歷史驗收。
