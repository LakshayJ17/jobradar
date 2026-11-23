"""Regression tests for the security audit: hostile job text must not crash a run, run code, break the report,
smuggle links, write escape sequences to your terminal, or let one posting rescore others."""

import json
import re
import subprocess
from html.parser import HTMLParser

import httpx
import pytest

from jobradar import notify
from jobradar.cli import main
from jobradar.export import write_xlsx
from jobradar.models import Job
from jobradar.report import render_html, render_markdown, write_csv
from jobradar.store import Store
from jobradar.util import clean_line, safe_url, today

EVIL = "Evil\x1b]52;c;Y3VybCBldmlsIHwgc2g=\x07Co\x1b[2J"


def _store(tmp_path, **kw):
    store = Store(tmp_path / "jobs.json")
    base = dict(source="remote", board="x", company="Acme", native_id="1", title="SDE", url="https://ex.com/1",
                location="Remote", posted_at=today().isoformat(), description="Build things.")
    base.update(kw)
    rec = store.add(Job(**base), rule_score=70, signals=[], min_years=None)
    return store, rec


def test_control_characters_never_reach_storage(tmp_path):
    store, rec = _store(tmp_path, company=EVIL, title="SDE\n::error::rotate your key", url="https://ex.com/1\x0b",
                        description="line one\r\nline two\x1b[31m red‮")
    assert "\x1b" not in rec["company"] and "\x07" not in rec["company"]
    assert rec["title"] == "SDE ::error::rotate your key"  # one line: can't fake a CI annotation
    assert rec["description"] == "line one\nline two[31m red" and rec["url"] == "https://ex.com/1"
    store.save()
    raw = json.loads((tmp_path / "jobs.json").read_text())
    raw["jobs"][0]["company"] = EVIL  # an old or hand-edited tracker is cleaned on load
    (tmp_path / "jobs.json").write_text(json.dumps(raw))
    assert "\x1b" not in Store(tmp_path / "jobs.json").all()[0]["company"]


def test_cli_prints_no_escape_sequences(tmp_path, monkeypatch, capsys):
    store, rec = _store(tmp_path, company="Acme")
    rec["company"], rec["ai"] = EVIL, {"why": "ok\x1b]0;pwned\x07", "verdict": "apply", "fit": 80}
    store.save()
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "jobs.json").rename(tmp_path / "data" / "jobs.json")
    main(["show", str(rec["id"])])
    main(["list"])
    assert "\x1b" not in capsys.readouterr().out


def test_spreadsheet_survives_hostile_urls_and_text(tmp_path):
    store, rec = _store(tmp_path)
    rec["url"] = "https://ex.com/1\x0b"  # as an old tracker might hold it
    rec["title"] = "=HYPERLINK(\"http://evil\")" + "x" * 40000
    path = write_xlsx(store, tmp_path / "jobs.xlsx")
    from openpyxl import load_workbook

    ws = load_workbook(path)["All tracked"]
    assert ws["O2"].hyperlink.target == "https://ex.com/1"  # control character dropped, plain link kept
    assert ws["E2"].data_type == "s" and len(ws["E2"].value) <= 32000
    rec["url"] = "javascript:alert(1)"
    assert load_workbook(write_xlsx(store, tmp_path / "j2.xlsx"))["All tracked"]["O2"].hyperlink is None


def test_html_report_data_is_valid_json_and_cannot_break_out(tmp_path):
    store, rec = _store(tmp_path, title="SDE <!-- remote </script><script>alert(1)</script>", url="javascript:alert(1)")
    page = render_html(store, {})
    data = page.split('id="data">')[1].split("</script>")[0]
    parsed = json.loads(data)
    assert parsed["jobs"][0]["title"].startswith("SDE <!-- remote </script>") and parsed["jobs"][0]["url"] == "#"

    class Scripts(HTMLParser):
        count = 0

        def handle_starttag(self, tag, attrs):
            self.count += tag == "script"

    p = Scripts()
    p.feed(page)
    assert p.count == 2  # only the report's own data and code blocks
    node = subprocess.run(["node", "-e", "JSON.parse(require('fs').readFileSync(0,'utf8'))"], input=data,
                          capture_output=True, text=True) if _has_node() else None
    assert node is None or node.returncode == 0


