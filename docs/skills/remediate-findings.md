# Skill: remediate-findings

Version: 1.0
Host Owner: Claude Code
Codex Entry: 準備 findings 並路由，不直接修改實作

## Purpose

由 Claude Code 逐項修正 Codex 審查 findings（步驟 12）。在 max: 2 輪限制內完成修正與複驗（Rule 7）。

## When to Use (Claude Code)

- `tasks/current_task.md` 狀態為 `Changes Requested` 時。
- 已收到 Codex 的審查報告與 Verdict 時。

## When to Use (Codex)

- 準備 findings 清單並路由至 Claude Code 時。
- 不得使用本技能直接修改實作。

## Trigger Phrases

- "remediate findings"、"修正 findings"
- "address review"、"處理審查意見"
- "fix findings"、"回應審查"

## Procedure (Claude Code)

### 1. 讀取審查報告

讀取 `reports/review_reports/<task_id>-review-round<N>.md`；確認 Baseline 與 Revision Round。

### 2. 確認修正範圍

- 只處理人工選定或明確需要驗收的 findings。
- Minor findings 不阻擋交付，除非與 AC 直接相關。
- 選擇性改善移至 `tasks/backlog.md`。

### 3. 確認 Revision Round 未超限

- 如果已是第 2 輪（revision_round == 2）且 Verdict 仍 Not Ready，停止並呈報；不換 Task ID。
- 如需繼續，由使用者明確調整輪數上限。

### 4. 逐項修正

對每個需修正的 finding：

1. 定位精確的檔案與位置。
2. 實作修正；保持變更範圍精確。
3. 記錄修正方式與驗證證據。
4. 如不同意，附證據提出異議（由 Codex 複核；無法解決的取捨交使用者決定）。

### 5. 更新 Baseline

修正後記錄新的 commit hash 或髒檔案 manifest。

### 6. 執行驗證命令

執行 Verification Commands；記錄 Exit Code 與結果。測試未執行不得宣稱通過（Rule 4）。

### 7. 填寫執行結果

使用 `docs/templates/execution-result.md`，填寫 Findings Addressed、Revision Round 更新後狀態。

### 8. 使用 collaboration-handoff 交回 Codex

遵循 `docs/skills/collaboration-handoff.md` 完成交接；設定 Status 為 `Ready for Review`。

## Constraints

- 修正後快照變更影響既有驗收時，通知 Codex 重新複驗（Rule 5）。
- 不得自行派生其他 agent（Rule 8）。
- 不得換 Task ID 規避 revision 輪數限制（Rule 7）。
