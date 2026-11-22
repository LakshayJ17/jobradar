# jobradar

Every morning, jobradar checks 17 job platforms and the career sites of 110+ companies. It keeps only the jobs
that match your profile (your roles, experience, cities, country and pay floor), ranks them against your resume,
and sends you a short list. It runs on your laptop or for free on GitHub Actions, and costs nothing.

Every filter is a setting in one file. The template is set up for freshers looking for software, AI and data roles
in India, which is where its job platforms are strongest. [Other setups](#other-setups) shows how to switch it to an
experienced engineer, another country, another currency or another field.

```
jobradar · 2026-11-16
  boards     105 checked, 0 failed · 837 requests
  postings   17449 listed → 236 unseen candidates → 52 new matches
  dropped    location 11907, title-excluded 4294, title-not-target 677, too-old 329, needs-2y-exp 46, ...
  AI         52/52 scored with gemini-3.5-flash-lite (10 marked ineligible, 14 requests)

   ID  SCORE  AI     COMPANY       ROLE                                     LOCATION            POSTED
    7     90  apply  Micron        Engineer, Data Science                   Hyderabad           20d ago
   14     88  apply  athenahealth  Member of Technical Staff- Front End     Bangalore           today
    5     85  apply  PayPal        Software Engineer                        Bangalore           3d ago
```

## How it works

1. **Fetch.** Three kinds of sources, all listed in `config/sources.yaml`:

   | Kind | Platforms |
   |---|---|
   | Company job boards (public APIs) | Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Rippling, Freshteam (scraped) |
   | Big-company career sites | Workday (NVIDIA, Adobe, Salesforce, Intel, PayPal, …), Oracle Recruiting (JPMorgan, Oracle), Eightfold (Microsoft, Qualcomm), Amazon, Atlassian |
   | Job platforms | Unstop, Internshala, Shine, Wellfound, Google Careers, plus the remote boards Himalayas, RemoteOK and Jobicy |

   Internshala, Shine, Wellfound and Google Careers are **scraped**: jobradar reads the pages directly
   because those sites have no API. The generic scraper works on any site whose job pages include
   schema.org `JobPosting` data, which most do because Google for Jobs needs it. To add one, put a listing
   URL under `scrape:` in `config/sources.yaml`.
2. **Filter with rules (no AI).** A job is kept only if all of these hold. Each one is a setting (template
   values in brackets):
   - it is in your accepted locations, or remote and open to you (India);
   - the title matches your role patterns and none of your exclusions (software, AI, data; nothing senior);
   - it asks for no more and no fewer years of experience than you set (at most 1);
   - the board doesn't label it at a level you excluded (senior, director, executive);
   - it is not an internship, unless you want them;
   - it was posted recently (30 days);
   - it isn't restricted to another graduation batch, e.g. "2025 batch only";
   - its listed pay isn't below your floor, in your currency (off);
   - it isn't a known job scam, such as a "registration fee" or "refundable deposit".
3. **Score with AI (optional, free).** Gemini reads each surviving job next to your resume. It returns
   eligibility, a 0–100 fit score, a one-line reason, and your skill gaps. It sends 6 jobs per request,
   so a normal day uses about 15 of the free tier's requests.
4. **Track and notify.** Everything lands in `data/jobs.json`, `data/jobs.csv` and an interactive
   `data/reports/latest.html`. You also get a Telegram and/or email digest of the best new matches.
5. **Draft materials.** `jobradar draft <id>` writes a kit for one job: why you fit, resume tweaks,
   a LinkedIn referral note, a cold email, interview topics, and gaps to cover.

Without a Gemini key, steps 1, 2 and 4 still run and jobs are ranked by rule score.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (it installs Python for you).

```bash
git clone https://github.com/<owner>/jobradar && cd jobradar
uv sync
uv run jobradar init       # creates config/profile.yaml and .env, both git-ignored
```

Then:

1. **Edit `config/profile.yaml`.** At least set `graduation_year`, `experience_years` and `skills`. Also look at
   `locations` and `salary`. If you aren't a fresher in India, read [Other setups](#other-setups) first.
2. **Copy your resume into `profile/`.** Any file name works, as `.pdf`, `.txt` or `.md`. It never leaves your
   machine except as redacted text for the AI.
3. **Optional: add a free Gemini key** to `.env` as `GEMINI_API_KEY`. Get one at
   https://aistudio.google.com/apikey.

```bash
uv run jobradar doctor     # checks profile, resume, keys and models
uv run jobradar resume     # shows your resume exactly as the AI sees it (personal details removed)
uv run jobradar run        # takes about 4 minutes
uv run jobradar open       # opens the HTML report
```

## Your profile

`config/profile.yaml` holds everything about what you want. It starts as a copy of
`config/profile.example.yaml`, and any setting you delete falls back to the default.

| Setting | What it does | Template default |
|---|---|---|
| `experience_years` | Your own full-time experience. Shapes the AI's eligibility check and the rule score | `0` (fresher) |
| `max_years_required`, `min_years_required` | Drop jobs asking for more / fewer years than this | `1`, `0` (off) |
| `exclude_levels` | Drop jobs a board labels at these levels: `entry`, `senior`, `director`, `executive` | `[senior, director, executive]` |
| `graduation_year` | Drops posts restricted to other batches | `2026` |
| `skills` | Each one found in a job raises its rule score (+2, max +20). **Drafts treat it as true**, so list only what you can defend in an interview | `[]` |
| `looking_for` | The roles you want, in plain words, for the AI | software engineering, AI / ML or data roles |
| `roles`, `exclude_titles` | Regexes a title must match / must not match | software, AI, data, early-career; nothing senior, non-engineering or hardware |
| `include_internships` | Show internships too | `false` |
| `max_age_days` | Ignore older postings | `30` |
| `locations.country` | Country that career sites with a country filter are searched in (Workday, Oracle, Microsoft, Amazon, Google, Wellfound, Jobicy) | `India` |
| `locations.accept` | Places that count as yours | 30 Indian cities |
| `locations.remote_open_to` | Words that make a remote job open to you | india, apac, asia, anywhere, worldwide, global |
| `locations.preferred`, `locations.groups` | Ranked higher (nothing is hidden); your own city groups | `[]`, `{}` |
| `salary.currency`, `salary.min` | Currency pay is shown in, and the floor in it (LPA for INR, thousands a year otherwise) | `INR`, `0` (off) |
| `ai.models` | Gemini free-tier models, tried in order | `gemini-3.5-flash-lite`, then two fallbacks |
| `notify` | Digest size and minimum score | 15 jobs, score 55+ |

`jobradar doctor` warns about settings that contradict each other, such as a country that none of your accepted
places mention.

Changes apply to tracked jobs on the next run: jobs that no longer fit are skipped, and ones that fit again come
back. Jobs you've marked yourself are never touched.

### Location and pay

```yaml
locations:
  preferred: [delhi ncr]       # ranked higher; nothing is hidden. Groups: delhi ncr, bengaluru, mumbai, pune...
  preferred_boost: 10          # points added for ordering only; the score shown stays the same
  remote_is_preferred: true    # remote jobs open to you count as preferred
salary:
  currency: INR
  min: 6                       # hide jobs whose listed pay tops out below ₹6 LPA
  hide_unknown: false          # keep jobs that don't list pay
```

- **City groups:** a group name covers every name the boards use for that area. `delhi ncr` covers Delhi,
  Gurugram/Gurgaon, Noida, Greater Noida, Faridabad and Ghaziabad. Add your own under `locations.groups`.
  Preferred jobs show a `*` in `jobradar list`.
- **Where pay comes from:** each platform's own pay field where it has one (Unstop, Wellfound, Lever, remote
  boards), the "CTC (ANNUAL)" line on Internshala, and Shine's listed salary. When the employer hid the salary,
  it stays hidden. Otherwise jobradar reads phrases like "CTC: 6-8 LPA", "₹25,000/month" or
  "86,250 USD – 169,000 USD" in the description. Pay in another currency is converted at a rough fixed rate, so
  every job can be compared with your floor.
