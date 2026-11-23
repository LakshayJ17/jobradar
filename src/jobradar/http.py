"""Polite async HTTP for APIs and HTML scraping.

* robots.txt (RFC 9309, longest-match) is checked before every scrape / career-site request
* NOFETCH_DOMAINS: sites whose terms of service forbid automated access - never fetched, whatever the config says
* redirects are followed by hand (at most 5), and every hop is checked again: http(s) only, the no-fetch list,
  robots.txt when the first request needed it or the host changes, and never into private / loopback / link-local
  addresses (a hostile board can't point us at your router or a cloud metadata service). POSTs are not redirected.
* every response is capped at 20 MB after decompression (no gzip bombs) and every request has a 60 s deadline,
  however slowly the server drips bytes
* rate-limit buckets: parallelism + minimum spacing per site (Workday tenants share one budget)
* circuit breaker: two blocking answers (403/429/503 or a bot challenge) and the site is skipped for the run
* no proxies, no browser impersonation, no CAPTCHA solving - an honest User-Agent, always
"""

from __future__ import annotations

import asyncio
import ipaddress
import random
import re
import socket
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "jobradar/0.1 (personal job-search tool; low volume)"
AGENT_TOKEN = "jobradar"
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_BYTES = 20_000_000      # decoded body; job APIs and pages are far smaller
REQUEST_DEADLINE = 60.0     # seconds for one request, body included
MAX_REDIRECTS = 5
BLOCKING_STATUS = {401, 403, 429, 451, 503}

# Terms of service forbid scraping / automated access (checked 2026-11-17), or the site walls off
# non-browser clients so access would need impersonation. Matched on the domain and all its subdomains.
NOFETCH_DOMAINS = {
    "linkedin.com": "ToS + robots.txt forbid automated access",
    "indeed.com": "ToS forbids scraping",
    "glassdoor.com": "ToS forbids scraping",
    "glassdoor.co.in": "ToS forbids scraping",
    "ambitionbox.com": "ToS forbids crawling / automated extraction (company reviews: use the report's reviews link)",
    "naukri.com": "serves bot-walled pages to automated clients",
    "instahyre.com": "ToS: no scraping without written consent",
    "cutshort.io": "ToS: no automated access",
    "hirist.tech": "ToS: no crawling / scraping",
    "freshersworld.com": "ToS: no automated agents / data mining",
    "workatastartup.com": "YC ToS: no scraping / robots",
    "ycombinator.com": "YC ToS: no scraping / robots",
    "cisco.com": "ToS: no crawling / bots",
    "uber.com": "robots.txt disallows the jobs API",
    "higher.gs.com": "robots.txt disallows",
}

# Rate-limit buckets: (parallel requests, min seconds between requests).
BUCKET_SUFFIXES = {".myworkdayjobs.com": "workday"}
BUCKET_LIMITS = {
    "workday": (2, 0.6),
    "apply.workable.com": (1, 1.0),
    "www.google.com": (1, 1.5),
    "internshala.com": (1, 1.5),
    "www.shine.com": (1, 1.5),
    "wellfound.com": (1, 3.0),
    "unstop.com": (1, 1.0),
}
# Interstitial pages that stand in for the content (Cloudflare, "are you a robot"). Not just any page that mentions a
# CAPTCHA: job pages talk about access and captchas too.
_CHALLENGE_RX = re.compile(r"cf-chl|/cdn-cgi/challenge-platform|<title>\s*(?:just a moment|attention required|access "
                           r"denied)|verify (?:that )?you(?: are|'re) (?:a )?human|are you a robot", re.I)


class FetchError(Exception):
    pass


class Blocked(FetchError):
    """robots.txt, the no-fetch list or the circuit breaker forbids this request - we never retry around it."""


def bucket_of(host: str) -> str:
    return next((name for suffix, name in BUCKET_SUFFIXES.items() if host.endswith(suffix)), host)


def host_of(url: str) -> str:
    """The real host: no userinfo ("x@linkedin.com"), port or trailing dot ("linkedin.com.")."""
    try:
        return (httpx.URL(url).host or "").rstrip(".").lower()
    except (httpx.InvalidURL, TypeError, ValueError):
        return ""


def nofetch_reason(url: str) -> str | None:
    host = host_of(url)
    for domain, reason in NOFETCH_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return reason
    return None


