# ISO 42001 A.6 Online V&V 報告（實打 RAG API 計算 Hit Rate）

**產生時間**：2026-05-26T13:08:24.156548+00:00
**RAG API**：http://localhost:8043
**資料集**：34 筆（in-scope: 31，out-of-scope: 3）
**業務目標**：Hit Rate ≥ 0.9
**達標狀態**：❌ 目標未達

## 主指標（in-scope 查詢）

| 指標 | 分數 | 是否 gating |
|------|------|-------------|
| **Hit Rate** | **0.7742** | ✅ 業務目標 |
| Precision@K | 0.6543 | info |
| Recall@K | 0.7581 | info |
| F1@K | 0.6828 | info |
| 評估筆數 | 31 | — |

## Out-of-Scope 拒絕測試

- 應拒絕筆數：3
- 正確拒絕筆數：3
- 拒絕正確率：1.0

## 失敗案例（hit_rate = 0）

- `eval_c10` 「軍人對長官違法命令是否要服從？...」 expected=['第10條'] cited=[]
- `eval_m04` 「對提起復審的人是否有歧視保護？...」 expected=['第4條'] cited=[]
- `eval_m05` 「原行政處分執行是否會因進行復審而停止？...」 expected=['第6條'] cited=[]
- `eval_m08` 「權保會審議復審事件需要多少人合議？...」 expected=['第10條'] cited=[]
- `eval_m10` 「軍人在什麼情況下可以提起復審？...」 expected=['第13條'] cited=[]
- `eval_m11` 「復審的提起期限多久？...」 expected=['第15條'] cited=[]
- `eval_cr02` 「軍人違紀受懲罰後，如不服懲罰處分要如何救濟？...」 expected=['第3條', '第13條'] cited=[]

---
*由 `monitoring_addon/scripts/run_online_vv.py` 自動產生。本腳本獨立於 RAG 主系統，不修改任何 RAG/ 內檔案。*