- **How the floor works:** a range counts if its top end reaches the floor, so "4-7 LPA" stays.
- **Coverage:** in India only about 1 in 4 jobs lists pay. Microsoft, Amazon, Workday companies and most Greenhouse
  boards never list it there, which is why jobs with no pay listed are kept by default. US postings usually do
  (NVIDIA and Amazon state a range on most of them).

### Other setups

Change only what differs from the template.

**Experienced engineer** (here, 4 years in, looking for mid-level roles):

```yaml
experience_years: 4
max_years_required: 6
min_years_required: 2             # hides "0-1 years" fresher roles
exclude_levels: [director, executive]
```

Also delete the first `exclude_titles` line (`senior|...|architect`) so "Senior Engineer" titles stay, and
consider removing Internshala, Shine and Unstop from `sources.yaml`, which are mostly fresher hiring. The early-career
bonus in the rule score turns off automatically above 1 year of experience.

**Another country** (here, the US):

```yaml
locations:
  country: United States          # Workday, Oracle, Microsoft, Amazon, Google, Wellfound search here
  accept: [united states, usa, new york, seattle, austin, san francisco]
  remote_open_to: [united states, usa, anywhere]
  groups: {bay area: [san francisco, palo alto, mountain view, san jose, santa clara]}
  preferred: [bay area]
salary:
  currency: USD
  min: 120                        # $120k a year
```