class Robots:
    """Minimal robots.txt evaluator following RFC 9309 (most specific / longest rule wins, Allow wins ties)."""

    def __init__(self, text: str, agent: str = AGENT_TOKEN) -> None:
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        in_agents = False
        for raw in (text or "").splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            name, value = (part.strip() for part in line.split(":", 1))
            name = name.lower()
            if name == "user-agent":
                if not in_agents and (agents or rules):
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
                in_agents = True
            elif name in ("allow", "disallow"):
                rules.append((name == "allow", value))
                in_agents = False
        if agents or rules:
            groups.append((agents, rules))
        mine = [r for a, r in groups if agent.lower() in a]  # RFC 9309: the product token, case-insensitive
        star = [r for a, r in groups if "*" in a]
        self.rules = [rule for group in (mine or star) for rule in group]

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        """Prefix match with * wildcards and a final $, without regex backtracking (a hostile robots.txt full of
        stars can't stall the run): classic glob matching, O(len(pattern) * len(path)) at worst."""
        pattern = re.sub(r"\*+", "*", pattern[:512])
        anchored = pattern.endswith("$")
        if anchored:
            pattern = pattern[:-1]
        if not anchored:
            pattern += "*"
        p = t = 0
        star, mark = -1, 0
        while t < len(path):
            if p < len(pattern) and pattern[p] != "*" and pattern[p] == path[t]:
                p, t = p + 1, t + 1
            elif p < len(pattern) and pattern[p] == "*":
                star, mark, p = p, t, p + 1
            elif star >= 0:
                p, mark = star + 1, mark + 1
                t = mark
            else:
                return False
        while p < len(pattern) and pattern[p] == "*":
            p += 1
        return p == len(pattern)

    def allowed(self, path: str) -> bool:
        best_len, best_allow = -1, True
        for allow, pattern in self.rules:
            if not pattern:  # "Disallow:" with no value allows everything
                continue
            if self._matches(pattern, path):
                length = len(pattern)
                if length > best_len or (length == best_len and allow):
                    best_len, best_allow = length, allow
        return best_allow


