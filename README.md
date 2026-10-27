# AI Collaboration Project Template

> **Implementation Status: Ready — Windows host-controller workflow independently verified (2026-10-26, Asia/Taipei).**
> 236 項測試：235 通過、1 項 POSIX-only 略過。真實 Claude 寫檔、Codex 精確審核／JSON、取消、逾時、恢復，以及乾淨新專案演練均通過。
> 驗證範圍、版本與證據見 [Controller Review](reports/review_reports/TEMPLATE-001-controller.md)。

可重用的 repo 範本，支援固定角色的 Claude Code 實作與 Codex 審查工作流程，並提供選用的 Consulting OS 擴充層。

本範本不預設技術堆疊。在專案限制確認前，不要新增專案特定的 manifests 或基礎設施。

## Fixed Roles

| Owner | Fixed responsibility |
|---|---|
| Human | 定義優先順序、核准範疇、解決 trade-off、接受交付 |
| Claude Code | 規格、架構、程式、frontend、backend、測試與修正 |
| Codex | 獨立審查、驗證、風險分析與 readiness verdict |

完整的 16 步驟角色地圖見 `docs/ai-collaboration-workflow.md`。

## How It Works

**Default workflow**: Codex current chat → host controller → Claude implementation → Codex independent review.

1. Codex (in current chat) starts a Claude implementation job via the controller (`collaboration.py start --actor claude`).
2. Codex polls status with `collaboration.py status --job <JOB_ID>`.
3. When the job completes, Codex retrieves the result (`collaboration.py result --job <JOB_ID>`) and independently reviews it.
4. Optional: `--actor codex` starts a machine-review turn via Codex App Server for an additional automated review pass.

Current chat reports progress while the turn is open. There are **no proactive updates after the turn closes**. There is **no permanent desktop-chat bridge**.

The old bidirectional check (`check_connection.py` 3-run gate) is a **legacy diagnostic** only — not required for the default controller workflow.

### Cancel / Timeout / Cleanup Recovery

If a job is stuck with `cleanup_required: true` in state:
1. Confirm the worker PID (`pid` field) and child PID (`child_pid` field) are gone.
2. Run `collaboration.py recover --job <JOB_ID>` — it verifies PIDs are gone, clears the flag, and releases the lock.
3. `recover` also works for jobs in terminal states (failed/interrupted) that still hold the lock due to uncertain cleanup.

## Quick Start (Windows)

### 1. 使用此範本建立 repo

從 GitHub 使用 "Use this template" 建立新 repo，或直接 clone：

```powershell
git clone <repo-url>
cd ProjectTemplate
```

### 2. 初始化協作設定

```powershell
# Python 3.11+ 標準函式庫，無額外相依套件
python scripts\init_project.py

# 或指定明確 root
python scripts\init_project.py --root C:\path\to\project
```

這會產生 `.collaboration/local.json`（gitignored），儲存 CLI 路徑、規則 hash 與 timeout 設定。

### 3. 預飛檢查（不消耗 tokens）

```powershell
python scripts\check_connection.py
```

### 4. 定義任務並啟動 Claude 背景工作

建立任務 JSON（範例）：

```json
{
  "task_id": "PROJ-001",
  "objective": "Implement feature X and write tests",
  "prompt": "Read docs/ai-collaboration-workflow.md and CLAUDE.md. Implement...",
  "allowed_paths": ["src/feature.py", "tests/test_feature.py"],
  "timeout_secs": 300
}
```

```powershell
# Codex 在 Codex chat 中執行：
python -X utf8 scripts\collaboration.py start --actor claude --task-file tasks\my_task.json

# 回傳 job_id，例如 {"job_id": "a1b2c3d4e5f6a7b8", ...}
```

### 5. Poll Status、取得結果、取消或復原

```powershell
# 輪詢狀態
python -X utf8 scripts\collaboration.py status --job <JOB_ID>

# 取得終態結果（job 完成後）
python -X utf8 scripts\collaboration.py result --job <JOB_ID>

# 請求取消
python -X utf8 scripts\collaboration.py cancel --job <JOB_ID>

# 復原 (worker 確認已消失後)
python -X utf8 scripts\collaboration.py recover --job <JOB_ID>
```

### 6. Codex 獨立審查

Codex 收到 `result` 後在自己的 chat 中執行獨立審查，不需要 Claude 桌面聊天介入。
`status=completed` 代表實作已提交，**不代表 Codex 已接受**。

### 7. 選用：App Server 機器審查

```powershell
python -X utf8 scripts\collaboration.py start --actor codex --task-file tasks\review_task.json
```

### Error Recovery

- Job 已在執行：等待完成或 `cancel` 後再 `start`。
- Worker 消失或 `cleanup_required: true`：執行 `recover`，由控制程式核對 PID 與建立身分；同身分仍在執行或無法確認時會保留鎖。
- 若 worker 已消失但同一工作擁有的 child 仍在執行，交由 Codex 核對記錄的建立身分後處理；使用說明見 [Safe Stopping and Recovery](docs/controller-usage.md#safe-stopping-and-recovery)。
- Config 路徑異動：備份 `.collaboration/local.json` 後重新執行 `init_project.py`。

```powershell
# 安全停止與復原（Windows）
python -X utf8 scripts\collaboration.py cancel --job <JOB_ID>
python -X utf8 scripts\collaboration.py status --job <JOB_ID>
# 控制程式核對原程序身分後才解除鎖；拒絕時依錯誤訊息處理
python -X utf8 scripts\collaboration.py recover --job <JOB_ID>
```

**注意**：
- Config 不建立永久連線，不橋接至目前桌面聊天。
- 不要在 GitHub CI 或 git 儲存帳號 tokens、raw logs 或 session ID。
- User 保持在此 Codex chat；無需永久 desktop-chat bridge。

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
| `scripts/collaboration.py` | **主要入口**：start/status/result/cancel/recover |
| `scripts/init_project.py` | 初始化 `.collaboration/local.json` |
| `scripts/check_connection.py` | 預飛檢查與 live 連線測試（legacy diagnostic） |
| `scripts/probe_peer.py` | 雙向探測輔助工具（legacy diagnostic） |

所有腳本為純標準函式庫，無 pip 相依套件。Windows 使用 `python`，POSIX 使用 `python3`。

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
python -X utf8 -m unittest discover -s tests -v
```

測試使用標準函式庫 unittest，假 CLI subprocess fixtures，不發出真實模型呼叫。不需要 pytest。

目前狀態：**Round 10 Pending Codex Verification** — Round 10 修復完成，等待 Codex 獨立執行測試套件後方可宣告通過。
詳見 [Round 10 Report](tasks/reports/TEMPLATE-001-round10.md)。
