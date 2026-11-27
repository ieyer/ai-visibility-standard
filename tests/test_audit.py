"""Offline tests for audit_engine.audit (ported from NegencyPro core/tests_audit.py).

The network layer (audit_engine.audit._fetch) is replaced by a configurable
fake site, so these tests never touch the network.
"""

from __future__ import annotations

import gzip
import http.client
import unittest
import urllib.error
from unittest import mock
from urllib.parse import urlparse

from audit_engine import audit


GOOD_HOME_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<title>Example Robotics | Smart Factory Automation</title>
<meta name="description" content="Example Robotics builds smart factory
automation systems for manufacturers worldwide, from robotic arms to
AI-driven quality inspection lines.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="https://example.com/">
<link rel="alternate" hreflang="en" href="https://example.com/en/">
<link rel="alternate" hreflang="zh" href="https://example.com/zh/">
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "Organization", "name": "Example Robotics"},
  {"@type": "WebSite", "url": "https://example.com/"},
  {"@type": "FAQPage", "mainEntity": []}
]}
</script>
</head>
<body>
<h1>Smart factory automation</h1>
<h2>What can Example automate for you?</h2>
<p>Robotic arms, vision inspection and line integration.</p>
<a href="/contact">Contact us</a>
<a href="/privacy">Privacy policy</a>
<a href="mailto:hello@example.com">hello@example.com</a>
<a href="https://www.linkedin.com/company/example">LinkedIn</a>
<a href="https://x.com/example">X</a>
</body>
</html>
"""

GOOD_ROBOTS = "User-agent: *\nAllow: /\n\nSitemap: https://example.com/sitemap.xml\n"

BLOCKING_ROBOTS = (
    "User-agent: *\nAllow: /\n\n"
    "User-agent: GPTBot\nDisallow: /\n"
)


def _resp(url, status=200, body=b"", headers=None, elapsed=0.2, error=None):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return {
        "url": url,
        "final_url": url,
        "status": status,
        "headers": {key.lower(): value for key, value in (headers or {}).items()},
        "body": body,
        "elapsed": elapsed,
        "error": error,
    }


class FakeSite:
    """A stand-in origin server driven by audit_engine.audit._fetch's contract."""

    def __init__(
        self,
        *,
        robots=GOOD_ROBOTS,
        llms="# Example\n" + "- Verified company fact line.\n" * 80,
        sitemap_urls=12,
        ai_txt=True,
        home_html=GOOD_HOME_HTML,
        bot_statuses=None,
    ):
        self.robots = robots
        self.llms = llms
        self.sitemap_urls = sitemap_urls
        self.ai_txt = ai_txt
        self.home_html = home_html
        self.bot_statuses = bot_statuses or {}

    def fetch(self, url, ua, timeout=6):
        path = urlparse(url).path or "/"
        if path == "/":
            for token, status in self.bot_statuses.items():
                if token in ua:
                    return _resp(url, status=status, body="blocked")
            return _resp(
                url,
                body=self.home_html,
                headers={
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Encoding": "gzip",
                },
            )
        if path == "/robots.txt":
            return _resp(url, body=self.robots, headers={"Content-Type": "text/plain"})
        if path == "/llms.txt":
            if self.llms is None:
                return _resp(url, status=404, body="not found")
            return _resp(url, body=self.llms, headers={"Content-Type": "text/plain"})
        if path == "/sitemap.xml":
            locs = "".join(
                f"<url><loc>https://example.com/p{i}/</loc></url>"
                for i in range(self.sitemap_urls)
            )
            return _resp(url, body=f"<urlset>{locs}</urlset>")
        if path == "/ai.txt":
            if not self.ai_txt:
                return _resp(url, status=404, body="not found")
            return _resp(url, body="# ai\n", headers={"Content-Type": "text/plain"})
        if path in ("/contact", "/privacy"):
            return _resp(url, body="<html><body>ok</body></html>")
        return _resp(url, status=404, body="not found")


def _checks_by_id(result, category):
    return {check["id"]: check for check in result["categories"][category]["checks"]}


