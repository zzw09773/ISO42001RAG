# ISO 42001 A.6 擴充 V&V 評估報告（含 Recall@K / F1@K / MAP）

**產生時間**：2026-05-27T01:34:06.430258+00:00
**資料集筆數**：34
**評估 K**：5
**整體結果**：❌ FAIL

## 業務目標：Hit Rate ≥ 0.9

**當前 Hit Rate**：`0.0`  →  ❌ (gating metric)

其餘指標僅供參考，不影響 pass/fail 判定。

## 檢索準確度指標

| 指標 | 分數 | 門檻 | 是否 gating | 狀態 |
|------|------|------|-------------|------|
| **Hit Rate** | **0.0** | **0.9** | ✅ 業務目標 | ❌ |
| Precision@K | 0.0 | 0.5 | info | ❌ |
| Recall@K | 0.0 | 0.5 | info | ❌ |
| F1@K | 0.0 | 0.5 | info | ❌ |
| MRR | 0.0 | 0.4 | info | ❌ |
| MAP | 0.0 | 0.4 | info | ❌ |
| 評估筆數 | 31 | — | — |
| 跳過筆數（無 ground truth） | 3 | — | — |

---
*由 `monitoring_addon/scripts/run_extended_vv.py` 自動產生。本腳本獨立於 RAG/scripts/run_vv_evaluation.py，主系統行為不受影響。*