# Configuration

Everything about what you want lives in `config/profile.yaml`. `jobradar init` creates it from
`config/profile.example.yaml`, and any setting you leave out falls back to the default. The file is git-ignored.

The template is set up for a fresher looking for software, AI and data roles in India. [Other setups](#other-setups)
shows what to change for anything else.

## Settings

| Setting | What it does | Template default |
|---|---|---|
| `experience_years` | Your own full-time experience. Shapes the AI's eligibility check and the rule score | `0` |
| `max_years_required`, `min_years_required` | Drop jobs asking for more / fewer years than this | `1`, `0` (off) |
| `exclude_levels` | Drop jobs a board labels at these levels: `entry`, `senior`, `director`, `executive` | `[senior, director, executive]` |
| `graduation_year` | Drops posts restricted to other graduation batches | `2026` |
| `skills` | Each one found in a job raises its rule score (+2, max +20). Application drafts treat this list as true, so list only what you can defend in an interview | `[]` |
| `looking_for` | The roles you want, in plain words, for the AI | software engineering, AI / ML or data roles |
| `roles`, `exclude_titles` | Regexes a job title must match / must not match | software, AI, data, early-career; nothing senior, non-engineering or hardware |
| `include_internships` | Show internships too | `false` |
| `max_age_days` | Ignore older postings | `30` |
| `locations.country` | Country that career sites with a country filter are searched in (Workday, Oracle, Microsoft, Amazon, Google, Wellfound, Jobicy) | `India` |
| `locations.accept` | Places that count as yours | 30 Indian cities |
| `locations.remote_open_to` | Words that make a remote job open to you | india, apac, asia, anywhere, worldwide, global |
| `locations.preferred`, `locations.groups` | Ranked higher (nothing is hidden); your own city groups | `[]`, `{}` |
| `salary.currency`, `salary.min` | Currency pay is shown in, and the floor in it (LPA for INR, thousands a year otherwise) | `INR`, `0` (off) |
| `ai.models`, `ai.draft_models` | Gemini free-tier models, tried in order | flash-lite models first |
| `resume.style` | Look of tailored resumes (paper, font size, headings) | A4, 10 pt, bold headings |
| `notify` | Digest size, minimum score, channels, spreadsheet attachment | 15 jobs, score 55+ |

`jobradar doctor` warns about settings that contradict each other, such as a country that none of your accepted
places mention.

Changes apply to tracked jobs on the next run: jobs that no longer fit are skipped, and ones that fit again come
back. Jobs whose status you've set yourself are never touched.

## Location and pay

```yaml
locations:
  preferred: [bengaluru]       # ranked higher; nothing is hidden
  preferred_boost: 10          # points added for ordering only; the score shown stays the same
  remote_is_preferred: true    # remote jobs open to you count as preferred
salary:
  currency: INR
  min: 8                       # hide jobs whose listed pay tops out below ₹8 LPA
  hide_unknown: false          # keep jobs that don't list pay
```

- **City groups:** a group name covers every name boards use for that area. `delhi ncr` covers Delhi,
  Gurugram/Gurgaon, Noida, Greater Noida, Faridabad and Ghaziabad. Built-in groups also exist for Bengaluru,
  Mumbai, Pune, Hyderabad, Chennai and Kolkata. Add your own under `locations.groups`. Preferred jobs show a `*`
  in `jobradar list`.
- **Where pay comes from:** each platform's own pay field where it has one (Unstop, Wellfound, Lever, remote
  boards), the salary line on Internshala and Shine pages, and otherwise phrases in the description such as
  "CTC: 6-8 LPA", "₹25,000/month" or "86,250 USD – 169,000 USD". A salary the employer hid stays unknown. Pay in
  another currency is converted at a rough fixed rate, so every job can be compared with your floor.
- **How the floor works:** a range counts if its top end reaches the floor, so "4-7 LPA" passes a floor of 6.
- **Coverage:** in India only about 1 in 4 postings lists pay, and many large companies never do, which is why jobs
  without pay are kept by default. US postings usually state a range.

## Other setups

Change only what differs from the template.

**Experienced engineer** (for example, 4 years in, looking for mid-level roles):

```yaml
experience_years: 4
max_years_required: 6
min_years_required: 2             # hides "0-1 years" fresher roles
exclude_levels: [director, executive]
```

Also delete the first `exclude_titles` line (`senior|...|architect`) so "Senior Engineer" titles stay, and consider
removing Internshala, Shine and Unstop from `sources.yaml`, which are mostly fresher hiring. The early-career bonus in
the rule score turns off automatically above 1 year of experience.

**Another country** (for example, the US):

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

Remove the India-only platforms (Internshala, Shine, Unstop) from `sources.yaml` and add companies that hire where you
are (see [Sources](sources.md#adding-companies)). About 40 countries are known by name and code; for others, career
sites with a country filter search everywhere and your `accept` list decides.

**Another field:** replace `roles` with patterns for your titles, set `looking_for` in plain words (for example
`"product design roles"`), set `early_career_title_needs` to words from your field, and trim the engineering-specific
lines from `exclude_titles`.

## Keys and tokens

`.env` (created by `jobradar init`, git-ignored, readable only by you) holds optional keys:

| Variable | For |
|---|---|
| `GEMINI_API_KEY` | AI scoring and the application kit. Free key: https://aistudio.google.com/apikey |
| `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` | Email digest and `jobradar mail`. For Gmail, use an [App password](https://myaccount.google.com/apppasswords) (needs 2-Step Verification) |
| `SMTP_HOST`, `SMTP_PORT` | Other email providers (587 uses STARTTLS; the password is never sent unencrypted) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram digest (see [Daily run](daily-run.md#telegram)) |
| `JOBRADAR_RESUME_TEXT` | Your resume as text, where no resume file is available (GitHub Actions) |

`jobradar doctor` shows which are set, without showing their values.
