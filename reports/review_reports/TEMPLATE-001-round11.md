# TEMPLATE-001 Round 11 Review

## Verdict

Not Ready — 正常合作與主要程序樹清理已改善；清理失敗的鎖管理仍未通過。使用者已授權持續修復，接續下一輪，不因輪數再次要求授權。

## Evidence

- 原生完整套件：200 項，198 通過、1 error、1 skip。錯誤為 `test_initialize_sends_client_info` 讀取尚未完整寫出的 captured.json；adapter 完成後立即終止假伺服器造成競態，需修正正常收尾與測試 fixture 的完成訊號順序。
- Reviewer-owned 獨立測試：孫程序持有管線案例已通過；注入清理不確定時，失敗 CLI 仍未保存 cleanup_required，任務鎖遭釋放，案例失敗。
- 真實 Claude 寫檔工作 `de1f79433881575e`：completed，回覆 CLAUDE_ROUND11_OK；產物 JSON 符合要求。F10-01 正常成功回覆不再被誤殺。
- 真實背景 Codex 審核 `6ab7f6de24bedc8e`：completed，讀取 Claude JSON 並回覆 CODEX_REVIEW_ROUND11_OK；結果含 commentary 與重複 final，需去除。
- 真實 Claude 取消 `14296b5d956ed204`：cancelled；逾時 `81ddebff5c3be276`：failed，明確 timeout after 0.05s；後續任務可開始且 lock 解除。
- 原始輸出在 `G:/AI Collaboration/work/round11-tests.log`、`round11-independent.log`、`round11-integration.log`；實作紀錄 `round11.stream.jsonl`。原始 session／登入紀錄不納入 Git。

## Ordered Follow-up

1. **Major / Claude:** cleanup_uncertain 在例外路徑未寫入 state，main 仍可釋放 lock；App Server 的清理結果仍被忽略。影響失敗後安全重試。以注入 cleanup failure 的真實工作者流程驗證所有路徑保留鎖，並修復 terminal+cleanup_required 的 recover。
2. **Major / Claude:** ProcessOwner.terminate 的例外 fallback 仍以父程序退出當整樹停止；spawn 失敗清理的未知結果需保留。影響程序狀態可信度。未知時必須回傳 uncertain，避免假成功。
3. **Minor / Claude:** Codex 最終回覆混入 commentary 且重複。依 phase=final_answer 與 item ID 去重；驗證單一 token 與 JSON 可直接解析。
4. **Minor / Claude:** result.baseline 尚無 dirty-content Hash；提供快照來源與版本 Hash，便於驗收追蹤。
5. **Minor / Claude:** README／validation 文件須與實際已驗證 controller gate 一致，保留 legacy bidirectional 證據並清楚標記用途。

## Next Gate

先修復例外鎖管理與恢復，再驗證結果格式、完整套件、獨立失敗情境、真實兩方合作與乾淨專案演練。未 commit／發布。
