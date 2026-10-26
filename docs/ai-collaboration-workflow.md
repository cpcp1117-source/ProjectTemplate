# AI Collaboration Workflow

Version: 1.0
Status: Adopted
Effective Date: 2026-10-22

本文件是 Claude Code 與 Codex 協作的唯一共同規範，亦為本 repo 內部的 Shared Source of Truth。開始任務前，雙方必須讀取本文件。使用者後續明確指示優先；本文件不擴大既有權限。

## Eight Shared Rules

### Rule 1: Shared Source of Truth

- 需求、核准規格、任務、審查與結果保存為專案檔案，交接時指定確切路徑與版本。
- 分開記錄 Facts、Assumptions、Unknowns；不得把假設寫成已確認事實。
- 新會話先讀取相關檔案，不假設它共享另一個 CLI 會話或目前聊天的記憶。

### Rule 2: Change Ownership

- 保留使用者指定分工（見下方 16 步驟角色地圖）。
- 同一工作目錄同時只有一個程式碼寫入者。Claude 實作與修正；Codex 審查時不直接代修實作。
- Codex 可維護其負責的需求、任務、審查與發布文件。需要並行修改時，先劃分工作目錄／worktree 與整合責任，避免覆寫他人變更。

### Rule 3: Handoff Contract

- 派工至少包含：Task ID、目標、輸入路徑、Baseline、允許修改路徑、不得修改項目、Acceptance Criteria、預期產出與 Timeout。
- 接收方先核對輸入與範圍；缺少關鍵資料時呈報，不自行擴充需求。
- 回傳至少包含：Task ID、執行狀態、修改檔案、產物路徑、驗證結果、未解決問題與下一步；介面有提供時記錄 Run ID／Session ID。
- 重要結果必須寫檔並回傳摘要。實際 CLI 會話 ID 不得用猜測或 `resume --last` 代替。

### Rule 4: Independent Verification

- Codex 根據核准需求、實際 diff 與獨立執行的適當檢查裁定；Claude 的成功宣告不能替代證據。
- 記錄驗證命令、Exit Code、結果與產物位置。無法或尚未執行標示 Not Run，說明原因，不標成 Pass。
- 測試依變更風險選擇；環境錯誤與產品缺陷分開記錄，不為低影響變更強制執行無關測試。

### Rule 5: Version-bound Acceptance

- 每次提交、審查、測試綁定明確版本：記錄 Commit hash；同時保存髒檔案 manifest（Git 倉庫使用 commit + dirty manifest；未使用 Git 時保存相關檔案 Hash/Manifest）。
- 驗收後若需求或相關實作變更，受影響的舊驗收失效；重新檢查影響範圍並執行適當複驗。
- 不得以先前版本通過的結果宣稱目前版本已通過。

### Rule 6: Explicit Review Verdict

- Codex 提供 Ready、Ready with Conditions 或 Not Ready。
- 每項問題包含 Finding ID、Critical／Major／Minor、位置、影響、修正預期、驗證方式與 Owner。
- Critical 或 Major 未解決不得通過。有條件通過須寫明條件及允許的下一步，不能自動視為可正式發布。
- Claude 逐項修正，或附證據提出異議；Codex 複核，仍無法解決的取捨交使用者決定。

### Rule 7: Bounded Retry and Recovery

- 同一任務預設最多兩輪修正：每輪為一次問題回傳、Claude 修正及 Codex 複驗。初次提交不計入；不得換 Task ID 規避上限。
- 呼叫前設定與任務大小相稱的 Timeout。逾時、兩輪仍未通過或執行受阻時，保存當前狀態、錯誤、產物與恢復步驟，停止自動重試並呈報。
- 逾時後先確認子程序是否仍執行及是否已有副作用，不立即重複提交。
- 恢復前核對檔案現況、既有產物與 Baseline；新增修正輪數須由使用者明確調整。

### Rule 8: No Recursive Delegation

- 每個任務指定一位主控方。主控方可依既有授權呼叫另一方；受派工方不得在該呼叫中再呼叫主控方，也不得自行派生其他代理。
- 受派工方將結果交回主控方，由主控方決定下一步。
- 需要例外時，必須有明確的任務授權、對象與界限；不得自行形成無限互叫。

## 16-Step Role Map

