---
name: remediate-findings
description: Claude 收到 Codex 的 Finding IDs 或 Changes Requested 時，依核准範圍修正並準備複驗證據。
---

# Remediate Findings

從專案根目錄讀取 `docs/ai-collaboration-workflow.md` 與 `docs/skills/remediate-findings.md`，依共同程序的 Claude 角色執行。

Owner 是 Claude。保存原 Task ID 與修正輪數，逐項提供修正或有證據的異議；達兩輪上限時停止並呈報。修正完成回傳提交，不自行宣告 Codex 已核准。
