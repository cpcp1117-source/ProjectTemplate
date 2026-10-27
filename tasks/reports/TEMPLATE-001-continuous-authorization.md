# TEMPLATE-001 Continuous Repair Authorization

## User Instruction

「和claude進行問題修復，一直到都可以正常運作沒有問題為止，如果遇到token或是用量使用上限就先暫停等到用量恢復後續進行」

## Scope and Operating Rules

- 本次使用者指示明確調整 TEMPLATE-001 的修正輪數：持續由 Claude 實作、Codex 獨立驗證，不再每兩輪要求追加授權。Task ID 保持不變。
- 目標是既定 controller 協作流程通過正常完成、真實 Claude 寫檔、真實 Codex 審核、取消、逾時、恢復與乾淨新專案演練；不宣稱任何軟體絕對沒有未知缺陷。
- 維持單一程式碼寫入者、明確版本、有限單次 Timeout、逾時後先確認程序與副作用再決定恢復；不得盲目重複提交已產生產物的任務。
- 遇到已確認的 token／用量限制：保存原始錯誤摘要、可用的恢復時間、Task／Job／Session ID、檔案 Hash 與副作用；等待恢復後核對狀態再接續。認證錯誤、程式缺陷與用量限制分開處理。
- 不變更帳號、複製登入憑證或自動繞過用量限制。只有既定修復與驗證範圍；不含 main 合併或正式部署。

## Current Entry Point

Round 10 review: `reports/review_reports/TEMPLATE-001-round10.md`。
先解決成功任務被誤殺與整棵程序樹清理，再完成結果版本資訊、文字去重與完整演練。
