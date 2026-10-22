# Consulting OS

## Purpose

此擴充層用於 discovery、industry research、process analysis、solution design、PRD、technical architecture、quality review 與 knowledge capture。一般程式修改不必啟用完整顧問流程。

## Operating Principles

- 從 business problem 出發，不預設 AI 是答案。
- 優先選擇最簡單且有效的解法。
- 明確區分 Facts、Assumptions、Unknowns、Risks、Recommendations。
- 使用完成任務所需的最少 agents 與文件。
- 內容預設繁體中文；headings、field names、User Stories、Acceptance Criteria 與 handoff structures 保留英文。

## Workflow Levels

| Level | Use When | Suggested Flow |
|---|---|---|
| Level 1 | 單點釐清或研究 | 1-2 個 specialist agents |
| Level 2 | 方案或規格交付 | Orchestrator + 必要 specialists + Quality Reviewer |
| Level 3 | 端到端顧問交付 | 完整必要鏈路 + Quality Review + Knowledge Capture |

## Agent Delegation

- `orchestrator` 負責分類、選角、排序、整合與 handoff，不代替 specialist 產出完整專業文件。
- Specialist 必須遵守 `.agents/` 的責任邊界。
- Agent 輸出衝突時，列出衝突、trade-off 與建議決策路徑。
- Consulting OS 不改變固定工程角色：Claude Code 實作，Codex review。

## Explicit-Only Subagent Delegation

- 只有任務明確授權（包含對象、範圍與界限）時，才可委派 subagent。
- 受委派的 subagent 不得自行再委派其他 agent（Rule 8：No Recursive Delegation）。
- 沒有明確的任務授權，任何一方不得自行形成 agent 鏈。
- 委派後，受派工方需回傳結果至主控方，由主控方決定下一步。

## Solution Order

依序考慮 Process Improvement、Paper-to-System、Data Standardization、System Workflow、Dashboard、Rule-based Automation、Human Review、Integration、AI Assistance、AI Agent。

只有需要語言理解、摘要、分類、模糊推理、knowledge retrieval、unstructured data、跨系統 tool use 或 semi-autonomous execution 時才採用 AI。

## Quality Standard

交付物需可被下一位 agent、PM 或 engineer 直接使用，並清楚標示 assumptions、owners、risks、open questions 與 next steps。Quality Reviewer 回傳 `Ready`、`Ready with Conditions` 或 `Not Ready`。

## Knowledge Capture

Level 2 或 Level 3 結束時，由 `orchestrator` 使用 `docs/templates/knowledge-capture.md` 彙整可重用知識。不得未經 human 確認直接覆寫 templates 或 domain packs。
