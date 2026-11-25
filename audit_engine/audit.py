"""AI visibility audit engine (Negency v1 体检引擎，独立版).

Standalone extraction of the audit engine behind Negency's public
"AI 可见度体检" tool, taken from the NegencyPro website repository
(commit f065726, file ``core/audit.py``). The only change from the
production copy is the removal of the single Django dependency
(``django.utils.timezone`` -> stdlib ``datetime``); all scoring logic,
weights and check definitions are byte-identical in behaviour.

Given a domain, it fetches the homepage (as a browser and as three AI
crawlers), robots.txt, llms.txt, sitemap.xml and a few well-known paths,
then scores about 30 signals in five weighted categories. Pure standard
library (urllib); the network layer is the single function ``_fetch`` so
tests can patch it and run fully offline.

Design notes:
- ``_fetch`` only offers ``Accept-Encoding: gzip, deflate`` because the
  standard library cannot decode Brotli; virtually all servers fall back
  to gzip when it is offered, so the compression check reads the homepage
  response's Content-Encoding header.
- An unreachable site is a result, not an exception: ``run_audit`` returns
  ``total_score=None`` plus an ``error`` field. ``ValueError`` is reserved
  for malformed domain input.
"""

from __future__ import annotations

import gzip
import html as html_lib
import http.client
import json
import re
import time
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone


FETCH_TIMEOUT = 6  # seconds per request; the whole audit stays well under ~25s
_MAX_BODY_BYTES = 800_000

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
GPTBOT_UA = "Mozilla/5.0 (compatible; GPTBot/1.2; +https://openai.com/gptbot)"
CLAUDEBOT_UA = "Mozilla/5.0 (compatible; ClaudeBot/1.0; +https://claude.com/bot)"
OAI_SEARCHBOT_UA = (
    "Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)"
)

# Category weights for the total score; keys are stable API surface.
CATEGORY_WEIGHTS = {
    "robots_access": 0.30,
    "machine_readable": 0.25,
    "entity_structure": 0.20,
    "performance_basics": 0.15,
    "trust_contact": 0.10,
}
CATEGORY_LABELS = {
    "robots_access": ("机器人可达性", "Bot accessibility"),
    "machine_readable": ("机器可读面", "Machine-readable surface"),
    "entity_structure": ("实体与内容结构", "Entity & content structure"),
    "performance_basics": ("性能与基础", "Performance & basics"),
    "trust_contact": ("信任与联系", "Trust & contact"),
}

_STATUS_POINTS = {"pass": 1.0, "warn": 0.5, "fail": 0.0}  # "info" is excluded

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-challenge",
    "challenge-platform",
    "/cdn-cgi/challenge",
    "attention required",
    "checking your browser",
)


# --- Input handling -------------------------------------------------------


def _normalize_domain(raw: str) -> str:
    """Accept ``example.com`` or ``https://example.com/...``; return the host.

    Raises ValueError for anything that is not a plausible public domain.
    """
    if not isinstance(raw, str):
        raise ValueError("域名必须是字符串")
    value = raw.strip().lower()
    if not value:
        raise ValueError("域名为空")
    if "://" in value:
        scheme, value = value.split("://", 1)
        if scheme not in ("http", "https"):
            raise ValueError(f"不支持的协议：{scheme}")
    host = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.rsplit("@", 1)[-1]  # drop any userinfo
    if ":" in host:
        host, _, port = host.partition(":")
        if not port.isdigit():
            raise ValueError(f"非法端口：{port}")
    host = host.strip().rstrip(".")
    if not _DOMAIN_RE.match(host):
        raise ValueError(f"非法域名：{raw!r}")
    return host


# --- Fetch layer (patched by tests) ---------------------------------------


def _decode_body(raw: bytes, content_encoding: str) -> bytes:
    encoding = (content_encoding or "").lower()
    try:
        if "gzip" in encoding:
            return gzip.decompress(raw)
        if "deflate" in encoding:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        return raw
    return raw


