# Application kit

`jobradar draft <id>` writes `data/drafts/<id>-<company>-<role>/` for one tracked job:

| File | What's in it |
|---|---|
| `Your_Name_Resume.pdf` | Your resume tailored to this job: one page, the classic LaTeX look, real text in one column with standard headings and no ligatures, so applicant tracking systems read it correctly |
| `Your_Name_Resume.docx` / `.txt` | The same resume to edit in Word or Google Docs, and as plain text for "paste your resume" boxes |
| `kit.md` | Why you fit (the job's main requirements, each with the line on your resume that shows it), keywords you have and gaps, every change made to your resume and why, five messages, and how to use them |
| `prep.md` | Topics to study with free resources (docs, roadmaps, YouTube, practice sites; some in Hindi), 12–15 likely questions with answer notes from your own resume, what to revise, questions to ask, links to other candidates' interview experiences, and a 7-day plan |

Flags: `--no-ai` (rules and templates only), `--only resume messages prep`.

## Setting up your resume

`jobradar resume --setup` turns your resume file into `profile/resume.yaml`, once. The AI only sorts the text into
sections; every field is then checked word for word against your file, and anything that doesn't match is listed.
Your name and contact line are read on your machine and never sent. Check the result with `jobradar resume --pdf`.

Without a Gemini key, copy `config/resume.example.yaml` to `profile/resume.yaml` and fill it in.

## How the resume is tailored

The order of projects, bullets and skills changes so what the job asks for comes first. At most 3 bullets may be
reworded to use the job's own term for something you did ("API endpoints" to "REST APIs"). Code checks every
rewording, and your original stays when the rewording:

- adds a word that is neither in that bullet nor a technology on your resume (so no "led a team", "production" or
  "ten thousand users" out of nowhere),
- adds a number, or changes what a number refers to,
- rewrites most of the sentence, or makes it much longer,
- contains unusual or invisible characters,
- uses wording recruiters read as AI-written, such as "spearheaded", "leveraged", "seamless", em dashes or
  ", resulting in…".

Skills the job wants that you don't have are listed as gaps and covered in `prep.md`; they are never added. If the
page overflows, the least relevant bullets go first. `kit.md` lists every change and every refused rewording.

`jobradar resume --check` flags wording in your base resume that recruiters read as AI-written or filler, such as the
same verb starting too many bullets.

## Messages

Five messages: a LinkedIn referral request, a recruiter DM, a cold email, a note to an alumnus of your college, and
one follow-up. They follow recruiter and career-centre guidance and reply-rate data:

- LinkedIn connection notes stay under its 200-character limit. Free accounts can add a note to only a few
  invitations a month, so spend them on people at the company.
- The recruiter DM is under 400 characters and the email 60–110 words; shorter messages get more replies.
- Each message has one specific detail and one ask, and opens with "Hi [first name]," for you to fill in.
- Alumni get a request for advice first, not a referral.

Code checks every message for stock phrases ("I hope this message finds you well", "I came across your profile",
"excited to"), exclamation marks, em dashes, flattery, very long sentences, repeated wording, numbers that aren't on
your resume, and claims your resume doesn't back up. Problems go back to the AI once. A message that still claims
something your resume doesn't show is replaced by a plain template built from your resume, and anything else left is
flagged in `kit.md`.

## Interview prep

The AI picks topics only from `config/prep.yaml`, a hand-checked catalog of free learning resources, so it can't
invent a link. Questions come with answer notes that name your own projects, and never invent experience. Company
links are web searches you open yourself: jobradar never fetches Glassdoor, AmbitionBox, LeetCode or Reddit, whose
terms forbid automated access.

## Cost

With a Gemini key a kit uses about 3–4 free requests. Without one, the kit is still made: keyword-based ordering,
plain message templates and keyword-picked prep topics.
