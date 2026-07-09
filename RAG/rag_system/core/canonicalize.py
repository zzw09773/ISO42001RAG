"""偵測用正規化層（detection-only）— ISO 42001 A.8。

只用於「產生給 sanitizer 比對的視圖」，不改寫送往 LLM 的文字。
送往 LLM 的清洗只做 clean_text_for_downstream()（移除隱形字元）。
"""
from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

# 零寬/隱形字元：ZWSP/ZWNJ/ZWJ、word-joiner、BOM、soft hyphen、雙向控制字元
# 用 \u 明碼（避免隱形字元在文字中複製走樣）
_INVISIBLE_RE = re.compile(
    "[\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f\u202a\u202b\u202c\u202d\u202e]"
)

# collapsed 視圖比對的 injection 關鍵詞白名單（去空白/標點後的相鄰形式）
INJECTION_COLLAPSED_KEYWORDS = frozenset({
    "ignoreprevious", "ignoreallprevious", "ignoreaboveinstructions",
    # 填充詞變體（the/these/those/my/any）— 與 regex 填充詞擴充對應的拆字/黏字錨點
    # ignore/disregard/forget 三家族保持一致，避免拆字＋填充詞的單家族缺口
    "ignoretheprevious", "ignoretheseprevious", "ignorethoseprevious",
    "ignoremyprevious", "ignoreanyprevious",
    "disregardprevious", "forgetprevious", "forgetall",
    "disregardtheprevious", "disregardtheseprevious", "disregardthoseprevious",
    "disregardmyprevious", "disregardanyprevious",
    "forgettheprevious", "forgettheseprevious", "forgetthoseprevious",
    "forgetmyprevious", "forgetanyprevious",
    "doanythingnow", "jailbreak",
    "systemprompt", "newsystemprompt",
    "revealyourinstructions", "showyourprompt",
})

# 一段 SQL 區塊註解跨度異常長 → 輔助可疑訊號（不阻止移除）
_LONG_COMMENT_THRESHOLD = 200


@dataclass
class HostInfo:
    raw: str
    kind: str        # "ip" | "name"
    category: str    # loopback|private|link_local|metadata|public|unparseable


@dataclass(frozen=True)
class CanonicalViews:
    normalized: str
    collapsed: str
    sql_view: str
    hosts: list
    # 隱形字元「以空白代換」（而非移除）的視圖：邊界零寬（ZWSP 取代空白，
    # 如 act[ZWSP]as）在 normalized 會黏字（actas）繞過 \s+ regex；spaced
    # 還原詞界供既有 word-boundary regex 比對。無隱形字元時與 normalized 逐字相同，
    # 不增誤判面；僅當隱形字元恰落在詞內時才可能製造出詞界（罕見，靠 \b 錨點收斂）。
    spaced: str = ""


def clean_text_for_downstream(text: str) -> str:
    """只移除隱形/零寬字元；不做 NFKC/URL-decode/SQL 移除。送 LLM/入庫用。"""
    return _INVISIBLE_RE.sub("", text)


def _bounded_url_decode(text: str, max_times: int) -> str:
    prev = text
    for _ in range(max_times):
        dec = unquote(prev)
        if dec == prev:
            break
        prev = dec
    return prev


def _normalize(text: str, max_url_decode: int, invisible_replacement: str = "") -> str:
    t = unicodedata.normalize("NFKC", text)   # 全形→半形、相容字元
    # 隱形字元處理：normalized 視圖移除（還原詞內零寬 a[ZWSP]ct→act）；
    # spaced 視圖以空白代換（還原邊界零寬 act[ZWSP]as→act as）
    t = _INVISIBLE_RE.sub(invisible_replacement, t)
    t = _bounded_url_decode(t, max_url_decode)  # URL decode（有界）
    # decode 可能重新引入隱形字元（%E2%80%8B → ZWSP），再處理一次
    t = _INVISIBLE_RE.sub(invisible_replacement, t)
    return t


def _strip_sql_comments(text: str) -> str:
    # 區塊註解：non-greedy + DOTALL，一律移除（空字串替換），使 UN/**/ION -> UNION
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # 行註解：只把標記本身（-- / #）換成空白，保留其後文字，避免
    # "# UNION SELECT password" 整行被吃掉而讓 SQL 偵測被繞過（單行 SQL 亦然）。
    # 不再承諾 UN--x\nION -> UNION 重組（罕見，且與單行 SQL 偵測衝突）。
    text = re.sub(r"(--|#)", " ", text)
    return text


def _expand_ip_host(host: str):
    """把 host 解析為 ipaddress 物件；支援整數/十六進位/短式/IPv6 bracket。回 None 表非 IP。"""
    h = host.strip()
    if h.startswith("[") and h.endswith("]"):     # IPv6 bracket
        h = h[1:-1]
        try:
            return ipaddress.ip_address(h)
        except ValueError:
            return None
    # 純十進位整數 IPv4（如 2130706433）
    if re.fullmatch(r"\d+", h):
        try:
            n = int(h)
            if 0 <= n <= 0xFFFFFFFF:
                return ipaddress.ip_address(n)
        except ValueError:
            return None
    # 十六進位 IPv4（如 0x7f000001）
    if re.fullmatch(r"0x[0-9a-fA-F]+", h):
        try:
            n = int(h, 16)
            if 0 <= n <= 0xFFFFFFFF:
                return ipaddress.ip_address(n)
        except ValueError:
            return None
    # 一般點分或短式（127.1 → 補零展開由 ip_address 不接受，手動補）
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    m = re.fullmatch(r"(\d+)\.(\d+)", h)   # 短式 a.b → a.0.0.b
    if m:
        try:
            return ipaddress.ip_address(f"{m.group(1)}.0.0.{m.group(2)}")
        except ValueError:
            return None
    return None


def _classify_ip(ip) -> str:
    if str(ip) in ("169.254.169.254",):
        return "metadata"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_private:
        return "private"
    return "public"


_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def _extract_hosts(normalized: str) -> list:
    hosts = []
    for url in _URL_RE.findall(normalized):
        parsed = urlparse(url)
        host = parsed.hostname or ""
        # urlparse 對 [::1] 會去掉括號；對整數/十六進位 host 需回原字串再解
        raw_host = host
        # 重新從 netloc 取（整數/hex host urlparse 也放在 hostname）
        ip = _expand_ip_host(host) if host else None
        if ip is not None:
            hosts.append(HostInfo(raw=raw_host, kind="ip", category=_classify_ip(ip)))
        elif host:
            hosts.append(HostInfo(raw=raw_host, kind="name", category="public"))
        else:
            hosts.append(HostInfo(raw=url, kind="name", category="unparseable"))
    return hosts


def canonicalize(text: str, max_url_decode: int = 2) -> CanonicalViews:
    normalized = _normalize(text, max_url_decode)
    collapsed = re.sub(r"[\W_]+", "", normalized).lower()
    sql_view = _strip_sql_comments(normalized)
    hosts = _extract_hosts(normalized)
    spaced = _normalize(text, max_url_decode, invisible_replacement=" ")
    return CanonicalViews(
        normalized=normalized, collapsed=collapsed, sql_view=sql_view, hosts=hosts,
        spaced=spaced,
    )