def _has_node() -> bool:
    try:
        return subprocess.run(["node", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def test_markdown_report_has_no_html_images_or_bad_links(tmp_path):
    store, rec = _store(tmp_path, title="<img src=https://evil.test/p.png> ![x](https://evil.test/i.png)",
                        url="javascript:alert(7)")
    rec["ai"] = {"why": "[click](https://evil.test/phish) <a href=x>", "verdict": "apply", "fit": 90}
    good = store.add(Job(source="remote", board="x", company="Good", native_id="2", title="SDE",
                         url="https://ex.com/a_(b)", posted_at=today().isoformat()), rule_score=60, signals=[],
                     min_years=None)
    from markdown_it import MarkdownIt

    # rendered the way GitHub's run summary or a markdown preview would, with raw HTML allowed
    page = MarkdownIt("commonmark", {"html": True}).enable("table").render(render_markdown(store, {}))
    assert re.findall(r"<img", page) == [] and "evil.test" not in "".join(re.findall(r'href="[^"]*"', page))
    assert set(re.findall(r'<a href="([^"]*)"', page)) == {good["url"]}


def test_csv_formula_prefixes_including_leading_space(tmp_path):
    store, rec = _store(tmp_path, title=" =1+1", company="@SUM(A1)")
    write_csv(store, tmp_path / "jobs.csv")
    text = (tmp_path / "jobs.csv").read_text()
    assert "'=1+1" in text and "'@SUM(A1)" in text  # stored trimmed, then prefixed
    rec["title"] = " =1+1"  # an untrimmed value (older tracker) is caught too
    write_csv(store, tmp_path / "jobs.csv")
    assert "' =1+1" in (tmp_path / "jobs.csv").read_text()


def test_digest_links_and_splitting(tmp_path, monkeypatch):
    store, rec = _store(tmp_path, title="A\n\n<b>B" + "x" * 5000, url="javascript:alert(1)")
    plain, rich = notify._line(rec, store)
    assert "\n\n" not in rich and "javascript:" not in rich and len(rich) < 600
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body["text"])
        assert len(body["text"]) <= 4096 and body["text"].count("<a ") == body["text"].count("</a>")
        return httpx.Response(200, json={"ok": True})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    items = [rec] * 40
    _, _, rich_all = notify.build_digest(items, store, 1)
    assert notify.send_telegram(rich_all) == "sent" and len(sent) >= 2


def test_network_errors_dont_crash_delivery(monkeypatch, tmp_path):
    def boom(request):
        raise httpx.ConnectError("down")

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(boom)))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    assert notify.send_telegram("hi") == "failed (ConnectError)"
    f = tmp_path / "x.xlsx"
    f.write_bytes(b"x")
    assert notify.send_telegram_file(f, "c") == "failed (ConnectError)"


@pytest.mark.parametrize("url,ok", [
    ("https://ex.com/a?b=1", True), ("http://ex.com", True), ("javascript:alert(1)", False),
    ("data:text/html,x", False), ("https://ex.com/\"onmouseover=x", False), ("file:///etc/passwd", False),
    ("tg://resolve?domain=x", False), (" https://ex.com ", True),
])
def test_safe_url(url, ok):
    assert bool(safe_url(url)) is ok


def test_clean_line_removes_bidi_and_zero_width():
    assert clean_line("Acme‮evil​ Co\n") == "Acmeevil Co"
    assert re.search(r"[\x00-\x1f\x7f]", clean_line("a\x00b\x1b[31mc\x7f")) is None


# ---------------------------------------------------------------------------- HTTP: redirects, size, time, policy

import asyncio  # noqa: E402
import time  # noqa: E402

