---

name: collaboration-handoff
description: 在 Claude Code 與 Codex 之間執行標準化任務交接，填寫完整 Handoff Contract 並綁定 Baseline。
---

# Collaboration Handoff

## Invocation

當需要將任務交給 Claude Code 或從 Claude Code 接收交接時使用。

Trigger phrases: "handoff to Claude"、"準備交接"、"交 Claude 修正"

## Procedure

讀取並遵循完整程序：`docs/skills/collaboration-handoff.md`

## Restart / Reload

如 Codex 會話重啟，讀取 `tasks/current_task.md` 確認 Stage 與 Revision Round，再繼續交接程序。

## Constraints

- 受派工方不得再委派其他 agent（Rule 8）。
- 詳細規則見 `docs/ai-collaboration-workflow.md`。
