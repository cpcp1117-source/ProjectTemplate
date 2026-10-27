# Skill: verify-and-review

Version: 1.0
Host Owner: Codex
Claude Entry: 準備產物供審查，不自我核准

## Purpose

由 Codex 執行獨立驗證與程式碼審查（步驟 9–10）。Claude Code 的成功宣告不能替代本技能的執行（Rule 4）。

## When to Use (Codex)

- `tasks/current_task.md` 狀態為 `Ready for Review` 時。
- 已收到完整 Handoff Contract 與執行結果時。

## When to Use (Claude)

- 準備供審查的產物（tests、docs、execution-result）時。
- 不得用本技能自我核准實作成果。

## Trigger Phrases

- "verify and review"、"獨立驗證"
- "code review"、"程式碼審查"
- "review findings"、"審查 findings"

## Procedure (Codex)

### 1. 讀取規範與任務

讀取 `docs/ai-collaboration-workflow.md`、`docs/project-brief.md`、`tasks/current_task.md` 與相關規格。

### 2. 核對 Baseline

確認審查版本（commit hash 或 manifest）與 Handoff Contract 中的 Baseline 一致。

### 3. 執行獨立驗證

執行 Verification Commands；記錄每個命令、Exit Code 與輸出摘要。未能執行標示 Not Run 並說明原因。

### 4. 審查實作

- 依 Acceptance Criteria 逐項查核。
- 檢查正確性、regression、安全性、可維護性、可及性與測試覆蓋率。
- 區分確認缺陷、風險、假設與選擇性改善。

### 5. 分類 Findings

使用 `docs/templates/qa-review.md`；每項包含：

| 欄位 | 說明 |
|---|---|
| Finding ID | F-<task_id>-<N> |
| Severity | Critical / Major / Minor |
| Location | 精確的檔案與行號 |
| Impact | 對功能、安全或可維護性的影響 |
| Fix Expected | 修正預期說明 |
| Verification Method | 如何驗證修正 |
| Owner | Claude Code |

### 6. 設定 Verdict

- **Ready**: 所有 AC 通過，無 Critical/Major。
- **Ready with Conditions**: 有條件；需明確條件與允許的下一步。不能自動視為可發布。
- **Not Ready**: 有未解決的 Critical 或 Major。

### 7. 儲存審查報告

儲存至 `reports/review_reports/<task_id>-review-round<N>.md`，並回傳摘要。

## Constraints

- 不得靜默改寫實作（Rule 2）。
- Critical/Major 未解決不得通過。
- 審查報告必須綁定 Baseline（Rule 5）。
- 不得自行派生其他 agent（Rule 8）。