from jobradar import http as jhttp  # noqa: E402
from jobradar.http import Blocked, FetchError, Http, Robots, nofetch_reason  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _client(handler, public=True):
    h = Http(transport=httpx.MockTransport(handler))
    if public is not None:
        async def fake_public(host):
            return public and host not in ("127.0.0.1", "169.254.169.254", "localhost")
        h._public_host = fake_public
    return h


@pytest.mark.parametrize("location,why", [
    ("http://127.0.0.1:8080/admin", "private"),
    ("http://169.254.169.254/latest/meta-data/", "private"),
    ("https://www.linkedin.com/jobs/view/1", "no-fetch"),
    ("file:///etc/passwd", "non-web"),
])
def test_redirects_are_checked_on_every_hop(location, why):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": location})

    async def go():
        async with _client(handler) as h:
            await h.get_json("https://boards-api.example.com/v1/jobs")

    with pytest.raises(Blocked, match=why):
        _run(go())
    assert all("127.0.0.1" not in u and "169.254" not in u and "linkedin" not in u for u in seen)


def test_safe_redirects_still_work_and_posts_are_not_redirected():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new?x=1"})
        if request.url.path == "/post":
            return httpx.Response(307, headers={"location": "https://other.example.com/post"})
        return httpx.Response(200, json={"ok": str(request.url)})

    async def go():
        async with _client(handler) as h:
            got = await h.get_json("https://a.example.com/old", params={"q": "1"})
            with pytest.raises(FetchError, match="not followed"):
                await h.post_json("https://a.example.com/post", {"x": 1})
            return got

    assert _run(go()) == {"ok": "https://a.example.com/new?x=1"}


def test_responses_are_capped_after_decompression(monkeypatch):
    monkeypatch.setattr(jhttp, "MAX_BYTES", 1000)
    import gzip

    bomb = gzip.compress(b"0" * 50_000)

    def handler(request):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=bomb)

    async def go():
        async with _client(handler) as h:
            await h.get_json("https://a.example.com/jobs")

    with pytest.raises(FetchError, match="more than"):
        _run(go())


def test_a_slow_server_cannot_hold_the_run(monkeypatch):
    monkeypatch.setattr(jhttp, "REQUEST_DEADLINE", 0.5)

    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                await asyncio.sleep(0.1)
                yield b" "

    def handler(request):
        return httpx.Response(200, stream=Drip())

    async def go():
        async with _client(handler) as h:
            await h.get_json("https://a.example.com/jobs")

    start = time.monotonic()
    with pytest.raises(FetchError, match="took over"):
        _run(go())
    assert time.monotonic() - start < 3


@pytest.mark.parametrize("url", ["https://x:y@www.linkedin.com/jobs", "https://x@linkedin.com/jobs",
                                 "https://www.linkedin.com./jobs", "https://WWW.NAUKRI.COM.:443/x"])
def test_no_fetch_list_sees_the_real_host(url):
    assert nofetch_reason(url)


def test_robots_wildcards_are_linear_and_agents_match_exactly():
    rules = Robots("User-agent: *\nDisallow: /" + "*" * 40 + "Q\n")
    start = time.monotonic()
    assert rules.allowed("/wday/cxs/acme/ext/jobs" * 20)
    assert time.monotonic() - start < 0.5
    assert not Robots("User-agent: a\nAllow: /\n\nUser-agent: *\nDisallow: /\n").allowed("/x")
    assert Robots("User-agent: JobRadar\nAllow: /\n\nUser-agent: *\nDisallow: /\n").allowed("/x")
    r = Robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /private\nAllow: /private/ok\n")
    assert not r.allowed("/a/b.pdf") and r.allowed("/a/b.pdf?x") and not r.allowed("/private/x")
    assert r.allowed("/private/ok/1") and r.allowed("/public")


def test_job_pages_that_mention_captcha_are_not_bot_walls():
    page = "<html><title>SDE</title><p>Access denied errors and CAPTCHA flows are part of this role.</p></html>"

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=page)

    async def go():
        async with _client(handler) as h:
            return [await h.get_html(f"https://jobs.example.com/{i}") for i in range(3)]

    assert len(_run(go())) == 3
