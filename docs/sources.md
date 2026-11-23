# Sources

All sources are listed in `config/sources.yaml`.

| Kind | Platforms |
|---|---|
| Company job boards (public APIs) | Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Rippling |
| Company career sites | Workday (NVIDIA, Adobe, Salesforce, Intel, PayPal, …), Oracle Recruiting (JPMorgan, Oracle), Eightfold (Microsoft, Qualcomm), Amazon, Atlassian, Freshteam career pages |
| Job platforms | Unstop, Internshala, Shine, Wellfound, Google Careers, and the remote boards Himalayas, RemoteOK and Jobicy |

Internshala, Shine, Wellfound, Google Careers and Freshteam career pages have no usable API, so their pages are read
directly. The generic scraper
works on any site whose job pages include schema.org `JobPosting` data, which most do because Google for Jobs needs
it; for pages without it, a site entry can name where the title, company, location and description sit
(`page_fallback`).

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
