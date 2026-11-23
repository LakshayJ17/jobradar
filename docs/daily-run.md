# Daily run on GitHub Actions

jobradar can run every morning for free on GitHub Actions and send you the results.

The daily workflow commits your tracker back to the repository it runs in, and the tracker shows what you applied to.
So it **only runs in a private repository**; in a public one, including this project's own, it skips itself. Keep
that copy private for good: making it public later would publish its git history, including your profile and every
day's tracker.

## Setup

1. **Make a private copy.** With the [GitHub CLI](https://cli.github.com), from your clone:

   ```bash
   git remote rename origin upstream     # the public project, for updates later
   gh repo create my-jobradar --private --source . --remote origin --push
   ```

   Without the CLI, create an empty private repository on github.com, then run `git remote add origin <its url>` and
   `git push -u origin main`.

2. **Commit your profile to it.** The file is git-ignored, so add it with `-f`. Only do this in the private copy:

   ```bash
   git add -f config/profile.yaml && git commit -m "my profile" && git push
   ```

3. **Add secrets** under **Settings > Secrets and variables > Actions**, or with the CLI:

   ```bash
   gh secret set GEMINI_API_KEY                                   # paste the key when asked
   uv run jobradar resume | gh secret set JOBRADAR_RESUME_TEXT    # your resume, personal details already removed
   ```

   Email and Telegram secrets are optional; see below.

4. **Done.** `.github/workflows/daily.yml` runs every day at 02:30 UTC (change the `cron` line for another time), and
   you can start it from the Actions tab. Read the report at `data/reports/latest.md` on GitHub, or download the HTML
   report from the run's artifacts.

Run `git pull` before `jobradar mark ...` on your laptop so status changes merge cleanly, and `git pull upstream main`
to get project updates.

The workflow pins every action to an exact commit, keeps no push token on disk while jobs are fetched, and
authenticates only for the final commit of the tracker.

## Email

1. Turn on 2-Step Verification for your Google account, then create an
   [App password](https://myaccount.google.com/apppasswords). Your normal password won't work.
2. Add secrets `SMTP_USER` (your Gmail address), `SMTP_PASSWORD` (the App password) and `EMAIL_TO` (where to send).
   For another provider, also add `SMTP_HOST` and `SMTP_PORT`.

The digest attaches `jobs.xlsx` with every tracked job, unless `notify.attach_spreadsheet` is `false`.

## Telegram

Message [@BotFather](https://t.me/BotFather) and send `/newbot` to get a token. Send your new bot any message, open
`https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`. Add secrets `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID`. The bot also sends the spreadsheet as a file.