Remove the India-only platforms (Internshala, Shine, Unstop) from `sources.yaml`, and add companies that hire where
you are (see [Adding companies](#adding-companies)). About 40 countries are known by name and code; for others,
career sites with a country filter search everywhere, and your `accept` list decides.

**Another field:** replace `roles` with patterns for your titles, set `looking_for` in plain words (for example
`"product design roles"`), set `early_career_title_needs` to words from your field, and trim the engineering-specific
lines from `exclude_titles`.

**Company reviews:** each job in the report and in `jobradar show` has a *reviews* link. It opens a web search
for the company on AmbitionBox and Glassdoor in your browser. Neither site has a public API, and both forbid
automated access, so jobradar never fetches ratings itself.

## What stays private

There is no database and no server. Everything is a local file, and git ignores all of your own:

| Path | What | In git |
|---|---|---|
| `config/profile.yaml` | Your settings | Ignored |
| `profile/` | Your resume | Ignored (except its README) |
| `.env` | API keys and tokens | Ignored |
| `data/` | Tracker, reports, drafts | Ignored. Only the daily run in **your private repo** commits the tracker |
| `config/profile.example.yaml`, `config/sources.yaml` | Template and source list | Committed |

- **The tracker:** `data/jobs.json` holds every match you're tracking. Each job has a stable id
  (`source:board:job id`), your status and notes, and the AI score. It is written atomically, so a crash can't
  corrupt it.
- **Postings that don't match aren't stored.** Each run downloads the boards again and filters them in memory.
  Only postings rejected after their full page was read (too much experience, wrong batch) are remembered,
  as id + reason, so that page isn't downloaded again.
- **It stays small.** Closed jobs you never acted on are deleted after 45 days, and descriptions are capped at
  4,000 characters. Jobs you applied to are never deleted.
- **Your resume is never stored**, and the AI never sees your personal details. The free Gemini tier may use
  prompts to improve Google's products, and human reviewers may read them, so its terms say not to send personal
  information. Before any AI call, jobradar removes your name, email, phone, links, address, PIN code, date of
  birth, family details, Aadhaar number and declaration (`src/jobradar/privacy.py`). It keeps education, skills
  and projects. Drafts come back with placeholders like `[EMAIL]`, which are filled in on your machine.

## Daily commands

| Command | What it does |
|---|---|
| `jobradar init` | Create your `config/profile.yaml` and `.env` from the templates (never overwrites) |
| `jobradar run` | Full daily run. Flags: `--no-ai`, `--no-notify`, `--only nvidia greenhouse`, `--recheck` (re-read postings rejected earlier), `--rescore` (AI scores again, e.g. after a resume change) |
| `jobradar list` | Open matches, best first. Filters: `--new`, `--min 70`, `--company amazon`, `--status applied`, `--all` |
| `jobradar show 14` | Full details, pay, reviews link, AI reasoning and description for one job |
| `jobradar draft 14` | Application kit, saved to `data/drafts/` |
| `jobradar mark 14 applied` | Update status: `saved`, `applied`, `oa`, `interview`, `offer`, `rejected`, `skipped` (works on several ids at once) |
| `jobradar probe razorpay phonepe` | Find which job board a company uses, and print the line to add to `sources.yaml` |
| `jobradar doctor` | Check profile, resume, keys and Gemini models |
| `jobradar resume` | Print your resume exactly as Gemini receives it, with personal details removed |
| `jobradar open` | Open the HTML report |

## Run it every morning (GitHub Actions, free)

The daily workflow commits your tracker back to the repo it runs in. That shows what you applied to, so it
**only runs in a private repo**. In a public repo, including this project's own, it skips itself.

1. **Make a private copy.** With the [GitHub CLI](https://cli.github.com), from your clone:

   ```bash
   git remote rename origin upstream     # the public project, for updates later
   gh repo create my-jobradar --private --source . --remote origin --push
   ```

   Without the CLI, create an empty private repo on github.com, then run `git remote add origin <its url>` and
   `git push -u origin main`.
2. **Commit your profile to it.** The file is git-ignored, so add it with `-f`. Only do this in the private repo:

   ```bash
   git add -f config/profile.yaml && git commit -m "my profile" && git push
   ```
3. **Add secrets** under **Settings → Secrets and variables → Actions**, or with the CLI:

   ```bash
   gh secret set GEMINI_API_KEY                                   # paste the key when asked
   uv run jobradar resume | gh secret set JOBRADAR_RESUME_TEXT    # your resume, personal details already removed
   ```

   Telegram and email secrets are optional; see below.
4. **That's it.** `.github/workflows/daily.yml` runs at 08:00 IST. You can also start it from the Actions tab.
   Read the report at `data/reports/latest.md` on GitHub, or download the HTML from the run's artifacts.
   Run `git pull` before `jobradar mark …` so your status changes merge cleanly, and `git pull upstream main`
   to get project updates.

**Telegram digest:** message [@BotFather](https://t.me/BotFather) and send `/newbot` to get a token. Then send
your bot any message, open `https://api.telegram.org/bot<TOKEN>/getUpdates`, and copy `chat.id`. Set
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

**Email digest:** create a Google App password (Google Account → Security → App passwords). Set
`SMTP_USER` (your Gmail address), `SMTP_PASSWORD` (the app password) and `EMAIL_TO`.

## Adding companies

Run `uv run jobradar probe <slug>`. The slug is usually the company name in lowercase.
If that finds nothing, open any job on the company's careers site: the URL shows the board.

| Job URL looks like | Add to `sources.yaml` under |
|---|---|
| `boards.greenhouse.io/<slug>` or `?gh_jid=` | `greenhouse` |
| `jobs.lever.co/<slug>` | `lever` |
| `jobs.ashbyhq.com/<slug>` | `ashby` |
| `jobs.smartrecruiters.com/<Company>` | `smartrecruiters` |
| `<tenant>.wd5.myworkdayjobs.com/<site>` | `workday` (tenant, wd, site) |

Companies on in-house systems (Flipkart, TCS/Infosys hiring drives) aren't covered. Keep alerts on for
those. If the careers page has job pages with `JobPosting` data, add it under `scrape:`.

## Ground rules it follows

- **Scrape only where it is allowed.** robots.txt is checked before every scraped page and career-site request.
  Google's robots.txt forbids paging through results, so jobradar reads only the first page of several searches.
- **A built-in no-fetch list for sites whose terms forbid automated access.** This was checked on 2026-11-17,
  and anything on the list is never fetched, whatever the config says.
  - **Terms forbid scraping:** LinkedIn, Indeed, Glassdoor, AmbitionBox, Instahyre, Cutshort, Hirist,
    Freshersworld, YC Work at a Startup, Cisco.
  - **Unusable for other reasons:** Naukri shows bot-walled pages to non-browsers. Uber's and Goldman Sachs's
    robots.txt disallow their job endpoints.
  - The list lives in `NOFETCH_DOMAINS` in `src/jobradar/http.py`.
- **No getting around blocks.** jobradar sends an honest User-Agent and uses no proxies, browser
  impersonation or CAPTCHA solving. Two blocking responses (403, 429, 503 or a bot challenge) from a site
  stop jobradar from contacting it again until the next run.
- **Polite traffic.** It limits requests per site and spaces them out, scraped sites included. It reads at most
  60 new job pages per scraped site per run, and anything left over waits for the next run. All Workday sites
  share one request budget, because Workday rate-limits by IP address.
- **It never applies for you.** It finds and ranks postings; you read them and apply yourself.
- **Job text is untrusted.** Job descriptions go to the model inside fences, and any instructions they
  contain are ignored. The model's output is only stored and displayed. The HTML report builds its table
  without `innerHTML`, and the CSV escapes cells so a spreadsheet can't run them as formulas.
- **Free tier only.** Model names and limits live in `config/profile.yaml`. When one model's free daily quota
  runs out, it moves to the next one, and anything left unscored waits for the next run.

## Development

```bash
uv sync --group dev
uv run pytest -q        # 135 tests, all offline
```

Contributions are welcome, especially new companies for `config/sources.yaml` and new job boards. A new board is
a module in `src/jobradar/sources/` plus a test fed with a trimmed real response. Sources on the no-fetch list
stay off it, robots.txt checks stay on, and nothing may get around a block.

jobradar isn't affiliated with any of the job sites it reads. Postings belong to the companies that publish them.

## License

[MIT](LICENSE)