class Http:
    def __init__(self, *, per_host: int = 3, total: int = 16, timeout: float = 30.0, transport=None) -> None:
        self.client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=False,  # followed by hand in _checked, so every hop is checked
            transport=transport,
        )
        self._total = asyncio.Semaphore(total)
        self._per_host = per_host
        self._host_sems: dict[str, asyncio.Semaphore] = {}
        self._host_next: dict[str, float] = {}
        self._robots: dict[str, Robots] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._strikes: dict[str, int] = {}
        self._public: dict[str, bool] = {}
        self.requests = 0

    async def __aenter__(self) -> "Http":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.client.aclose()

    def _sem(self, bucket: str) -> asyncio.Semaphore:
        if bucket not in self._host_sems:
            self._host_sems[bucket] = asyncio.Semaphore(BUCKET_LIMITS.get(bucket, (self._per_host, 0))[0])
        return self._host_sems[bucket]

    async def _space(self, bucket: str) -> None:
        gap = BUCKET_LIMITS.get(bucket, (0, 0))[1]
        if not gap:
            return
        loop = asyncio.get_running_loop()
        slot = max(loop.time(), self._host_next.get(bucket, 0.0))
        self._host_next[bucket] = slot + gap
        if slot > loop.time():
            await asyncio.sleep(slot - loop.time())

    def circuit_open(self, url: str) -> bool:
        return self._strikes.get(bucket_of(urlsplit(url).netloc), 0) >= 2

    def _strike(self, url: str) -> None:
        bucket = bucket_of(urlsplit(url).netloc)
        self._strikes[bucket] = self._strikes.get(bucket, 0) + 1

    async def _send(self, method: str, url: str, *, retries: int = 4, **kwargs) -> httpx.Response:
        reason = nofetch_reason(url)
        if reason:
            raise Blocked(f"{urlsplit(url).netloc} is on the no-fetch list ({reason})")
        if self.circuit_open(url):
            bucket = bucket_of(urlsplit(url).netloc)
            who = "Workday (all tenants share one rate limit)" if bucket == "workday" else bucket
            raise Blocked(f"{who} blocked us twice this run - skipping until next run")
        bucket = bucket_of(urlsplit(url).netloc)
        backoff = 1.0
        for attempt in range(retries + 1):
            response: httpx.Response | None = None
            async with self._total, self._sem(bucket):
                await self._space(bucket)
                self.requests += 1
                try:
                    response = await asyncio.wait_for(self._fetch(method, url, **kwargs), REQUEST_DEADLINE)
                except asyncio.TimeoutError:
                    raise FetchError(f"{urlsplit(url).netloc} took over {REQUEST_DEADLINE:.0f}s") from None
                except httpx.TransportError:
                    if attempt >= retries:
                        raise
            if response is not None and (response.status_code not in RETRY_STATUS or attempt >= retries):
                if response.status_code in BLOCKING_STATUS:
                    self._strike(url)
                return response
            wait = backoff
            if response is not None and response.status_code == 429:
                retry_after = response.headers.get("retry-after", "")
                wait = min(float(retry_after), 30.0) if retry_after.isdigit() else max(backoff * 2, 4.0)
            await asyncio.sleep(wait + random.random() * 0.5)
            backoff *= 2
        raise FetchError(f"gave up on {url}")  # pragma: no cover - loop always returns or raises

    async def _fetch(self, method: str, url: str, **kwargs) -> httpx.Response:
        """One request, body read with a size cap (counted after decompression)."""
        request = self.client.build_request(method, url, **kwargs)
        response = await self.client.send(request, stream=True)
        try:
            body, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise FetchError(f"{urlsplit(url).netloc} sent more than {MAX_BYTES // 1_000_000} MB")
                body.append(chunk)
        finally:
            await response.aclose()
        headers = [(k, v) for k, v in response.headers.multi_items()
                   if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(response.status_code, headers=headers, content=b"".join(body), request=request)

    async def _public_host(self, host: str) -> bool:
        """False for hosts that are, or resolve to, private / loopback / link-local / reserved addresses."""
        if host not in self._public:
            try:
                addrs = [ipaddress.ip_address(host)]
            except ValueError:
                try:
                    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
                    addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
                except (OSError, ValueError):
                    addrs = []
            self._public[host] = bool(addrs) and all(a.is_global and not a.is_multicast for a in addrs)
        return self._public[host]

    async def _redirect_target(self, response: httpx.Response, current: str, *, robots: bool) -> str:
        """Where a redirect points, after the same checks the first URL got (and a few more)."""
        location = response.headers.get("location", "")
        target = urljoin(current, location)
        if urlsplit(target).scheme not in ("http", "https") or not host_of(target):
            raise Blocked(f"redirect to a non-web address refused: {location[:80]}")
        reason = nofetch_reason(target)
        if reason:
            raise Blocked(f"redirect to {host_of(target)} refused: on the no-fetch list ({reason})")
        if not await self._public_host(host_of(target)):
            raise Blocked(f"redirect to a private or local address refused: {host_of(target)}")
        if (robots or host_of(target) != host_of(current)) and not await self.robots_allowed(target):
            raise Blocked(f"robots.txt disallows {target}")
        return target

    async def robots_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.netloc
        lock = self._robots_locks.setdefault(host, asyncio.Lock())
        async with lock:
            if host not in self._robots:
                try:
                    robots_url = f"{parts.scheme}://{host}/robots.txt"
                    r = await self._send("GET", robots_url, retries=1, headers={"Accept": "text/plain"})
                    for _ in range(MAX_REDIRECTS):
                        target = urljoin(robots_url, r.headers.get("location", "")) if r.is_redirect else ""
                        if not target:
                            break
                        if (urlsplit(target).scheme not in ("http", "https") or nofetch_reason(target)
                                or not await self._public_host(host_of(target))):
                            r = httpx.Response(503)  # redirected somewhere we won't go: treat the site as unreachable
                            break
                        robots_url = target
                        r = await self._send("GET", robots_url, retries=1, headers={"Accept": "text/plain"})
                    if r.status_code >= 500:
                        self._robots[host] = Robots("User-agent: *\nDisallow: /")  # RFC 9309: unreachable = disallow
                    elif r.status_code >= 400:
                        self._robots[host] = Robots("")  # no robots.txt = allow
                    else:
                        self._robots[host] = Robots(r.text)
                except (httpx.HTTPError, FetchError):
                    self._robots[host] = Robots("User-agent: *\nDisallow: /")
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        return self._robots[host].allowed(path)

    async def _checked(self, method: str, url: str, *, robots: bool, **kwargs) -> httpx.Response:
        if robots and not await self.robots_allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        r = await self._send(method, url, **kwargs)
        hops = 0
        while r.is_redirect and r.headers.get("location"):
            if method != "GET" or hops >= MAX_REDIRECTS:
                raise FetchError(f"HTTP {r.status_code} redirect from {urlsplit(url).netloc} not followed")
            url = await self._redirect_target(r, url, robots=robots)
            kwargs.pop("params", None)  # the Location already carries its query
            r = await self._send("GET", url, **kwargs)
            hops += 1
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code} from {urlsplit(url).netloc}{urlsplit(url).path}")
        return r

    async def _json(self, method: str, url: str, *, robots: bool, **kwargs):
        r = await self._checked(method, url, robots=robots, **kwargs)
        try:
            return r.json()
        except ValueError as exc:
            raise FetchError(f"non-JSON response from {url}") from exc

    async def get_json(self, url: str, *, params: dict | None = None, robots: bool = False):
        return await self._json("GET", url, robots=robots, params=params)

    async def post_json(self, url: str, payload: dict, *, robots: bool = False):
        return await self._json("POST", url, robots=robots, json=payload)

    async def get_html(self, url: str, *, params: dict | None = None) -> str:
        """Fetch a web page for scraping. robots.txt is always enforced here; bot challenges stop us, never bypassed."""
        r = await self._checked("GET", url, robots=True, params=params,
                                headers={"Accept": "text/html,application/xhtml+xml"})
        text = r.text
        if len(text) < 20000 and _CHALLENGE_RX.search(text[:5000]):
            self._strike(url)
            raise Blocked(f"{urlsplit(url).netloc} answered with a bot challenge - not bypassing it")
        return text
