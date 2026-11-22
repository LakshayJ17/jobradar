"""Polite async HTTP for APIs and HTML scraping.

* robots.txt (RFC 9309, longest-match) is checked before every scrape / career-site request
* NOFETCH_DOMAINS: sites whose terms of service forbid automated access - never fetched, whatever the config says
* rate-limit buckets: parallelism + minimum spacing per site (Workday tenants share one budget)
* circuit breaker: two blocking answers (403/429/503 or a bot challenge) and the site is skipped for the run
* no proxies, no browser impersonation, no CAPTCHA solving - an honest User-Agent, always
"""

from __future__ import annotations

import asyncio
import random
import re
from urllib.parse import urlsplit

import httpx

USER_AGENT = "jobradar/0.1 (personal job-search tool; low volume)"
AGENT_TOKEN = "jobradar"
RETRY_STATUS = {429, 500, 502, 503, 504}
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
_CHALLENGE_RX = re.compile(r"cf-chl|challenge-platform|just a moment\.\.\.|captcha|access denied|are you a robot", re.I)


class FetchError(Exception):
    pass


class Blocked(FetchError):
    """robots.txt, the no-fetch list or the circuit breaker forbids this request - we never retry around it."""


def bucket_of(host: str) -> str:
    return next((name for suffix, name in BUCKET_SUFFIXES.items() if host.endswith(suffix)), host)


def nofetch_reason(url: str) -> str | None:
    host = urlsplit(url).netloc.lower().split(":")[0]
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
        mine = [r for a, r in groups if any(x != "*" and x and x in agent.lower() for x in a)]
        star = [r for a, r in groups if "*" in a]
        self.rules = [rule for group in (mine or star) for rule in group]

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        rx = "^" + re.escape(pattern).replace(r"\*", ".*")
        if rx.endswith(r"\$"):
            rx = rx[:-2] + "$"
        return re.match(rx, path) is not None

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
            follow_redirects=True,
            transport=transport,
        )
        self._total = asyncio.Semaphore(total)
        self._per_host = per_host
        self._host_sems: dict[str, asyncio.Semaphore] = {}
        self._host_next: dict[str, float] = {}
        self._robots: dict[str, Robots] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._strikes: dict[str, int] = {}
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
                    response = await self.client.request(method, url, **kwargs)
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

    async def robots_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.netloc
        lock = self._robots_locks.setdefault(host, asyncio.Lock())
        async with lock:
            if host not in self._robots:
                try:
                    r = await self._send("GET", f"{parts.scheme}://{host}/robots.txt", retries=1,
                                         headers={"Accept": "text/plain"})
                    if r.status_code >= 500:
                        self._robots[host] = Robots("User-agent: *\nDisallow: /")  # RFC 9309: unreachable = disallow
                    elif r.status_code >= 400:
                        self._robots[host] = Robots("")  # no robots.txt = allow
                    else:
                        self._robots[host] = Robots(r.text)
                except httpx.HTTPError:
                    self._robots[host] = Robots("User-agent: *\nDisallow: /")
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        return self._robots[host].allowed(path)

    async def _checked(self, method: str, url: str, *, robots: bool, **kwargs) -> httpx.Response:
        if robots and not await self.robots_allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        r = await self._send(method, url, **kwargs)
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
