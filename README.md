# AI Collaboration Project Template

> **Implementation Status: Not Ready — Draft PR (2026-10-26).** 101 項自動測試、離線預檢與兩邊各自的 live 呼叫通過；真實雙向委派均失敗，仍不可視為已驗證可用。詳見 [TEMPLATE-001 Review](reports/review_reports/TEMPLATE-001.md)。

可重用的 repo 範本，支援固定角色的 Claude Code 實作與 Codex 審查工作流程，並提供選用的 Consulting OS 擴充層。

本範本不預設技術堆疊。在專案限制確認前，不要新增專案特定的 manifests 或基礎設施。

## Fixed Roles

| Owner | Fixed responsibility |
|---|---|
| Human | 定義優先順序、核准範疇、解決 trade-off、接受交付 |
| Claude Code | 規格、架構、程式、frontend、backend、測試與修正 |
| Codex | 獨立審查、驗證、風險分析與 readiness verdict |

完整的 16 步驟角色地圖見 `docs/ai-collaboration-workflow.md`。

## Quick Start

### 1. 使用此範本建立 repo

從 GitHub 使用 "Use this template" 建立新 repo。

### 2. 初始化協作設定

```bash
# Python 3.11+ 標準函式庫，無額外相依套件
python scripts/init_project.py

# 或指定明確 root（用於隔離測試）
python scripts/init_project.py --root /path/to/project
```

這會產生 `.collaboration/local.json`（gitignored），儲存 CLI 路徑、規則 hash 與 timeout 設定。

### 3. 執行確定性預飛檢查（不消耗 tokens）

```bash
python scripts/check_connection.py
```

檢查 CLI binaries、必要文件、config 與規則 hash。

### 4. 選用：執行 live 模型呼叫檢查

```bash
# 需要付費 API 或有效登入
python scripts/check_connection.py --live

# 雙向探測（需要 --live）
python scripts/check_connection.py --live --bidirectional
```

報告儲存於 `reports/connection_reports/`（gitignored）。

### 5. 填寫 Project Brief 與 Current Task

- 完成 `docs/project-brief.md`。
- 在 `tasks/current_task.md` 定義第一個任務。

### 6. 開始協作

```bash
# Claude Code 實作
claude "請讀取 docs/ai-collaboration-workflow.md 與 tasks/current_task.md 並開始實作"

# Codex 審查
codex exec "讀取 docs/ai-collaboration-workflow.md 與 tasks/current_task.md，審查 Claude 的實作"
```

**注意**：
- Runtime config/root 不授予 sandbox 或 shell 存取；scopes 是行為約束，非技術權限。
- Config 不建立永久連線，不橋接至目前桌面聊天。
- 不要在 GitHub CI 或 git 儲存帳號 tokens、raw logs 或 session ID。
- 移動專案目錄或工作流程文件變更後：將 `.collaboration/local.json` 重新命名為備份，再重新執行 `init_project.py`，不要靜默覆蓋現有 config。

## Core Files

- `AGENTS.md` — Codex 固定審查規則
- `CLAUDE.md` — Claude Code 固定實作規則
- `docs/ai-collaboration-workflow.md` — 八項規則與 16 步驟角色地圖（唯一共同規範）
- `docs/consulting-os.md` — 選用顧問工作流程
- `docs/project-brief.md` — 專案說明（填寫後使用）
- `tasks/current_task.md` — 進行中任務的 single source of truth
- `tasks/reports/` — 任務實際執行證據（與 current_task 分開）
- `docs/skills/` — 三個 custom skills 的共享程序
- `.agents/` — Codex specialist 顧問角色
- `docs/templates/` — 可重用的交付範本

## Scripts

| Script | Purpose |
|---|---|
| `scripts/init_project.py` | 初始化 `.collaboration/local.json` |
| `scripts/check_connection.py` | 預飛檢查與 live 連線測試 |
| `scripts/probe_peer.py` | 雙向探測的有界輔助工具 |

Windows PowerShell 等效指令：用 `python` 替換 `python3`；所有腳本為純標準函式庫，無 pip 相依套件。

## Skills

| Skill | Host | Trigger |
|---|---|---|
| `collaboration-handoff` | Claude + Codex | 任務交接 |
| `verify-and-review` | Codex (owner) | 獨立驗證與審查 |
| `remediate-findings` | Claude (owner) | 修正審查 findings |

Skill 程序見 `docs/skills/`；thin entries 分別在 `.agents/skills/` 與 `.claude/skills/`。

**Custom Skills 呼叫方式：**
- Codex：`$skill-name`（例如 `$collaboration-handoff`）
- Claude Code：`/skill-name`（例如 `/collaboration-handoff`）

Host discovery 不由 runtime 自動完成；需明確指定對象與範圍。

## Tests

```powershell
# Windows PowerShell (UTF-8 mode required)
python -X utf8 -m unittest tests/test_scripts.py -v
# 或 discover
python -X utf8 -m unittest discover tests/
```

測試使用標準函式庫 unittest，假 CLI subprocess fixtures，不發出真實模型呼叫。不需要 pytest。

目前狀態：**Pending Validation** — 測試尚未由 Codex 執行，不宣告通過。
