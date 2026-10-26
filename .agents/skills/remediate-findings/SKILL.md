---

name: remediate-findings
description: Codex 準備 findings 並路由至 Claude Code；Codex 不直接修改實作。
---

# Remediate Findings

## Invocation

當需要將審查 findings 路由給 Claude Code 修正時使用。

Trigger phrases: "route findings to Claude"、"路由 findings"、"準備修正清單"

## Procedure

讀取並遵循完整程序：`docs/skills/remediate-findings.md`

Codex 在本 skill 中的角色：準備 findings 清單、確認 Revision Round、路由至 Claude Code，不直接編輯實作檔案。

## Restart / Reload

如 Codex 會話重啟，讀取 `reports/review_reports/` 確認最新審查報告，再路由。

## Constraints

- Codex 不得使用本 skill 直接修改實作（Rule 2）。
- 不得自行派生其他 agent（Rule 8）。
- 詳細規則見 `docs/ai-collaboration-workflow.md`。
