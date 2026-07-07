#!/usr/bin/env python3
"""Faithfulness judge 能力驗證（稽核佐證：judge 經驗證）。

對 7 個**已知忠實度**的法條案例（完全有據／捏造數字／引錯條號／部分有據／
拒答／改寫有據／過度推論），用部署中的 `FAITHFULNESS_PROMPT` 讓 judge 打分數次，
檢查：JSON 合法率、溫度 0 測定性、**方向正確性**（分數隨真值走）、拒答旗標。
通過代表此 judge（預設 gpt-oss-20b）足以作為 faithfulness 評估器。

輸出 `data/reports/judge_validation_<date>.{json,md}` 作為 ISO 42001 A.6/A.9 證據。
判定 PASS = 所有案例方向正確且 JSON 合法率達標。

用法（需 LLM_API_BASE/LLM_API_KEY 指向 chat gateway；容器內 compose 已提供）：
    docker exec ISO42001_monitoring python3 /app/scripts/validate_judge.py
"""
import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitoring.ragas_metrics import FAITHFULNESS_PROMPT, _call_llm, _parse_json

# ── 真實法條（陸海空軍懲罰法）作為評估上下文 ──────────────────────────────
ART6 = """【陸海空軍懲罰法 第6條】
1 軍人非執行勤務而有下列行為之一者，為勤務外違紀行為，仍應受懲罰：
一、故意觸犯刑事法律。
二、無故施用或持有毒品、迷幻藥、麻醉藥品或其他相類似之管制藥品。
三、服用酒類而違法駕駛交通工具，或無正當理由拒絕接受酒精濃度測試。
四、實施性騷擾或性霸凌。
2 前項第七款之行為，不得為重大懲罰。"""

ART8 = """【陸海空軍懲罰法 第8條】
1 軍人之違紀行為非出於故意或過失者，不受懲罰。因過失而應受重大懲罰者，以重大過失為限。
2 軍人不得因不知法令而免除本法所定懲罰責任。但按其情節，得從輕懲罰。"""

ART9 = """【陸海空軍懲罰法 第9條】
1 違紀行為有下列情形之一者，不受懲罰：
一、對於現在不法之侵害，而出於防衛自己或他人權利之行為。
二、因避免自己或他人生命、身體、自由、名譽或財產之緊急危難，而出於不得已之行為。"""

CASES = [
    dict(id="C1_完全有據", band="high",
         q="軍人勤務外有哪些行為仍應受懲罰？", ctx=ART6,
         a="依第6條，軍人非執行勤務而故意觸犯刑事法律、無故施用毒品、服用酒類而違法駕駛、實施性騷擾或性霸凌等，仍應受懲罰。"),
    dict(id="C2_捏造數字", band="low",
         q="軍人酒駕的懲罰期限與處分是什麼？", ctx=ART6,
         a="依第6條，軍人酒駕應於30日內完成處分，並一律記大過兩次、扣薪三個月。"),
    dict(id="C3_引錯條號", band="low",
         q="正當防衛是否受懲罰？", ctx=ART9,
         a="依第11條規定，對於現在不法之侵害而出於防衛自己權利之行為，不受懲罰。"),
    dict(id="C4_部分有據", band="mid",
         q="勤務外違紀行為有哪些？最重可受什麼懲罰？", ctx=ART6,
         a="依第6條，故意觸犯刑事法律、無故施用毒品屬勤務外違紀行為；且所有勤務外違紀行為一律可處撤職。"),
    dict(id="C5_拒答", band="abstain",
         q="軍人退伍後的月退俸如何計算？", ctx=ART6, a="無相關資料。"),
    dict(id="C6_改寫有據", band="high",
         q="非出於故意或過失的違紀行為要受罰嗎？", ctx=ART8,
         a="不用。軍人的違紀行為若不是出於故意或過失，就不會受到懲罰；不過若因過失而需受重大懲罰，必須是重大過失才行。"),
    dict(id="C7_過度推論", band="low",
         q="朋友酒駕被抓，軍方會怎麼處理？", ctx=ART6,
         a="依第6條，酒駕屬勤務外違紀行為應受懲罰。因此你朋友一定會被開除軍籍，而且終身不得再報考任何公職。"),
]


