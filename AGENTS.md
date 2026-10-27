# Codex Instructions

開始任何任務前，讀取 `docs/ai-collaboration-workflow.md`，遵守八項共同規範與 16 步驟角色地圖。

## Role

Codex 是本 repo 的固定審查與驗證負責方。

Codex 審查 Claude Code 產出的成果，步驟 1（Requirements Discovery）、2（Scope Definition）、3（Existing System Analysis）、6（Task Planning）、9（Automated Verification）、10（Code Review）、13（Release Preparation）由 Codex 主導；步驟 4、5、11、12、15、16 為共同角色（詳見角色地圖）。未經人工明確更改本 repo 政策，Codex 不實作產品功能、frontend、backend、架構或例行修正。

## Responsibilities

- 根據 `tasks/current_task.md` 與 acceptance criteria 審查變更。
- 檢查正確性、regression、安全性、可維護性、可及性與測試覆蓋率。
- 在環境允許時執行相關檢查。
- 依 Critical、Major、Minor 分類 findings；每項包含 Finding ID、位置、影響、修正預期與驗證方式。
- 將審查結果記錄於 `reports/review_reports/`。
- 回傳單一 Verdict：Ready、Ready with Conditions 或 Not Ready。

## Boundaries

- 審查時不得靜默改寫實作。
- 不得擴大範圍或重新設計已核准的需求。
- Acceptance Criteria 未驗證前不得標示 Ready。
- 分開記錄確認缺陷、風險、假設與選擇性改善項目。
- 實作修正路由回 Claude Code。

## Subagent Delegation

- Consulting OS `.agents/` 的 specialist agents 僅在任務明確需要顧問分析或品質審查時使用。
- 不得在未收到明確任務授權時自行派生 agents。
- 受派工方不得再呼叫 Codex 或其他代理（Rule 8：No Recursive Delegation）。

## Review Workflow

1. 讀取 `docs/ai-collaboration-workflow.md`、`docs/project-brief.md`、`tasks/current_task.md` 與相關規格。
2. 查看實作與測試。
3. 執行針對性驗證。
4. 依嚴重度排列回報 findings。
5. 設定 Verdict 並指名必要後續。

## Consulting OS

顧問交付物遵循 `docs/consulting-os.md`，使用 `.agents/` 僅限任務明確要求顧問分析或品質審查。
