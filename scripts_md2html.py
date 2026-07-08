#!/usr/bin/env python3
"""
md2html — 把專案所有文件 .md 轉成自包含 .html（內網無 .md 解析器用）。

每個 .html：
  - 內嵌 CSS，不載入 CDN/字型/JS，可離線開啟、列印、郵寄。
  - 正確渲染 GFM 表格、程式碼區塊、引用、清單、標題、中文。
  - 文件間 [..](X.md) 連結自動改寫為 [..](X.html)，內網可互相導航。

用法：python3 scripts_md2html.py            # 轉換全部
      python3 scripts_md2html.py --list     # 只列出將轉換的檔
"""
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent

# 排除：pytest 快取（雜物）、法規語料（RAG 知識庫資料，非文件）、虛擬環境
EXCLUDE_DIRS = {".pytest_cache", "__pycache__", "_dev_archive", "node_modules",
                "converted_md", ".venv", "venv", "superpowers", ".superpowers"}

CSS = """
:root{--ink:#1a1f2c;--muted:#5b6578;--line:#c9d1dc;--hairline:#e5eaf1;--bg:#fff;--soft:#f6f8fc;--accent:#1e3a8a;
      --mono:"JetBrains Mono","Consolas","Courier New",monospace;}
*,*::before,*::after{box-sizing:border-box;}
body{font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",sans-serif;
     margin:0;background:var(--soft);color:var(--ink);line-height:1.75;font-size:15px;}
.wrap{max-width:960px;margin:0 auto;padding:0 0 60px;background:var(--bg);
      border-left:1px solid var(--line);border-right:1px solid var(--line);min-height:100vh;}
.dochead{padding:22px 40px 0;}
.dochead .row{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px;
              font-size:12px;color:var(--muted);}
.dochead .row code{background:none;padding:0;color:var(--muted);}
.dochead .row a{color:var(--accent);text-decoration:none;font-weight:700;}
.dochead .row a:hover{text-decoration:underline;}
.dochead hr{border:0;border-top:2px solid var(--ink);margin:10px 0 0;}
.content{padding:20px 40px 0;}
h1{font-size:26px;font-weight:900;margin:.4em 0 .5em;padding-bottom:.3em;border-bottom:1px solid var(--ink);}
h2{font-size:20px;font-weight:800;margin:1.6em 0 .6em;padding-bottom:.25em;border-bottom:1px solid var(--hairline);}
h2::before{content:"";display:inline-block;width:4px;height:.85em;background:var(--accent);
           margin-right:10px;vertical-align:-.08em;}
h3{font-size:16.5px;font-weight:800;margin:1.3em 0 .5em;}
h4{font-size:14.5px;font-weight:800;color:#374151;margin:1.1em 0 .4em;}
p{margin:.7em 0;text-wrap:pretty;}
a{color:var(--accent);}
ul,ol{margin:.6em 0;padding-left:1.7em;}
li{margin:.25em 0;}
blockquote{margin:1em 0;padding:.6em 16px;border-left:2px solid var(--ink);
           background:var(--soft);color:#374151;}
blockquote p{margin:.3em 0;}
code{font-family:var(--mono);font-size:.88em;background:#eef2f7;color:#0f172a;padding:.12em .4em;}
pre{background:var(--soft);color:var(--ink);border:1px solid var(--line);padding:14px 16px;
    overflow-x:auto;font-size:13px;line-height:1.55;}
pre code{background:none;color:inherit;padding:0;}
table{border-collapse:collapse;width:100%;margin:1em 0;font-size:13.5px;display:block;overflow-x:auto;}
th{text-align:left;padding:8px 11px;font-weight:700;white-space:nowrap;
   border-top:2px solid var(--ink);border-bottom:1px solid var(--ink);}
td{padding:7px 11px;border-bottom:1px solid var(--hairline);vertical-align:top;}
tr:nth-child(even) td{background:#fafbfd;}
hr{border:0;border-top:1px solid var(--line);margin:1.6em 0;}
img{max-width:100%;}
.docfoot{margin:36px 40px 0;padding-top:14px;border-top:2px solid var(--ink);
         font-size:11.5px;color:var(--muted);}
details{border:1px solid var(--line);margin:1em 0;padding:0 14px;}
summary{cursor:pointer;font-weight:700;padding:10px 0;color:var(--accent);}
@media print{body{background:#fff;}.wrap{border:0;max-width:none;}
             .dochead .row a{display:none;}
             pre,blockquote,table{break-inside:avoid-page;}}
"""

PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <div class="dochead">
    <div class="row">
      <span>ISO 42001 RAG · <code>{relpath}</code></span>
      <span><a href="javascript:window.print()">列印 / 存 PDF</a></span>
    </div>
    <hr>
  </div>
  <div class="content">
{body}
  </div>
  <div class="docfoot">
    本檔由 <code>scripts_md2html.py</code> 自 <code>{relpath}</code> 產生 · 自包含，可離線開啟。
    原始 Markdown 為版控來源；此 HTML 供內網無 .md 解析器之環境檢視。
  </div>
</div>
</body>
</html>
"""

# 將文件內 [..](X.md) / [..](X.md#anchor) 改寫成 .html（僅相對連結，不動 http）
_LINK_RE = re.compile(r'(\]\()([^)\s]+?)\.md(#[^)\s]*)?(\))')


def rewrite_md_links(text: str) -> str:
    def repl(m):
        target = m.group(2)
        if target.startswith(("http://", "https://", "mailto:")):
            return m.group(0)
        return f"{m.group(1)}{target}.html{m.group(3) or ''}{m.group(4)}"
    return _LINK_RE.sub(repl, text)


def collect() -> list:
    out = []
    for p in ROOT.rglob("*.md"):
        if any(part in EXCLUDE_DIRS for part in p.relative_to(ROOT).parts):
            continue
        out.append(p)
    return sorted(out)


def first_title(md_text: str, fallback: str) -> str:
    for line in md_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def convert(p: Path) -> Path:
    raw = p.read_text(encoding="utf-8")
    raw = rewrite_md_links(raw)
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "toc", "attr_list"],
        output_format="html5",
    )
    body = md.convert(raw)
    relpath = str(p.relative_to(ROOT))
    title = first_title(raw, p.stem)
    html = PAGE.format(title=title, css=CSS, body=body, relpath=relpath)
    out = p.with_suffix(".html")
    out.write_text(html, encoding="utf-8")
    return out


def main() -> int:
    files = collect()
    if "--list" in sys.argv:
        for p in files:
            print(p.relative_to(ROOT))
        print(f"\n共 {len(files)} 個 .md 將轉為 .html")
        return 0
    n = 0
    for p in files:
        try:
            out = convert(p)
            n += 1
        except Exception as e:
            print(f"  ✗ {p.relative_to(ROOT)}: {e}", file=sys.stderr)
    print(f"完成：{n}/{len(files)} 個 .md → .html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
