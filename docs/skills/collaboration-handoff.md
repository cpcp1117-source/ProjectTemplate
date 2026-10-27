# Skill: collaboration-handoff

Version: 1.0
Hosts: Claude Code, Codex

## Purpose

在 Claude Code 與 Codex 之間執行標準化的任務交接。確保每次交接都包含完整的 Handoff Contract 欄位（Rule 3），並綁定 Baseline（Rule 5）。

## When to Use

- Claude Code 完成實作，準備交 Codex 審查時。
- Codex 完成審查，準備回傳 findings 給 Claude Code 時。
- 任一方在會話邊界結束時，需保留狀態供下一個會話繼續時。

## Trigger Phrases

- "handoff to Codex"、"交 Codex 審查"
- "handoff to Claude"、"交 Claude 修正"
- "prepare handoff"、"準備交接"

## Procedure

### 1. 讀取規範

讀取 `docs/ai-collaboration-workflow.md`，確認八項規則與當前步驟。

### 2. 確認 Baseline

記錄當前 commit hash（`git rev-parse HEAD`）或髒檔案的 hash manifest。

### 3. 填寫 Handoff Contract

使用 `docs/templates/task-handoff.md` 或直接更新 `tasks/current_task.md`，填寫：

- Task ID
- Stage（目前進行的步驟）
- Owner / Reviewer
- Baseline（commit + 髒檔案 manifest）
- Inputs（輸入路徑與版本）
- Allowed Paths / Forbidden Paths
- Acceptance Criteria（含驗證方式）
- Verification Commands
- Timeout
- Revision Round（目前輪數 / max: 2）
- Expected Outputs

### 4. 填寫 Execution Result

使用 `docs/templates/execution-result.md` 記錄：

- Files Changed
- Artifact Paths
- Verification Results
- AC Coverage
- Known Limitations
- Items Not Completed

### 5. 儲存至 tasks/reports/

將執行結果檔案儲存在 `tasks/reports/<task_id>-round<N>.md`，不覆蓋 `tasks/current_task.md` 的空白範本。

### 6. 更新 current_task.md Status

設定 Status 為 `Ready for Review`（Claude → Codex）或 `Changes Requested`（Codex → Claude）。

### 7. 確認 Revision Limit

如果本次為第 2 輪修正且仍 Not Ready，停止並呈報；不得換 Task ID 規避上限。

## Constraints

- 不得共用會話 ID 或 `resume --last`。
- 受派工方不得再委派其他 agent（Rule 8）。
- 快照變更後若影響驗收，需重新複驗（Rule 5）。
