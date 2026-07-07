# golden_dataset.json 說明

**整理日期**：2026-07-07

`RAG/tests/evaluation/golden_dataset.json` 保留作為 RAG 主系統 V&V 範例資料與單元測試輸入。
外部稽核準備前，由龔修潁確認實際採用的核定資料集版本、評估時間與報告位置。

## 使用原則

1. V&V 報告需記錄資料集版本、筆數、操作者與執行日期。
2. 若調整題目、期望關鍵字或期望條文，需同步更新 `RAG/CHANGELOG.md` 與版本快照。
3. 稽核時以 `AUDIT_EVIDENCE_INDEX.md` 所列主系統證據為準。