def _fetch(url: str, ua: str, timeout: int = FETCH_TIMEOUT) -> dict:
    """GET ``url`` and return a plain dict; network errors never raise.

    Keys: url, final_url, status (int or None), headers (lower-cased),
    body (bytes, decompressed), elapsed (seconds), error (str or None).
    """
    started = time.monotonic()
    result = {
        "url": url,
        "final_url": url,
        "status": None,
        "headers": {},
        "body": b"",
        "elapsed": 0.0,
        "error": None,
    }
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result["status"] = response.status
            result["final_url"] = response.geturl()
            result["headers"] = {
                key.lower(): value for key, value in response.headers.items()
            }
            raw = response.read(_MAX_BODY_BYTES)
            result["body"] = _decode_body(
                raw, result["headers"].get("content-encoding", "")
            )
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        result["final_url"] = exc.geturl() or url
        if exc.headers is not None:
            result["headers"] = {
                key.lower(): value for key, value in exc.headers.items()
            }
        try:
            raw = exc.read(_MAX_BODY_BYTES)
            result["body"] = _decode_body(
                raw, result["headers"].get("content-encoding", "")
            )
        except Exception:
            result["body"] = b""
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        result["error"] = str(getattr(exc, "reason", exc))
    result["elapsed"] = round(time.monotonic() - started, 3)
    return result


_ERROR_DETAILS = (
    (
        ("name or service not known", "name resolution", "getaddrinfo", "nodename nor servname"),
        ("域名解析失败", "the domain name could not be resolved"),
    ),
    (("timed out", "timeout"), ("连接超时", "the connection timed out")),
    (
        ("certificate", "ssl", "tls"),
        ("TLS 证书校验失败", "the TLS certificate could not be verified"),
    ),
    (("connection refused",), ("连接被拒绝", "the connection was refused")),
    (
        ("connection reset", "remote end closed", "connection aborted"),
        ("连接被对方中断", "the connection was closed by the site"),
    ),
)


def _describe_error(reason: str) -> tuple[str, str]:
    """Map a raw fetch exception string to a clean bilingual detail.

    Raw exception text (``[Errno -2] Name or service not known``) must
    never reach the customer-facing result page.
    """
    text = (reason or "").lower()
    for needles, pair in _ERROR_DETAILS:
        if any(needle in text for needle in needles):
            return pair
    return ("网络连接失败", "a network error occurred")


def _text(resp: dict) -> str:
    if resp["status"] is None or not resp["body"]:
        return ""
    charset = "utf-8"
    match = re.search(r"charset=([\w-]+)", resp["headers"].get("content-type", ""))
    if match:
        charset = match.group(1)
    try:
        return resp["body"].decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return resp["body"].decode("utf-8", errors="replace")


def _looks_like_challenge(resp: dict) -> bool:
    if resp["headers"].get("cf-mitigated", "").lower() == "challenge":
        return True
    text = _text(resp).lower()
    return any(marker in text for marker in _CHALLENGE_MARKERS)


# --- robots.txt ------------------------------------------------------------


def _parse_robots(text: str) -> list[dict]:
    """Return groups: [{"agents": {lower token,...}, "rules": [(kind, path)]}]."""
    groups: list[dict] = []
    current: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if current is None or current["rules"]:
                current = {"agents": set(), "rules": []}
                groups.append(current)
            current["agents"].add(value.lower())
        elif key in ("allow", "disallow") and current is not None:
            current["rules"].append((key, value))
    return groups


def _robots_allows(groups: list[dict], agent: str, path: str = "/") -> bool:
    """Longest-match robots evaluation for one agent token and path."""
    matched = [g for g in groups if agent.lower() in g["agents"]]
    if not matched:
        matched = [g for g in groups if "*" in g["agents"]]
    if not matched:
        return True
    best_len = -1
    allowed = True
    for group in matched:
        for kind, rule_path in group["rules"]:
            if not rule_path or not path.startswith(rule_path):
                continue
            if len(rule_path) > best_len or (
                len(rule_path) == best_len and kind == "allow"
            ):
                best_len = len(rule_path)
                allowed = kind == "allow"
    return allowed


