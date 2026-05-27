# ISO 42001 A.6 Online V&V 報告（實打 RAG API 計算 Hit Rate）

**產生時間**：2026-05-26T13:19:11.731409+00:00
**RAG API**：http://localhost:8043
**資料集**：34 筆（in-scope: 31，out-of-scope: 3）
**業務目標**：Hit Rate ≥ 0.9
**達標狀態**：❌ 目標未達

## 主指標（in-scope 查詢）

| 指標 | 分數 | 是否 gating |
|------|------|-------------|
| **Hit Rate** | **0.8065** | ✅ 業務目標 |
| Precision@K | 0.4876 | info |
| Recall@K | 0.7903 | info |
| F1@K | 0.5624 | info |
| 評估筆數 | 31 | — |

## Out-of-Scope 拒絕測試

- 應拒絕筆數：3
- 正確拒絕筆數：3
- 拒絕正確率：1.0

## 失敗案例（hit_rate = 0）

- `eval_c04` 「陸海空軍懲罰法所稱「重大懲罰」的定義為何？...」 expected=['第4條'] cited=['第78條', '第14條', '第28條']
- `eval_m03` 「軍人權益救濟有哪幾種程序？...」 expected=['第3條'] cited=['第68條', '第76條', '第五條', '第77條']
- `eval_m07` 「復審事件由哪個權保會管轄？...」 expected=['第9條'] cited=['第45條', '第38條']
- `eval_m11` 「復審的提起期限多久？...」 expected=['第15條'] cited=[]
- `eval_cr02` 「軍人違紀受懲罰後，如不服懲罰處分要如何救濟？...」 expected=['第3條', '第13條'] cited=['第65條']
- `eval_cr04` 「權保委員與懲罰權責長官是不是同一人？...」 expected=['第4條', '第8條'] cited=['第37條', '第55條', '第57條']

---
*由 `monitoring_addon/scripts/run_online_vv.py` 自動產生。本腳本獨立於 RAG 主系統，不修改任何 RAG/ 內檔案。*