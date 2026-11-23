# jobradar

A free, self-hosted job search assistant. Every day it checks job platforms and the career sites of 110+ companies,
keeps the jobs that fit your profile, ranks them against your resume, and helps you apply: a resume tailored to the
job, short outreach messages, and interview prep.

It runs on your laptop or on GitHub Actions, needs no server or paid API, and keeps your data in local files.

```
jobradar 2026-11-16
  boards     105 checked, 0 failed, 837 requests
  postings   17449 listed -> 236 unseen candidates -> 52 new matches
  dropped    location 11907, title-excluded 4294, title-not-target 677, too-old 329, needs-2y-exp 46, ...
  AI         52/52 scored with gemini-3.5-flash-lite (10 marked ineligible, 14 requests)

   ID  SCORE  AI     COMPANY       ROLE                                     LOCATION            POSTED
    7     90  apply  Micron        Engineer, Data Science                   Hyderabad           20d ago
   14     88  apply  athenahealth  Member of Technical Staff- Front End     Bangalore           today
    5     85  apply  PayPal        Software Engineer                        Bangalore           3d ago
```

## Features

- **Finds jobs** on Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Rippling, Workday, Oracle, Eightfold,
  Amazon, Atlassian, Google Careers, Unstop, Internshala, Shine, Wellfound and remote boards.
- **Filters by your rules**: role, experience, level, location, remote, pay floor, posting age, graduation batch,
  and common job scams such as registration fees. Every rule is a setting.
- **Jobs, internships or both**: one setting. Internships come from Internshala, Unstop and company boards, with
  stipends shown a month and their own pay floor.
- **Ranks with AI** (optional, Gemini free tier): eligibility, a fit score, a reason and your skill gaps for each job.
- **Checks jobs are still open.** Postings that were taken down, stopped taking applications or passed their deadline
  are detected and hidden, even when the link still loads.
- **Tracks everything** in a local tracker, an interactive HTML report and a spreadsheet, with a daily digest by email
  or Telegram.
- **Helps you apply** with `jobradar draft <id>`: your resume tailored to the job (PDF and Word) without inventing
  anything, referral and recruiter messages that don't read as AI-written, and interview prep with free resources.
- **Works for any setup.** The template targets freshers in India; one file switches it to experienced roles,
  another country, another currency or another field.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs Python for you.

```bash
git clone https://github.com/LakshayJ17/jobradar && cd jobradar
uv sync
uv run jobradar init       # creates config/profile.yaml and .env, both git-ignored
```

1. Edit `config/profile.yaml`: at least `graduation_year`, `experience_years`, `skills`, `internships`, `locations`
   and `salary` ([configuration guide](docs/configuration.md)).
2. Copy your resume into `profile/` (`.pdf`, `.txt` or `.md`, any name).
3. Optional: put a free Gemini key in `.env` as `GEMINI_API_KEY` ([get one](https://aistudio.google.com/apikey)).
   Without it, jobs are ranked by rules only.

```bash
uv run jobradar doctor     # check profile, resume and keys
uv run jobradar run        # find today's jobs (a few minutes)
uv run jobradar open       # open the report
```

## Commands

| Command | What it does |
|---|---|
| `jobradar run` | Fetch, filter, score, report and notify. Flags: `--no-ai`, `--no-notify`, `--only <source or company>` |
| `jobradar list` | Open matches, best first (`--new`, `--min 70`, `--company amazon`, `--status applied`, `--internships`) |
| `jobradar show <id>` | Details, pay, AI reasoning and description for one job |
| `jobradar check [<id> ...]` | Check jobs are still open: taken down, applications closed, deadline passed |
| `jobradar draft <id>` | Application kit: tailored resume, messages and interview prep (checks the job is open first) |
| `jobradar mark <id> applied` | Track progress: `saved`, `applied`, `oa`, `interview`, `offer`, `rejected`, `skipped` |
| `jobradar mail` / `export` | Email every tracked job as a spreadsheet, or just save it |
| `jobradar resume` | Show your resume as the AI sees it; `--setup` prepares it for tailoring |
| `jobradar probe <slug>` | Find which job board a company uses |
| `jobradar doctor` | Check configuration, resume, keys and models |

## How it works

1. **Fetch** listings from the sources in `config/sources.yaml`, through public APIs or by reading public job pages.
2. **Filter** them with the rules in your profile. No AI is needed for this.
3. **Score** what's left against your resume with the free Gemini tier, about 15 requests on a normal day.
4. **Track and notify**: new matches go to the tracker, the report, the spreadsheet and your digest. Tracked jobs
   are re-checked every few days and closed ones are hidden. Jobs you haven't acted on show as `new` while fresh, then
   `to review`.
5. **Apply** with an application kit for the jobs you pick ([details](docs/application-kit.md)).

## Privacy

- Everything is a local file. Your profile, resume, keys and tracker are git-ignored.
- Before any AI call, your name, contact details, links, address, ID numbers and other personal details are removed
  from your resume. Run `jobradar resume` to see exactly what is sent.
- Tailored resumes and messages get your contact details filled in on your machine, after the AI has replied.

## Responsible scraping

jobradar respects robots.txt, never fetches sites whose terms forbid automated access (LinkedIn, Indeed,
Glassdoor, Naukri and others), never gets around a block, keeps request rates low, and never applies to jobs for
you. See [Sources](docs/sources.md) for the platforms and the full rules.

## Documentation

- [Configuration](docs/configuration.md): every profile setting, location and pay, other countries, keys
- [Application kit](docs/application-kit.md): how resumes are tailored and messages checked
- [Daily run](docs/daily-run.md): free daily runs on GitHub Actions, email and Telegram digests
- [Sources](docs/sources.md): supported platforms, adding companies, scraping rules

## Contributing

Contributions are welcome, especially new companies for `config/sources.yaml` and new job boards. A new board is a
module in `src/jobradar/sources/` plus a test fed with a trimmed real response. Sites on the no-fetch list stay off
it, robots.txt checks stay on, and nothing may get around a block.

```bash
uv sync --group dev
uv run pytest -q           # offline test suite
```

jobradar isn't affiliated with any of the job sites it reads. Postings belong to the companies that publish them.

## License

[MIT](LICENSE)