# --- Homepage HTML helpers --------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_JSONLD_RE = re.compile(
    r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def _attr(tag: str, name: str) -> str:
    match = re.search(rf"{name}\s*=\s*[\"']([^\"']*)[\"']", tag, re.IGNORECASE)
    return match.group(1) if match else ""


def _clean_text(fragment: str) -> str:
    return html_lib.unescape(_TAG_RE.sub(" ", fragment)).strip()


def _meta_content(text: str, name: str) -> str:
    for tag in _META_TAG_RE.findall(text):
        if _attr(tag, "name").lower() == name:
            return _attr(tag, "content")
    return ""


def _jsonld_types(text: str) -> set[str]:
    types: set[str] = set()

    def collect(node) -> None:
        if isinstance(node, dict):
            node_type = node.get("@type")
            if isinstance(node_type, str):
                types.add(node_type)
            elif isinstance(node_type, list):
                types.update(item for item in node_type if isinstance(item, str))
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    for block in _JSONLD_RE.findall(text):
        try:
            collect(json.loads(block.strip()))
        except (ValueError, RecursionError):
            continue
    return types


def _hrefs(text: str) -> list[str]:
    return re.findall(r"href\s*=\s*[\"']([^\"']*)[\"']", text, re.IGNORECASE)


# --- Check construction -----------------------------------------------------


def _check(
    check_id: str,
    label_zh: str,
    label_en: str,
    status: str,
    detail: str,
    detail_en: str = "",
) -> dict:
    return {
        "id": check_id,
        "label_zh": label_zh,
        "label_en": label_en,
        "status": status,
        "detail": detail,
        "detail_en": detail_en,
    }


def _score_checks(checks: list[dict]) -> int:
    earned = sum(_STATUS_POINTS[c["status"]] for c in checks if c["status"] != "info")
    applicable = sum(1 for c in checks if c["status"] != "info")
    if applicable == 0:
        return 0
    return round(100 * earned / applicable)


def _bot_fetch_check(check_id: str, label_zh: str, label_en: str, resp: dict) -> dict:
    if resp["status"] is None:
        return _check(check_id, label_zh, label_en, "warn", f"实测抓取失败：{resp['error']}", f"Live fetch failed: {resp['error']}")
    if resp["status"] in (401, 403):
        suffix = "（疑似防火墙/挑战拦截）" if _looks_like_challenge(resp) else ""
        suffix_en = " (possible firewall/challenge block)" if _looks_like_challenge(resp) else ""
        return _check(
            check_id, label_zh, label_en, "fail", f"实测抓取被拒绝：HTTP {resp['status']}{suffix}", f"Live fetch refused: HTTP {resp['status']}{suffix_en}"
        )
    if resp["status"] == 200:
        if _looks_like_challenge(resp):
            return _check(
                check_id, label_zh, label_en, "warn", "返回 200 但疑似验证码/挑战页，机器人可能拿不到正文", "Returned 200 but looks like a CAPTCHA/challenge page; bots may not get the real content"
            )
        return _check(check_id, label_zh, label_en, "pass", "实测抓取成功（HTTP 200）", "Live fetch succeeded (HTTP 200)")
    return _check(check_id, label_zh, label_en, "warn", f"实测抓取状态异常：HTTP {resp['status']}", f"Live fetch returned an unexpected status: HTTP {resp['status']}")


