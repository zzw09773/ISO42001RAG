#!/usr/bin/env python3
"""Rigorous A/B rerun: toggle prompt version, rerun flip queries N times each.

Separates LLM/backend noise from a real prompt effect by repeating each flip
query under BOTH prompt versions and reporting per-query pass-rate. Uses a
generous client timeout to eliminate the client-timeout artifact that
contaminated the full V&V runs (server answers taking 300–900s).

Drives prompt toggling by copying a pre-saved prompts.py into place and
restarting rag-api between phases. Restores the candidate (1.2.0) at the end.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ADDON = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ADDON / "scripts"))
from run_online_vv import ask_rag, extract_cited_articles, normalize_article  # noqa: E402

PROMPTS_DEST = ADDON.parent / "RAG" / "rag_system" / "core" / "prompts.py"
RAG_URL = "http://localhost:8043"
RUNS = 3
TIMEOUT = 300
QUERY_IDS = ["eval_m07", "eval_m10", "eval_cr02"]  # genuine prompt-relevant flips
VERSIONS = [("1.1.0", "/tmp/prompts_v110.py"), ("1.2.0", "/tmp/prompts_v120.py")]


def restart_rag(expect_hash_prefix):
    subprocess.run(["cp", expect_hash_prefix[1], PROMPTS_DEST], check=True)
    subprocess.run(["docker", "restart", "ISO42001_rag_api"],
                   check=True, capture_output=True)
    # wait healthy
    for _ in range(40):
        r = subprocess.run(["curl", "-fsS", f"{RAG_URL}/health"], capture_output=True)
        if r.returncode == 0:
            break
        time.sleep(3)
    time.sleep(3)


def hit(cited, expected):
    e = {normalize_article(a) for a in expected}
    c = set(cited)
    return bool(e & c)


def main():
    golden = {g["id"]: g for g in json.load(open(ADDON / "data" / "golden_dataset.json"))}
    targets = [golden[q] for q in QUERY_IDS]

    results = {}  # ver -> qid -> list of (hit, cited, secs)
    for ver, path in VERSIONS:
        print(f"\n=== 切換至 prompt {ver} 並重啟 rag-api ===", flush=True)
        restart_rag((ver, path))
        results[ver] = {}
        for g in targets:
            qid, query, exp = g["id"], g["query"], g.get("expected_articles", [])
            runs = []
            for i in range(RUNS):
                t0 = time.time()
                ans = ask_rag(RAG_URL, query, api_key=None, timeout=TIMEOUT)
                secs = time.time() - t0
                cited = extract_cited_articles(ans) if ans else []
                h = hit(cited, exp) if ans else None  # None = empty/timeout
                runs.append({"hit": h, "cited": cited, "secs": round(secs)})
                print(f"  [{ver}] {qid} run{i+1}: hit={h} cited={cited} ({secs:.0f}s)", flush=True)
                time.sleep(0.3)
            results[ver][qid] = runs

    # restore candidate 1.2.0
    subprocess.run(["cp", "/tmp/prompts_v120.py", PROMPTS_DEST], check=True)
    subprocess.run(["docker", "restart", "ISO42001_rag_api"], check=True, capture_output=True)

    # summary
    print("\n" + "=" * 60)
    print("嚴謹重跑結果（每題每版 3 次，timeout 300s）")
    print("=" * 60)
    summary = {}
    for g in targets:
        qid, exp = g["id"], g.get("expected_articles", [])
        print(f"\n{qid} (expected={exp}):")
        summary[qid] = {"expected": exp}
        for ver, _ in VERSIONS:
            runs = results[ver][qid]
            hits = sum(1 for r in runs if r["hit"] is True)
            valid = sum(1 for r in runs if r["hit"] is not None)
            empties = sum(1 for r in runs if r["hit"] is None)
            print(f"  {ver}: 命中 {hits}/{valid} 有效次"
                  + (f"（{empties} 次空/超時）" if empties else ""))
            for r in runs:
                print(f"        hit={r['hit']} cited={r['cited']} {r['secs']}s")
            summary[qid][ver] = {"hits": hits, "valid": valid, "empties": empties}

    out = ADDON / "data" / "reports" / "rigorous_rerun_2026-06-12.json"
    out.write_text(json.dumps({"runs": results, "summary": summary},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {out}")
    print("已還原 prompts.py 至候選版 1.2.0")


if __name__ == "__main__":
    main()
