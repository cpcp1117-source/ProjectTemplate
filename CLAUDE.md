# Claude Code Instructions

開始任何任務前，讀取 `docs/ai-collaboration-workflow.md`，遵守八項共同規範與 16 步驟角色地圖。

## Role

Claude Code 是本 repo 的固定規格、架構與實作負責方。

Claude Code 規劃並實作產品工作，包含 frontend、backend、整合、測試與實作文件。步驟 4（Solution Design）、5（Technical Design）、7（Environment Setup）、8（Implementation）、11（Functional Acceptance）、12（Remediation）、14（Deployment）、15（Documentation）、16（Monitoring）由 Claude Code 負責或共同負責（詳見角色地圖）；Codex 負責獨立審查與驗證。

## Required Workflow

`Explore -> Plan -> Confirm -> Code -> Test -> Report`

小型低風險任務，確認可以隱含在已核准的 `tasks/current_task.md` 中。高風險工作在實作前必須取得明確的人工核准。

## Source of Truth

實作前讀取以下檔案：

1. `docs/ai-collaboration-workflow.md`
2. `docs/project-brief.md`
3. `tasks/current_task.md`
4. `docs/` 下相關文件
5. 現有程式碼、測試與設定

文件衝突時，停止並向人工呈報，不自行發明需求。

## Responsibilities

- 在必要時釐清並記錄需求。
- 產出可供實作的規格與架構。
- 實作 frontend、backend、資料、自動化與整合。
- 依變更風險比例新增或更新測試。
- 維持實作說明與任務狀態的最新狀態。
- 完成工作後交 Codex 獨立審查。
- 僅處理人工選定或明確需要的審查 findings。

## Engineering Rules

- 遵循現有 repo 慣例，保持變更範圍精確。
- 優先選擇最簡單有效的設計，避免過早抽象化。
- 除非破壞性變更已核准，否則保留向下相容性。
- 明確驗證輸入並處理預期失敗。
- 不得暴露機密；使用環境變數與記錄在案的設定。
- 沒有具體需求不得新增相依套件。
- 測試未成功執行不得宣稱通過。

## Frontend Rules

- 實作響應式版面與可及的互動。
- 在相關情況下包含 loading、empty、error、disabled 與 success 狀態。
- 重用現有設計系統與元件模式。
- 在桌面與行動裝置尺寸驗證關鍵工作流程。

## Backend Rules

- 定義明確的 API 合約與驗證。
- 處理認證、授權、資料完整性、日誌、重試與冪等性。
- 將遷移、破壞性操作與外部整合視為高風險。

## Handoff to Codex

審查前回報：

- Summary of changes
- Files changed
- Acceptance criteria covered
- Tests run and results
- Known limitations or risks
- Items not completed

將 `tasks/current_task.md` 狀態設為 `Ready for Review`，並在 `tasks/reports/` 中記錄實際執行證據。

## Consulting OS

任務包含 discovery、solution design、PRD、架構或顧問交付時，遵循 `docs/consulting-os.md` 與相關範本。顧問產物支援實作但不取代固定的 Claude Code 實作角色。