def _robots_checks(ctx: dict) -> list[dict]:
    resp = ctx["robots"]
    checks = []
    exists = resp["status"] == 200
    checks.append(
        _check(
            "robots_exists",
            "robots.txt 存在",
            "robots.txt present",
            "pass" if exists else "fail",
            "已找到 robots.txt" if exists else "未找到 robots.txt，爬虫缺少官方抓取规则",
            "robots.txt found" if exists else "No robots.txt found; crawlers have no official crawling rules",
        )
    )
    bots = [
        ("gptbot", "GPTBot"),
        ("claudebot", "ClaudeBot"),
        ("oai-searchbot", "OAI-SearchBot"),
        ("perplexitybot", "PerplexityBot"),
        ("google-extended", "Google-Extended"),
    ]
    for token, display in bots:
        check_id = f"robots_allows_{token.replace('-', '_')}"
        if not exists:
            checks.append(
                _check(
                    check_id,
                    f"放行 {display}",
                    f"Allows {display}",
                    "warn",
                    "无 robots.txt：默认放行，但没有对 AI 爬虫做显式声明", "No robots.txt: allowed by default, but no explicit declaration for AI crawlers",
                )
            )
        elif _robots_allows(ctx["robots_groups"], token):
            checks.append(
                _check(check_id, f"放行 {display}", f"Allows {display}", "pass", f"robots.txt 放行 {display}", f"robots.txt allows {display}")
            )
        else:
            checks.append(
                _check(
                    check_id,
                    f"放行 {display}",
                    f"Allows {display}",
                    "fail",
                    f"robots.txt 对 {display} 设有 Disallow 规则，抓取被拒", f"robots.txt has a Disallow rule for {display}; crawling is refused",
                )
            )
    checks.append(_bot_fetch_check("fetch_gptbot", "GPTBot 实测抓取", "GPTBot live fetch", ctx["bot_gptbot"]))
    checks.append(_bot_fetch_check("fetch_claudebot", "ClaudeBot 实测抓取", "ClaudeBot live fetch", ctx["bot_claudebot"]))
    checks.append(
        _bot_fetch_check("fetch_oai_searchbot", "OAI-SearchBot 实测抓取", "OAI-SearchBot live fetch", ctx["bot_oai"])
    )
    return checks