| Step | Name | Owner | Action |
|------|------|-------|--------|
| 1 | Requirements Discovery | Codex | 接收需求，確認範圍與 baseline |
| 2 | Scope Definition | Codex | 整理需求文件、建立 acceptance criteria |
| 3 | Existing System Analysis | Codex | 現有系統分析，標記缺口與風險 |
| 4 | Solution Design | Claude + Codex review | Claude 設計方案；Codex 審查設計 |
| 5 | Technical Design | Claude + Codex review | Claude 產出技術規格；Codex 審查 |
| 6 | Task Planning | Codex | 任務規劃，建立 task handoff 至 Claude |
| 7 | Environment Setup | Claude | 設定環境、準備基礎設施 |
| 8 | Implementation | Claude | 實作功能（frontend、backend、tests） |
| 9 | Automated Verification | Codex | 獨立驗證：執行檢查、測試、diff 分析 |
| 10 | Code Review | Codex leading / Claude responds | Codex 程式碼審查，提出 findings；Claude 逐項回應 |
| 11 | Functional Acceptance | Claude / Codex review | Claude 功能驗收測試；Codex 審查測試證據 |
| 12 | Remediation & Recheck | Claude / Codex | Claude 修正 findings；Codex 複驗（最多兩輪） |
| 13 | Release Preparation | Codex | 發布準備：changelog、版本標籤、發布文件 |
| 14 | Deployment & Smoke Test | Claude | 部署與 smoke test |
| 15 | Documentation & Handoff | Claude / Codex review | Claude 撰寫文件；Codex 審查交付物 |
| 16 | Monitoring & Maintenance | Claude / Codex review | Claude 執行維護；Codex 審查維護產物 |

**Human authority**: 商業範疇決策、有爭議的 trade-off、授權發布決策。例行的已授權工作不需要重新取得許可。Subagents 僅在任務明確授權時啟用，不得自行形成代理鏈。

## Task Entry Criteria

實作開始的前提條件（`tasks/current_task.md` 需包含）：

- 明確目標
- 需求與 Acceptance Criteria
- In Scope / Out of Scope
- Risk Level
- Baseline（commit hash 或相關檔案 hash/manifest）
- 已知限制與待解問題

## Handoff Contract Details

**Claude 交付 Codex 時需提供：**
- 變更摘要、修改檔案清單
- Acceptance Criteria 涵蓋情形
- 執行過的測試與結果
- 已知限制與風險
- 未完成項目

**Codex 回傳時需提供：**
- 按嚴重度排列的 findings（Finding ID、Critical/Major/Minor、位置、影響、修正預期）
- 驗證命令、Exit Code 與產物位置
- 必要修正的 Owner
- Readiness Verdict：Ready / Ready with Conditions / Not Ready

## High-Risk Work

以下視為高風險，需在實作前取得核准計畫，並由 Codex 審查後人工驗收：
身份驗證、授權、付款、資料庫遷移、正式部署、機密資料、資料刪除、核心業務邏輯、外部整合。

## Revision Limit

每個 Task ID 預設最多兩輪修正。初次提交不計入兩輪。不得以新 Task ID 規避此限制。快照變更後若影響既有驗收，該驗收失效，必須重新複驗。

## Template vs. Active Project

本 repo 的 `tasks/current_task.md` 在初始分發時為空白模板（所有欄位填寫 TBD）。新專案建立後，第一個任務的 Owner 負責填寫所有欄位並持續更新任務狀態；完成的任務歸檔至 `tasks/reports/`。可重用的空白範本永久保存於 `docs/templates/task-handoff.md`。

Skills 文件（`docs/skills/`）在 Git 倉庫為 dirty 狀態時，必須同時提交 Skills 文件變更與更新 dirty manifest；不得在 dirty 狀態下宣稱 Skills baseline 已確認。

## Self-Approval Prohibition

任何一方不得核准自己的實作成果（Rule 4）。Claude Code 不得宣告自己的實作 Ready；Codex 不得修改實作後再核准。

## Helper Invocation Scope

`check_connection.py --live` 使用任務合約與顯式呼叫設定 Timeout 與範圍；腳本本身僅對 probe_peer.py 助手呼叫設有界限。完整工作的 Timeout 由 `tasks/current_task.md` 的 Timeout 欄位定義，不依賴連線檢查設定。

## Operational Boundary

Markdown 規範提供行為約束，不是自動執行的權限、Timeout 或狀態機。尚未建立控制程式強制執行八項規則；目前由主控方讀取規範並逐次落實。
