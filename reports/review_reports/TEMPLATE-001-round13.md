# TEMPLATE-001 Round 13 Review

## Verdict

Not Ready — 正常合作證據已存在；精確的程序身分與失敗啟動狀態尚未接上實際流程。持續修復授權有效。

## Evidence

- Claude 自測完整套件：217 total、216 pass、1 skip。Codex 已獨立重跑同一套件，結果見 `G:/AI Collaboration/work/round13-tests.log`。
- Reviewer-owned 程序清理 2/2 Pass：`round13-independent.log`。
- 新增的 reviewer recovery acceptance 6 項：2 Pass、4 Fail，`round13-recovery.log`。使用本機 fake CLI，不耗模型額度。
- 四項確認缺陷：failed launch 仍保存 pending；reused PID 仍阻擋舊工作恢復；uncertain spawn 的 child PID／identity 遺失；正常 worker／child 身分未存入 state。
- `get_process_creation_identity` 已建立、typed Windows liveness API 與 pending-no-PID refusal 已加入，但 helper 尚未接線；不是完成 PID reuse handling。
- App Server wrong-turn-before-response buffer 已加入測試；程式 API 例外不再只因父程序退出而推論整群組已停止。

## Ordered Required Fixes

1. Claude 接上 worker_identity、child_identity 的實際寫入；recover 比對原始建立身分，確認舊程序已不存在，不碰重用 PID 的新程序。
2. uncertain containment failure 例外攜帶 child PID／identity，worker 保留以供安全恢復。
3. 確認未建立 worker 的 launch failure 應保存 failed 與原錯誤、解除鎖，不能留下假 pending。
4. 直接執行 reviewer acceptance 的同一六項斷言，全部通過後由 Codex 再獨立複驗；不改 reviewer 檔案、不放寬斷言。

## Limits

未重新消耗模型額度重跑已知正常路徑；本輪先補齊本機失效案例。乾淨新專案演練仍為最後 gate。沒有 commit 或發布。