def _machine_checks(ctx: dict) -> list[dict]:
    checks = []
    llms = ctx["llms"]
    llms_text = _text(llms)
    if llms["status"] == 200:
        checks.append(
            _check("llms_exists", "llms.txt 存在", "llms.txt present", "pass", "已提供 llms.txt", "llms.txt is provided")
        )
        length = len(llms_text)
        if length >= 1500:
            status, detail, detail_en = "pass", f"llms.txt 内容充实（{length} 字符）", f"llms.txt is substantial ({length} characters)"
        elif length >= 300:
            status, detail, detail_en = "warn", f"llms.txt 内容偏薄（{length} 字符），建议补全公司事实与页面索引", f"llms.txt is thin ({length} characters); add company facts and a page index"
        else:
            status, detail, detail_en = "warn", f"llms.txt 内容过少（{length} 字符），接近空壳", f"llms.txt is nearly empty ({length} characters)"
        checks.append(_check("llms_substantial", "llms.txt 内容充实度", "llms.txt substance", status, detail, detail_en))
    else:
        checks.append(
            _check(
                "llms_exists",
                "llms.txt 存在",
                "llms.txt present",
                "warn",
                "未提供 llms.txt：AI 代理缺少一份机器可读的公司说明书", "No llms.txt: AI agents lack a machine-readable company fact sheet",
            )
        )
        checks.append(
            _check("llms_substantial", "llms.txt 内容充实度", "llms.txt substance", "info", "无 llms.txt，跳过", "No llms.txt; skipped")
        )
    sitemap = ctx["sitemap"]
    if sitemap["status"] == 200:
        checks.append(_check("sitemap_exists", "sitemap.xml 存在", "sitemap.xml present", "pass", "已提供 sitemap.xml", "sitemap.xml is provided"))
        count = _text(sitemap).count("<loc>")
        if count >= 10:
            status, detail, detail_en = "pass", f"sitemap 收录 {count} 个 URL", f"Sitemap lists {count} URLs"
        elif count >= 1:
            status, detail, detail_en = "warn", f"sitemap 仅收录 {count} 个 URL，覆盖不足", f"Sitemap lists only {count} URLs; coverage is incomplete"
        else:
            status, detail, detail_en = "fail", "sitemap.xml 中没有找到任何 URL", "No URLs found in sitemap.xml"
        checks.append(_check("sitemap_size", "sitemap 收录量", "Sitemap URL count", status, detail, detail_en))
    else:
        checks.append(
            _check("sitemap_exists", "sitemap.xml 存在", "sitemap.xml present", "fail", "未找到 sitemap.xml，搜索引擎难以完整发现页面", "No sitemap.xml found; search engines cannot discover all pages")
        )
        checks.append(_check("sitemap_size", "sitemap 收录量", "Sitemap URL count", "info", "无 sitemap，跳过", "No sitemap; skipped"))
    if ctx["ai"]["status"] == 200:
        checks.append(_check("ai_txt", "ai.txt 存在（加分）", "ai.txt present (bonus)", "pass", "已提供 ai.txt", "ai.txt is provided"))
    else:
        checks.append(
            _check("ai_txt", "ai.txt 存在（加分）", "ai.txt present (bonus)", "info", "未提供 ai.txt（加分项，不扣分）", "No ai.txt (bonus item; no points deducted)")
        )
    types = ctx["jsonld_types"]
    if types:
        checks.append(
            _check(
                "jsonld_exists",
                "JSON-LD 结构化数据",
                "JSON-LD structured data",
                "pass",
                f"检测到 JSON-LD 类型：{', '.join(sorted(types))}", f"JSON-LD types detected: {', '.join(sorted(types))}",
            )
        )
        entity = sorted(types & {"Organization", "WebSite"})
        if entity:
            checks.append(
                _check(
                    "jsonld_entity_types",
                    "实体型 schema",
                    "Entity schema types",
                    "pass",
                    f"包含实体型 schema：{', '.join(entity)}，有助 AI 确认公司实体", f"Includes entity schema ({', '.join(entity)}), helping AI confirm the company entity",
                )
            )
        else:
            checks.append(
                _check(
                    "jsonld_entity_types",
                    "实体型 schema",
                    "Entity schema types",
                    "warn",
                    "有 JSON-LD 但缺少 Organization/WebSite 实体型 schema", "JSON-LD present but no Organization/WebSite entity schema",
                )
            )
    else:
        checks.append(
            _check("jsonld_exists", "JSON-LD 结构化数据", "JSON-LD structured data", "fail", "首页未检测到 JSON-LD 结构化数据", "No JSON-LD structured data detected on the homepage")
        )
        checks.append(
            _check("jsonld_entity_types", "实体型 schema", "Entity schema types", "info", "无 JSON-LD，跳过", "No JSON-LD; skipped")
        )
    return checks