class RunAuditTests(unittest.TestCase):
    def run_with(self, site, domain="example.com"):
        with mock.patch("audit_engine.audit._fetch", site.fetch):
            return audit.run_audit(domain)

    def test_all_green_site_scores_high(self):
        result = self.run_with(FakeSite())
        self.assertIsNotNone(result["total_score"])
        self.assertGreaterEqual(result["total_score"], 85)
        self.assertEqual(
            set(result["categories"]),
            {
                "robots_access",
                "machine_readable",
                "entity_structure",
                "performance_basics",
                "trust_contact",
            },
        )
        total_checks = sum(
            len(category["checks"]) for category in result["categories"].values()
        )
        self.assertEqual(total_checks, 30)

    def test_https_url_input_accepted(self):
        result = self.run_with(FakeSite(), domain="https://example.com")
        self.assertEqual(result["domain"], "example.com")
        self.assertGreaterEqual(result["total_score"], 85)

    def test_gptbot_blocked_penalizes_robots_category(self):
        site = FakeSite(robots=BLOCKING_ROBOTS, bot_statuses={"GPTBot": 403})
        result = self.run_with(site)
        robots = result["categories"]["robots_access"]
        self.assertLessEqual(robots["score"], 80)
        checks = _checks_by_id(result, "robots_access")
        self.assertEqual(checks["robots_allows_gptbot"]["status"], "fail")
        self.assertEqual(checks["fetch_gptbot"]["status"], "fail")
        self.assertEqual(checks["robots_allows_claudebot"]["status"], "pass")
        top_ids = [issue["id"] for issue in result["top_issues"]]
        self.assertIn("robots_allows_gptbot", top_ids)

    def test_missing_llms_is_flagged(self):
        result = self.run_with(FakeSite(llms=None))
        checks = _checks_by_id(result, "machine_readable")
        self.assertNotEqual(checks["llms_exists"]["status"], "pass")
        self.assertEqual(checks["llms_substantial"]["status"], "info")
        top_ids = [issue["id"] for issue in result["top_issues"]]
        self.assertIn("llms_exists", top_ids)

    def test_unreachable_site_returns_error_not_exception(self):
        def dead_fetch(url, ua, timeout=6):
            return _resp(url, status=None, error="Name or service not known")

        with mock.patch("audit_engine.audit._fetch", dead_fetch):
            result = audit.run_audit("example.com")
        self.assertIsNone(result["total_score"])
        self.assertTrue(result["error"])
        self.assertEqual(result["categories"], {})

    def test_unreachable_error_text_is_customer_clean(self):
        def dead_fetch(url, ua, timeout=6):
            return _resp(url, status=None, error="[Errno -2] Name or service not known")

        with mock.patch("audit_engine.audit._fetch", dead_fetch):
            result = audit.run_audit("example.com")
        self.assertNotIn("Errno", result["error"])
        self.assertNotIn("Errno", result["error_en"])
        self.assertIn("域名解析失败", result["error"])
        self.assertIn("could not be resolved", result["error_en"])

    def test_invalid_domains_raise_value_error(self):
        for bad in ("", "not a domain", "http://", "nodot", "exa mple.com", "ftp://example.com"):
            with self.subTest(domain=bad):
                with self.assertRaises(ValueError):
                    audit.run_audit(bad)


class _FakeHTTPResponse:
    """Minimal stand-in for the object urllib.request.urlopen returns."""

    def __init__(self, status=200, body=b"", headers=None, read_exc=None):
        self.status = status
        self._body = body
        self._headers = headers or {}
        self._read_exc = read_exc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def geturl(self):
        return "https://example.com/"

    @property
    def headers(self):
        return self._headers

    def read(self, n=-1):
        if self._read_exc is not None:
            raise self._read_exc
        return self._body


