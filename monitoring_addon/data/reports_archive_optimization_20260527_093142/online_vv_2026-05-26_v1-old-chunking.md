# ISO 42001 A.6 Online V&V 報告（實打 RAG API 計算 Hit Rate）

**產生時間**：2026-05-26T12:33:22.673230+00:00
**RAG API**：http://localhost:8043
**資料集**：34 筆（in-scope: 31，out-of-scope: 3）
**業務目標**：Hit Rate ≥ 0.9
**達標狀態**：❌ 目標未達

## 主指標（in-scope 查詢）

| 指標 | 分數 | 是否 gating |
|------|------|-------------|
| **Hit Rate** | **0.7742** | ✅ 業務目標 |
| Precision@K | 0.6516 | info |
| Recall@K | 0.7581 | info |
| F1@K | 0.6839 | info |
| 評估筆數 | 31 | — |

## Out-of-Scope 拒絕測試

- 應拒絕筆數：3
- 正確拒絕筆數：3
- 拒絕正確率：1.0

## 失敗案例（hit_rate = 0）

- `eval_c04` 「陸海空軍懲罰法所稱「重大懲罰」的定義為何？...」 expected=['第4條'] cited=['第5條']
- `eval_m04` 「對提起復審的人是否有歧視保護？...」 expected=['第4條'] cited=[]
- `eval_m05` 「原行政處分執行是否會因進行復審而停止？...」 expected=['第6條'] cited=['第50條']
- `eval_m08` 「權保會審議復審事件需要多少人合議？...」 expected=['第10條'] cited=[]
- `eval_m10` 「軍人在什麼情況下可以提起復審？...」 expected=['第13條'] cited=['第3條', '第56條']
- `eval_m11` 「復審的提起期限多久？...」 expected=['第15條'] cited=['第55條', '第56條']
- `eval_cr02` 「軍人違紀受懲罰後，如不服懲罰處分要如何救濟？...」 expected=['第3條', '第13條'] cited=['第57條', '第58條', '第59條', '第60條', '第61條', '第62條']

---
*由 `monitoring_addon/scripts/run_online_vv.py` 自動產生。本腳本獨立於 RAG 主系統，不修改任何 RAG/ 內檔案。*