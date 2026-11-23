# Sources

All sources are listed in `config/sources.yaml`.

| Kind | Platforms |
|---|---|
| Company job boards (public APIs) | Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Rippling |
| Company career sites | Workday (NVIDIA, Adobe, Salesforce, Intel, PayPal and more), Oracle Recruiting (JPMorgan, Oracle), Eightfold (Microsoft, Qualcomm), Amazon, Atlassian, Freshteam career pages |
| Job platforms | Unstop, Internshala, Shine, Wellfound, Google Careers, and the remote boards Himalayas, RemoteOK and Jobicy |
| Internships (when `internships` is on) | Internshala and Unstop internships, Google, Amazon and Microsoft intern searches, and every company board above |

Internshala, Shine, Wellfound, Google Careers and Freshteam career pages have no usable API, so their pages are read
directly. The generic scraper works on any site whose job pages include schema.org `JobPosting` data, which most do
because Google for Jobs needs it; for pages without it, a site entry can name where the title, company, location and
description sit (`page_fallback`).

## Checking jobs are still open

A link that loads isn't proof a job is open: pages stay up after applications close, and boards take jobs down
without notice. jobradar closes a tracked job when:

- its company board, read in full every day (Greenhouse, Lever, Ashby, Workable, Rippling, Atlassian), stops listing it;
- the board says applications are closed (Unstop registrations, Workday's `canApply`) or its closing date passes;
- a re-check finds the page gone (404 / 410), redirected away from the job, saying applications are closed or the job
  was filled or taken down, or past the deadline in its `JobPosting` data. Oracle and Eightfold career sites are asked
  through their own APIs;
- no board has listed it for 30 days.

Each run re-checks up to 40 tracked jobs you haven't acted on (`verify.per_run`), each at most every 3 days
(`verify.every_days`), best-ranked first. `jobradar check <id>` checks a job on demand, `jobradar check --all` checks
every open one, and `jobradar draft` checks before making a kit. Closed jobs are hidden from lists, reports and
digests; `jobradar show <id>` says why and since when. A check that can't tell (a block, an app-only page) changes
nothing.

## Adding companies

Run `uv run jobradar probe <slug>`; the slug is usually the company name in lowercase. If that finds nothing, open
any job on the company's careers site: the URL shows the board.

| Job URL looks like | Add to `sources.yaml` under |
|---|---|
| `boards.greenhouse.io/<slug>` or `?gh_jid=` | `greenhouse` |
| `jobs.lever.co/<slug>` | `lever` |
| `jobs.ashbyhq.com/<slug>` | `ashby` |
| `jobs.smartrecruiters.com/<Company>` | `smartrecruiters` |
| `<tenant>.wd5.myworkdayjobs.com/<site>` | `workday` (tenant, wd, site) |

Companies that hire through in-house systems aren't covered. If their careers pages carry `JobPosting` data, add a
listing URL under `scrape:`.

## Scraping rules

- **Only where allowed.** robots.txt is checked before every scraped page and career-site request, including
  redirects. Where robots.txt forbids paging (Google Careers), only first pages are read.
- **A no-fetch list** of sites whose terms forbid automated access is never fetched, whatever the configuration
  says: LinkedIn, Indeed, Glassdoor, AmbitionBox, Naukri, Instahyre, Cutshort, Hirist, Freshersworld, YC Work at a
  Startup and a few others.
- **No getting around blocks.** An honest User-Agent, no proxies, no browser impersonation, no CAPTCHA solving. Two
  blocking responses from a site stop requests to it until the next run.
- **Polite traffic.** Requests are limited and spaced per site, at most a set number of new job pages are read per
  site per run, and all Workday sites share one request budget.
- **Defensive by default.** Redirects into private networks are refused, responses are size-capped, every request
  has a deadline, and one broken board or posting can't stop a run.
- **It never applies for you.** jobradar finds and ranks postings; you read them and apply yourself.