def _judge(case, reps):
    prompt = FAITHFULNESS_PROMPT.format(question=case["q"], context=case["ctx"], answer=case["a"])
    scores, abst, valid = [], 0, 0
    for _ in range(reps):
        d = _parse_json(_call_llm(prompt))
        if d is None:
            continue
        valid += 1
        if d.get("is_abstention"):
            abst += 1
        sc = d.get("score")
        if isinstance(sc, (int, float)) and not isinstance(sc, bool):
            scores.append(float(sc))
    return {
        "valid": valid, "reps": reps,
        "median": round(statistics.median(scores), 3) if scores else None,
        "abstentions": abst,
        "stable": len(set(scores)) <= 1,
    }


def _direction_ok(band, r):
    """分數是否與已知真值方向一致。"""
    if band == "abstain":
        return r["abstentions"] >= 1          # judge 須標記為拒答
    med = r["median"]
    if med is None:
        return False
    return {"high": med >= 0.8, "low": med <= 0.5, "mid": 0.3 <= med <= 0.85}.get(band, False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate the faithfulness judge against known-truth cases")
    ap.add_argument("--reps", type=int, default=3, help="評分重複次數（測定性）")
    ap.add_argument("--output-dir",
                    default=str(Path(__file__).resolve().parent.parent / "data" / "reports"))
    args = ap.parse_args()

    if not os.environ.get("LLM_API_BASE"):
        print("❌ 需 LLM_API_BASE 指向 chat gateway（非 embedding proxy）。", file=sys.stderr)
        return 2
    model = os.environ.get("CHAT_MODEL_NAME", "gpt-oss-20b")
    print(f"judge 模型：{model}（每案 {args.reps} 次）\n")

    results, total_valid, total_calls = [], 0, 0
    for c in CASES:
        r = _judge(c, args.reps)
        ok = _direction_ok(c["band"], r)
        total_valid += r["valid"]
        total_calls += r["reps"]
        results.append({"id": c["id"], "band": c["band"], "median": r["median"],
                        "valid": f"{r['valid']}/{r['reps']}", "stable": r["stable"],
                        "abstentions": r["abstentions"], "direction_ok": ok})
        print(f"[{c['id']}] band={c['band']:7} median={r['median']} "
              f"valid={r['valid']}/{r['reps']} stable={r['stable']} → {'✅' if ok else '❌'}")

    n_ok = sum(1 for x in results if x["direction_ok"])
    json_rate = round(total_valid / total_calls, 3) if total_calls else 0.0
    verdict = (n_ok == len(CASES)) and json_rate >= 0.95

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "judge_model": model,
        "reps_per_case": args.reps,
        "json_valid_rate": json_rate,
        "cases_direction_ok": f"{n_ok}/{len(CASES)}",
        "verdict": "PASS" if verdict else "FAIL",
        "results": results,
        "note": "方向正確性：高真值案例得高分、低真值得低分、拒答被標記。PASS 代表此 judge 足以作為 faithfulness 評估器。",
    }
    lines = [
        "# Faithfulness Judge 驗證報告（稽核佐證）",
        "",
        f"**產生時間**：{report['generated_at']}",
        f"**Judge 模型**：{model}",
        f"**JSON 合法率**：{json_rate}　**方向正確**：{n_ok}/{len(CASES)}　**判定**：{report['verdict']}",
        "",
        "| 案例 | 真值 | 中位分數 | JSON | 穩定 | 方向 |",
        "|---|---|---|---|---|---|",
    ] + [
        f"| {x['id']} | {x['band']} | {x['median']} | {x['valid']} | {x['stable']} | {'✅' if x['direction_ok'] else '❌'} |"
        for x in results
    ] + ["", f"> {report['note']}"]

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    (out / f"judge_validation_{date}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"judge_validation_{date}.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n判定：{report['verdict']}（JSON 合法率 {json_rate}、方向 {n_ok}/{len(CASES)}）")
    print(f"→ {out / f'judge_validation_{date}.json'}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