def _structure_checks(ctx: dict) -> list[dict]:
    text = ctx["home_text"]
    checks = []
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    title = _clean_text(title_match.group(1)) if title_match else ""
    if not title:
        checks.append(_check("title_tag", "页面标题 title", "Title tag", "fail", "首页缺少 title 标签", "The homepage has no title tag"))
    elif 10 <= len(title) <= 65:
        checks.append(_check("title_tag", "页面标题 title", "Title tag", "pass", f"title 长度合适（{len(title)} 字符）", f"Title length is good ({len(title)} characters)"))
    else:
        checks.append(
            _check("title_tag", "页面标题 title", "Title tag", "warn", f"title 长度异常（{len(title)} 字符，建议 10–65）", f"Title length is off ({len(title)} characters; 10-65 recommended)")
        )
    description = _meta_content(text, "description").strip()
    if not description:
        checks.append(
            _check("meta_description", "页面描述 description", "Meta description", "warn", "首页缺少 meta description", "The homepage has no meta description")
        )
    elif 40 <= len(description) <= 170:
        checks.append(
            _check("meta_description", "页面描述 description", "Meta description", "pass", f"description 长度合适（{len(description)} 字符）", f"Description length is good ({len(description)} characters)")
        )
    else:
        checks.append(
            _check(
                "meta_description",
                "页面描述 description",
                "Meta description",
                "warn",
                f"description 长度异常（{len(description)} 字符，建议 40–170）", f"Description length is off ({len(description)} characters; 40-170 recommended)",
            )
        )
    h1_count = len(re.findall(r"<h1[\s>]", text, re.IGNORECASE))
    if h1_count == 1:
        checks.append(_check("h1_unique", "H1 唯一性", "Single H1", "pass", "首页恰好一个 H1", "Exactly one H1 on the homepage"))
    elif h1_count == 0:
        checks.append(_check("h1_unique", "H1 唯一性", "Single H1", "fail", "首页没有 H1，主题不明确", "No H1 on the homepage; the topic is unclear"))
    else:
        checks.append(
            _check("h1_unique", "H1 唯一性", "Single H1", "warn", f"首页有 {h1_count} 个 H1，主题信号被稀释", f"{h1_count} H1 tags on the homepage; the topic signal is diluted")
        )
    if any("hreflang" in tag.lower() for tag in _LINK_TAG_RE.findall(text)):
        checks.append(_check("hreflang", "hreflang 多语言标注", "hreflang annotations", "pass", "检测到 hreflang 标注", "hreflang annotations detected"))
    else:
        checks.append(
            _check(
                "hreflang",
                "hreflang 多语言标注",
                "hreflang annotations",
                "warn",
                "未检测到 hreflang：多语言站点应标注各语言版本对应关系", "No hreflang detected; multilingual sites should declare how language versions correspond",
            )
        )
    if any(_attr(tag, "rel").lower() == "canonical" for tag in _LINK_TAG_RE.findall(text)):
        checks.append(_check("canonical", "canonical 规范链接", "Canonical link", "pass", "已设置 canonical", "Canonical link is set"))
    else:
        checks.append(_check("canonical", "canonical 规范链接", "Canonical link", "warn", "未检测到 canonical 链接", "No canonical link detected"))
    headings = re.findall(r"<h[23][^>]*>(.*?)</h[23]>", text, re.IGNORECASE | re.DOTALL)
    has_question = any(("?" in _clean_text(h) or "？" in _clean_text(h)) for h in headings)
    if "FAQPage" in ctx["jsonld_types"] or has_question:
        checks.append(
            _check("faq_structure", "问答式内容结构", "Q&A content structure", "pass", "检测到 FAQ schema 或问句式标题，易被 AI 摘引", "FAQ schema or question-style headings detected; easy for AI to quote")
        )
    else:
        checks.append(
            _check(
                "faq_structure",
                "问答式内容结构",
                "Q&A content structure",
                "warn",
                "未检测到 FAQ schema 或问句式标题：问答结构更易被 AI 答案引用", "No FAQ schema or question-style headings; Q&A structure is quoted by AI answers more often",
            )
        )
    return checks


