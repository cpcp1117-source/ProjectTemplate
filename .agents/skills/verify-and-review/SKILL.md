---

name: verify-and-review
description: Codex 執行獨立驗證與程式碼審查，Claude Code 的成功宣告不能替代本技能執行。
---

# Verify and Review

## Invocation

當 `tasks/current_task.md` 狀態為 `Ready for Review` 且已收到完整 Handoff Contract 時使用。

Trigger phrases: "verify and review"、"獨立驗證"、"審查實作"

## Procedure

讀取並遵循完整程序：`docs/skills/verify-and-review.md`

## Restart / Reload

如 Codex 會話重啟，讀取 `tasks/current_task.md` 與最新 `reports/review_reports/` 確認審查進度。

## Constraints

- Codex 為本 skill 的 Owner；Claude Code 只準備產物，不自我核准。
- 不得靜默改寫實作；審查報告必須綁定 Baseline。
- 詳細規則見 `docs/ai-collaboration-workflow.md`。
