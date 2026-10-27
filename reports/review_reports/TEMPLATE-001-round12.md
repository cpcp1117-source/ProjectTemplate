# TEMPLATE-001 Round 12 Review

## Executive Verdict

Not Ready. 真實核心合作與獨立清理測試通過；尚有一個 shipped test 注入位置錯誤，及 ProcessOwner 例外判斷／PID 身分恢復缺口。

## Validation Evidence

- 完整套件：209 項，207 通過、1 failure、1 skip。失敗 `test_cleanup_uncertain_sets_cleanup_required_on_failure` patch 到 `_controller_process.ProcessOwner`，但 worker 改成模組層級匯入，實際注入點為 `_controller_worker.ProcessOwner`；需同步 fixture，保留原斷言。
- Reviewer-owned 測試已同步注入兩個匯入點，原斷言不變：2/2 通過（孫程序清理、清理不確定時保留鎖）。先前本輪第一次執行的失敗為測試注入未生效，不是該產品路徑再次失敗。
- 真實 Claude 寫檔 `e62db482f93a815a`：completed，精確 CLAUDE_ROUND12_OK。
- 真實 Codex 審核 `21b558cfdc91994f`：completed，精確 CODEX_REVIEW_ROUND12_OK，無 commentary／重複。
- 真實 Codex JSON `b3de3f23ffebb2fe`：completed，可直接 json.loads，等於要求的 status/proof。
- 真實取消 `e6c54ec7c689dff0`：cancelled；真實逾時 `65ff8f5d938d8602`：明確 timeout after 0.05s，lock 解除。
- 原始輸出 `G:/AI Collaboration/work/round12-tests.log`、`round12-independent.log`、`round12-integration.log`，不提交原始 session／登入紀錄。

## Findings by Priority

1. **Major / Claude / Engineering Standards:** ProcessOwner.terminate except 分支仍以父程序退出回傳 (True,False)，違反整群組確認；spawn 失敗的未知清理亦可能遺失。需要只以正向群組證據確認清理，對 API 例外保留 unknown，補失效測試。
2. **Major / Claude / Operational Risk:** worker_gone 的 Windows HANDLE API 尚未完整宣告型別；recover 未記錄／比對程序建立身分，且 pending 無 PID 時可能釋放仍在啟動的工作。需 FILETIME identity、typed API、unknown/live 拒絕恢復與啟動階段保護。
3. **Minor / Claude:** shipped cleanup failure test 需 patch 實際使用的 worker.ProcessOwner；不得移除測試或放寬斷言。
4. **Minor / Claude:** App Server 收到同 thread 的錯誤 turn 完成通知若先於 turn/start response，仍可能污染當前結果；需 buffer 後與 response turn ID 核對。補反例。

## Re-review Status

F10-01 正常任務誤殺：Fixed；F10-02 主要程序樹清理與清理錯誤鎖傳遞：Partially Fixed（獨立情境通過，仍有以上 API 例外缺口）；F10-03 結果版本資訊已加入；F10-04 去重與 final phase：Fixed（真實精確文字及 JSON 通過）。文件已標記 legacy，待最後版本演練。

## Required Handoff

Claude 修復上述限定範圍，可自行執行核准的 stdlib unit tests；Codex 再獨立複驗。持續修復授權有效，不再以兩輪上限停止。乾淨新專案演練與最終發布審查在後續 gate。