def _performance_checks(ctx: dict) -> list[dict]:
    home = ctx["home"]
    checks = []
    if home["final_url"].startswith("https://"):
        checks.append(_check("https_enabled", "HTTPS", "HTTPS enabled", "pass", "首页经 HTTPS 提供", "The homepage is served over HTTPS"))
    else:
        checks.append(_check("https_enabled", "HTTPS", "HTTPS enabled", "fail", "首页未启用 HTTPS（或未跳转到 HTTPS）", "The homepage is not on HTTPS (or does not redirect to it)"))
    status = home["status"]
    if status == 200:
        checks.append(_check("homepage_status", "首页可访问", "Homepage reachable", "pass", "浏览器访问首页返回 200", "Homepage returns 200 to a browser"))
    elif status is not None and status < 400:
        checks.append(
            _check("homepage_status", "首页可访问", "Homepage reachable", "warn", f"首页最终状态码 {status}", f"Final homepage status code: {status}")
        )
    else:
        checks.append(
            _check("homepage_status", "首页可访问", "Homepage reachable", "fail", f"首页返回 HTTP {status}", f"Homepage returns HTTP {status}")
        )
    elapsed = home["elapsed"]
    if elapsed <= 1.0:
        checks.append(_check("response_time", "首页响应时间", "Homepage response time", "pass", f"响应时间 {elapsed:.2f}s", f"Response time {elapsed:.2f}s"))
    elif elapsed <= 2.5:
        checks.append(
            _check("response_time", "首页响应时间", "Homepage response time", "warn", f"响应时间 {elapsed:.2f}s，偏慢", f"Response time {elapsed:.2f}s - on the slow side")
        )
    else:
        checks.append(
            _check("response_time", "首页响应时间", "Homepage response time", "fail", f"响应时间 {elapsed:.2f}s，过慢", f"Response time {elapsed:.2f}s - too slow")
        )
    encoding = home["headers"].get("content-encoding", "").lower()
    if "gzip" in encoding or "deflate" in encoding or "br" in encoding:
        checks.append(
            _check("compression", "响应压缩", "Response compression", "pass", f"已启用压缩（{encoding}）", f"Compression enabled ({encoding})")
        )
    else:
        checks.append(
            _check("compression", "响应压缩", "Response compression", "warn", "未检测到 gzip/br 压缩，页面传输体积偏大", "No gzip/br compression detected; page transfer size is larger than needed")
        )
    if _meta_content(ctx["home_text"], "viewport"):
        checks.append(_check("viewport", "移动端 viewport", "Mobile viewport", "pass", "已设置 viewport", "Viewport meta is set"))
    else:
        checks.append(
            _check("viewport", "移动端 viewport", "Mobile viewport", "fail", "缺少 viewport meta，移动端渲染会出问题", "Missing viewport meta; mobile rendering will break")
        )
    return checks


def _trust_checks(ctx: dict) -> list[dict]:
    hrefs = ctx["hrefs"]
    lowered = [h.lower() for h in hrefs]
    checks = []
    has_mailto = any(h.startswith("mailto:") for h in lowered)
    has_contact_link = any("contact" in h or "联系" in h for h in lowered)
    if has_mailto or has_contact_link or ctx["contact"]["status"] == 200:
        checks.append(_check("contact_reachable", "联系入口可达", "Contact entry reachable", "pass", "首页有联系入口且联系页可达", "The homepage has a contact entry and the contact page is reachable"))
    else:
        checks.append(
            _check(
                "contact_reachable",
                "联系入口可达",
                "Contact entry reachable",
                "warn",
                "首页未发现邮箱/联系页入口，联系页也不可达", "No email/contact entry on the homepage, and the contact page is unreachable",
            )
        )
    has_privacy_link = any("privacy" in h or "隐私" in h for h in lowered)
    if has_privacy_link or ctx["privacy"]["status"] == 200:
        checks.append(_check("privacy_exists", "隐私政策页", "Privacy page", "pass", "隐私政策页存在", "A privacy policy page exists"))
    else:
        checks.append(
            _check("privacy_exists", "隐私政策页", "Privacy page", "warn", "未发现隐私政策页，信任与合规信号缺失", "No privacy policy page found; a trust and compliance signal is missing")
        )
    social = re.compile(r"(?:https?:)?//(?:www\.)?(linkedin\.com|x\.com|twitter\.com)", re.IGNORECASE)
    found = sorted({match.group(1).lower() for match in (social.search(h) for h in hrefs) if match})
    if found:
        checks.append(
            _check("social_links", "社交主页链接", "Social profile links", "pass", f"检测到社交链接：{', '.join(found)}", f"Social links detected: {', '.join(found)}")
        )
    else:
        checks.append(
            _check("social_links", "社交主页链接", "Social profile links", "warn", "未检测到 LinkedIn/X 等社交主页链接", "No LinkedIn/X social profile links detected")
        )
    return checks


# --- Entry point -------------------------------------------------------------


