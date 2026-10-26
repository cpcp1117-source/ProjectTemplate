# TEMPLATE-001 Round 10 Review

## Verdict

**Not Ready.** 完整套件 186 項、185 通過、1 略過；額外的獨立程序樹測試失敗，真實 Claude 成功任務被誤判 failed。不得將單元測試通過視為完整協作已驗收。尚未 commit、發布或合併。

## Reviewed Version and Evidence

- HEAD `a6a6075741ec8668b2610268b3887e20e8b4d888` 加本輪工作目錄變更；逐檔 SHA-256 綁定於 `reports/test_reports/TEMPLATE-001-round10-manifest.json`。產品檔案在測試至 Claude 完成提交之間的修改時間未變。
- 原生 Windows：`python -X utf8 -m unittest discover -s tests -v`，exit 0，186 項、185 Pass、1 Skip；原始輸出 `G:/AI Collaboration/work/round10-tests.log`。
- Reviewer-owned 獨立測試：`python -X utf8 G:/AI Collaboration/work/test_controller_independent.py`，exit 1。主程序退出、孫程序持有管線後，adapter 等待 **8.30 秒**，不是在清理期限內終止孫程序；輸出 `G:/AI Collaboration/work/round10-independent.log`。
- 真實 Claude 工作 `14a5397cc547cf61`：JSON 實際建立成功；CLI result 為 subtype=success、is_error=False，控制程式卻回報 `Claude task failed: subtype=success is_error=False rc=1`。實際檔案 `.collaboration/controller-round10-proof.json` 經 Codex 讀取確認 actor=claude、status=ok、task_id=TEMPLATE-001、proof=ROUND10。**不可重複提交同一寫檔工作**。
- 真實 Codex App Server 工作 `83a521331b1d8235`：completed，回傳 `APP_SERVER_SMOKE_OK`（目前 adapter 重複收集成兩份）。完成事件不再等到 90 秒逾時。
- Claude 實作程序已正常結束；實作報告的「verified」不能替代以上獨立證據。

## Findings by Priority

### F10-01 — Critical — Successful Claude job forcibly terminated

- **Axis / Owner:** Spec Fidelity、Engineering Standards / Claude。
- **Location:** `scripts/_controller_worker.py` `_run_claude` 成功 result 後的 kill_tree 及 rc 檢查。
- **Fact:** 收到 terminal_event 後無條件 kill_tree，再以非零退出碼判定 failed；真實任務已重現。
- **Impact:** 主要實作路徑會將成功產物標成失敗；若自動或人工重試，可能重複修改檔案。後續 Codex 審核、任務接續與發布均受阻。
- **Resolution:** 成功 result 後先給有界的正常退出與管線收尾時間；只有取消、逾時或退出寬限耗盡才強制清理。保留真正非零退出碼的失敗語意，不能直接忽略 rc。已存在的 JSON 必須先核對再重測，使用新的明確驗證產物。
- **Verification:** 真實 Claude 寫檔需同時滿足產物正確、成功 envelope、正常退出、completed 與無殘留；另測成功 result 後卡住的失效路徑。

### F10-02 — Major — Descendant cleanup and lock release are not proven

- **Axis / Owner:** Engineering Standards、Validation Evidence and Operational Risk / Claude。
- **Location:** `scripts/_controller_process.py` kill_tree；worker main/finally；AppServerAdapter.close。
- **Fact:** taskkill 結果被忽略，只以直接子程序退出判定整樹 terminated；孫程序獨立測試失敗。worker 的 cleanup_uncertain 在 raise 路徑可能未回傳至 main；App Server 清理狀態亦未完整回傳至任務鎖管理。
- **Impact:** 舊程序可能繼續持有管線或執行工作，卻允許下一任務開始，影響取消、逾時、重試與版本驗收。
- **Recommended Resolution:** Windows 以 Job Object 管理本任務所擁有的程序群組，清理後查詢活動程序數；POSIX 保留自有 process group。取得 containment 失敗需停止啟動。清理結果必須在所有成功／錯誤／逾時路徑寫入 state；無法證明停止時保留 lock，recover 不得只看已退出的父程序。替代方案是維護所有後代程序的可靠身份與存活追蹤，但實作與競態處理更繁複。
- **Official Basis:** https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects 說明群組管理、預設子程序關聯、TerminateJobObject、活動程序統計與 KILL_ON_JOB_CLOSE；實作仍需驗證失敗與相容情境。
- **Verification:** 主程序先退出而孫程序仍持有管線、手動取消、逾時、初始化失敗、清理 API 失敗；確認整群組停止才解除 lock，未知時拒絕 start/recover。

### F10-03 — Minor — Version hashes need clearer result exposure

- **Axis / Owner:** Spec Fidelity / Claude。
- **Progress:** 任務快照在啟動前保存；worker 拒絕缺失與 hash 不符，prompt_file 使用正確路徑包含檢查，config 在建立任務前驗證，dirty paths 採 NUL 分隔並處理刪除；snapshot_hash 已回傳。相關測試通過。
- **Remaining:** dirty 檔案的內容 Hash 保存在 task_snapshot，但 result.baseline 仍只有 commit 與檔名清單。建議提供 baseline dirty-content manifest 或清楚的快照來源欄位，便於後續驗收者追查。
- **Verification:** start/status/result 能取得對應版本資訊；修改原始任務不影響執行快照；CJK、刪除與 staged 檔案均可核對。

### F10-04 — Minor — Codex final text collected twice

- **Axis / Owner:** Engineering Standards / Claude。
- **Fact:** 真實 Codex 回覆 `APP_SERVER_SMOKE_OK` 在 result_text 出現兩次；item 與 turn 完成事件皆收集同一回答。
- **Impact:** 使用文字格式契約或 JSON 解碼的後續自動化可能收到重複內容。
- **Resolution / Verification:** 依 item ID 保存完整最終回答並去重，不重複串接；真實指定單一 token 與 JSON 回覆，確認各只出現一次。

## Resolved and Deferred Checks

- 原先 Codex 完成回傳逾時：基本真實回覆已通過；仍有上述重複內容問題。
- PowerShell 註解造成的既有測試失敗：已解決。
- README 與 controller usage 已更新至 Codex 主控流程，但程序安全的保證尚未驗收成立。
- 真實取消／逾時及乾淨新專案完整演練：**Not Run**。正常 Claude 結束路徑已失敗，程序清理亦有確認缺陷，先修復再執行這些 gate。沒有將未跑層級標成 Pass。

## Recovery and Next Action

保存本輪所有變更與既有產物。使用者本次授權內的兩輪修正（round9、round10）已用完，依 `G:/AI Collaboration/COLLABORATION.md` 第 7 條停止自動修正／重試；新增輪數須使用者明確調整。

下一輪先完成 F10-01、F10-02，再修正結果 Hash 暴露與文字去重，最後重跑相同獨立測試、真實合作、取消／逾時及乾淨新專案演練。保留 Task ID TEMPLATE-001，不將初始模型成功回覆當成獨立驗收通過。
