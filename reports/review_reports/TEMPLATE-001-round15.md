# TEMPLATE-001 Round 15 Review

## Verdict

Not Ready — 新增身分資訊後，Codex 正常完成路徑未重設 caller cleanup flag，造成正常任務仍持有鎖。

## Evidence

- Claude 自測：當時九項 reviewer acceptance 9/9 Pass；原 shipped suite 217 total、216 Pass、1 Skip。
- Codex 最終獨立 suite（加入 11 個 portable reviewer regressions）：228 total、227 Pass、1 Skip，無失敗。
- 真實主專案 Claude 寫檔與 Codex 精確審核均 completed；下一任務 start 拒絕，因舊 Codex 工作 `c3af68789d15f03d` lock 仍存在。
- 乾淨中文／空白／撇號路徑：初始化與離線預檢 Pass；Claude 寫 JSON 與 Codex 審核都 completed；最後 lock 應不存在的斷言失敗。不是路徑或登入錯誤。
- Source: `_run_codex` 進入 adapter 後將 `_outcome.cleanup_uncertain=True`，清理證明為 True 時 finally 未將它設回 False；outer finally 重新將 cleanup_required=True，保留 lock。
- 原始紀錄已封存於 `G:/AI Collaboration/work/round15-final-tests.log`、`round15-final-integration.log`、`round15-clean-template-acceptance.log`，避免後續複驗覆寫歷史。

## Required Fix

Claude 在每個 `_run_codex` finally 明確回寫正反兩種清理結果；success 必須 state/outcome 一致，unknown 不得 completed 或解除鎖。補正常 Codex completed 後 lock 已釋放的回歸斷言，保留原本 uncertain spawn 斷言。

Codex 接著安全恢復已完成但被錯誤鎖住的工作，保留既有產物與歷史，再重跑串接與新的乾淨副本。不重複寫入同一份已完成產物。