class FetchRobustnessTests(unittest.TestCase):
    """_fetch under an egress proxy that cuts responses short (A02c).

    Observed failure: the homepage gzip stream cut at 8029 bytes, and
    flaky "Remote end closed connection without response" resets. The
    old code lost the whole fetch in both cases — truncated gzip raised
    an uncaught EOFError out of run_audit, and IncompleteRead.partial
    was discarded.
    """

    def _gzip_home(self):
        return gzip.compress(GOOD_HOME_HTML.encode("utf-8"))

    def _gzip_headers(self):
        return {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Encoding": "gzip",
        }

    def test_truncated_gzip_decodes_prefix_not_exception(self):
        raw = self._gzip_home()
        cut = raw[: len(raw) // 2]
        with self.assertRaises(EOFError):
            gzip.decompress(cut)
        decoded = audit._decode_body(cut, "gzip")
        self.assertTrue(decoded.startswith(b"<!DOCTYPE html>"))
        self.assertIn(b"<title>", decoded)

    def test_truncated_gzip_too_short_for_any_output_returns_empty(self):
        raw = self._gzip_home()
        decoded = audit._decode_body(raw[:10], "gzip")
        self.assertEqual(decoded, b"")

    def test_fetch_retries_remote_disconnect_then_succeeds(self):
        ok = _FakeHTTPResponse(
            status=200, body=b"hello", headers={"Content-Type": "text/plain"})
        calls = {"n": 0}

        def flaky(request, timeout=6):
            calls["n"] += 1
            if calls["n"] < 3:
                raise http.client.RemoteDisconnected(
                    "Remote end closed connection without response")
            return ok

        with mock.patch("urllib.request.urlopen", side_effect=flaky):
            result = audit._fetch("https://example.com/", audit.BROWSER_UA)
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["body"], b"hello")
        self.assertIsNone(result["error"])
        self.assertEqual(calls["n"], 3)

    def test_fetch_salvages_partial_after_repeated_truncation(self):
        gz = self._gzip_home()
        cut = gz[: len(gz) // 2]
        exc = http.client.IncompleteRead(partial=cut, expected=len(gz))
        resp = _FakeHTTPResponse(
            status=200, headers=self._gzip_headers(), read_exc=exc)
        with mock.patch("urllib.request.urlopen", return_value=resp):
            result = audit._fetch("https://example.com/", audit.BROWSER_UA)
        self.assertEqual(result["status"], 200)
        self.assertIsNone(result["error"])
        self.assertTrue(result["body"].startswith(b"<!DOCTYPE html>"))
        self.assertIn(b"<title>", result["body"])

    def test_fetch_empty_partial_after_retries_reports_error(self):
        exc = http.client.IncompleteRead(partial=b"", expected=20458)
        resp = _FakeHTTPResponse(
            status=200, headers=self._gzip_headers(), read_exc=exc)
        calls = {"n": 0}

        def counting(request, timeout=6):
            calls["n"] += 1
            return resp

        with mock.patch("urllib.request.urlopen", side_effect=counting):
            result = audit._fetch("https://example.com/", audit.BROWSER_UA)
        self.assertIsNone(result["status"])
        self.assertEqual(result["body"], b"")
        self.assertTrue(result["error"])
        self.assertEqual(calls["n"], 3)

    def test_fetch_does_not_retry_http_error(self):
        calls = {"n": 0}

        def erroring(request, timeout=6):
            calls["n"] += 1
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, None)

        with mock.patch("urllib.request.urlopen", side_effect=erroring):
            result = audit._fetch("https://example.com/", audit.BROWSER_UA)
        self.assertEqual(result["status"], 404)
        self.assertEqual(calls["n"], 1)

    def test_run_audit_survives_truncated_homepage(self):
        gz = self._gzip_home()
        cut = gz[: len(gz) // 2]
        partial_exc = http.client.IncompleteRead(partial=cut, expected=len(gz))

        def fake_urlopen(request, timeout=6):
            path = urlparse(request.full_url).path or "/"
            if path == "/":
                return _FakeHTTPResponse(
                    status=200, headers=self._gzip_headers(),
                    read_exc=partial_exc)
            return _FakeHTTPResponse(status=200, body=b"", headers={})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = audit.run_audit("example.com")
        self.assertIsNotNone(result["total_score"])
        checks = _checks_by_id(result, "entity_structure")
        self.assertEqual(checks["title_tag"]["status"], "pass")