def run_audit(domain: str) -> dict:
    """Audit ``domain`` and return the structured result described above.

    Raises ValueError only for malformed input; an unreachable site yields
    ``total_score=None`` with an ``error`` message instead.
    """
    host = _normalize_domain(domain)
    fetched_at = datetime.now(timezone.utc).isoformat()

    home = _fetch(f"https://{host}/", BROWSER_UA, timeout=FETCH_TIMEOUT)
    base = f"https://{host}"
    if home["status"] is None:
        fallback = _fetch(f"http://{host}/", BROWSER_UA, timeout=FETCH_TIMEOUT)
        if fallback["status"] is None:
            reason = home["error"] or fallback["error"] or ""
            reason_zh, reason_en = _describe_error(reason)
            return {
                "domain": host,
                "total_score": None,
                "error": f"无法访问该站点（{reason_zh}）",
                "error_en": f"The site could not be reached ({reason_en})",
                "categories": {},
                "top_issues": [],
                "fetched_at": fetched_at,
            }
        home, base = fallback, f"http://{host}"

    jobs = {
        "robots": (f"{base}/robots.txt", BROWSER_UA),
        "llms": (f"{base}/llms.txt", BROWSER_UA),
        "sitemap": (f"{base}/sitemap.xml", BROWSER_UA),
        "ai": (f"{base}/ai.txt", BROWSER_UA),
        "contact": (f"{base}/contact", BROWSER_UA),
        "privacy": (f"{base}/privacy", BROWSER_UA),
        "bot_gptbot": (f"{base}/", GPTBOT_UA),
        "bot_claudebot": (f"{base}/", CLAUDEBOT_UA),
        "bot_oai": (f"{base}/", OAI_SEARCHBOT_UA),
    }
    fetched: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {
            name: pool.submit(_fetch, url, ua, FETCH_TIMEOUT)
            for name, (url, ua) in jobs.items()
        }
        for name, future in futures.items():
            fetched[name] = future.result()

    home_text = _text(home)
    ctx = {
        "home": home,
        "home_text": home_text,
        "robots": fetched["robots"],
        "robots_groups": _parse_robots(_text(fetched["robots"])),
        "llms": fetched["llms"],
        "sitemap": fetched["sitemap"],
        "ai": fetched["ai"],
        "contact": fetched["contact"],
        "privacy": fetched["privacy"],
        "bot_gptbot": fetched["bot_gptbot"],
        "bot_claudebot": fetched["bot_claudebot"],
        "bot_oai": fetched["bot_oai"],
        "jsonld_types": _jsonld_types(home_text),
        "hrefs": _hrefs(home_text),
    }

    builders = {
        "robots_access": _robots_checks,
        "machine_readable": _machine_checks,
        "entity_structure": _structure_checks,
        "performance_basics": _performance_checks,
        "trust_contact": _trust_checks,
    }
    categories: dict[str, dict] = {}
    weighted_points = 0.0
    weight_total = 0.0
    issue_pool: list[tuple[int, float, dict]] = []
    for key, builder in builders.items():
        checks = builder(ctx)
        score = _score_checks(checks)
        label_zh, label_en = CATEGORY_LABELS[key]
        categories[key] = {
            "label_zh": label_zh,
            "label_en": label_en,
            "score": score,
            "checks": checks,
        }
        if any(c["status"] != "info" for c in checks):
            weighted_points += score * CATEGORY_WEIGHTS[key]
            weight_total += CATEGORY_WEIGHTS[key]
        for check in checks:
            if check["status"] in ("fail", "warn"):
                severity = 0 if check["status"] == "fail" else 1
                issue_pool.append((severity, -CATEGORY_WEIGHTS[key], {**check, "category": key}))
    issue_pool.sort(key=lambda item: (item[0], item[1]))
    total_score = round(weighted_points / weight_total) if weight_total else 0
    return {
        "domain": host,
        "total_score": total_score,
        "categories": categories,
        "top_issues": [item[2] for item in issue_pool[:5]],
        "fetched_at": fetched_at,
    }


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point: ``python -m audit_engine example.com``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Run the Negency AI visibility audit (v1 engine) for a domain."
    )
    parser.add_argument("domain", help="Domain to audit, e.g. example.com")
    args = parser.parse_args(argv)
    try:
        result = run_audit(args.domain)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
