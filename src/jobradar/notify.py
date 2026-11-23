"""Digest delivery: Telegram bot, email (SMTP, e.g. Gmail app password), GitHub Actions run summary."""

from __future__ import annotations

import html
import mimetypes
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import httpx

from .export import summary_line
from .report import pay_text, posted_label, why
from .store import Store
from .util import clean_line, clip, safe_url, today


def pick(store: Store, cfg: dict) -> list[dict]:
    """Unsent, visible matches at or above the score threshold - best first."""
    min_score = int(cfg.get("min_score", 55))
    items = [r for r in store.all() if store.visible(r) and not r.get("notified") and store.score_of(r) >= min_score
             and (r.get("ai") or {}).get("flag") != "steers-ai"]  # postings that try to game AI screening: report only
    return store.ranked(items)[: int(cfg.get("top_n", 15))]


def _line(rec: dict, store: Store) -> tuple[str, str]:
    ai = rec.get("ai") or {}
    tag = f" [{clean_line(ai['verdict'])}]" if ai.get("verdict") else ""
    pay = f", {pay_text(rec)}" if pay_text(rec) else ""
    title, company = clip(clean_line(rec["title"]), 140), clip(clean_line(rec["company"]), 60)
    where, reason = clip(clean_line(rec.get("location") or ""), 40), clip(clean_line(why(rec)), 160)
    url = safe_url(rec["url"])
    plain = (f"#{rec['id']} {store.score_of(rec)}{tag} {company} - {title} "
             f"({where}{pay}, {posted_label(rec)})\n{url or '(link not shown: not an http(s) address)'}\n{reason}")
    link = (f"<a href=\"{html.escape(url, quote=True)}\">{html.escape(title)}</a>" if url else html.escape(title))
    rich = (f"<b>#{rec['id']} | {store.score_of(rec)}{html.escape(tag)}</b> {html.escape(company)} - {link}\n"
            f"<i>{html.escape(where)}{html.escape(pay.replace(',', ' |', 1))} | {posted_label(rec)}</i>\n"
            f"{html.escape(reason)}")
    return plain, rich


def build_digest(items: list[dict], store: Store, total_new: int) -> tuple[str, str, str]:
    head = f"jobradar {today().isoformat()}: {len(items)} top matches ({total_new} new today)"
    lines = [_line(r, store) for r in items]
    plain = head + "\n\n" + "\n\n".join(p for p, _ in lines)
    rich = f"<b>{html.escape(head)}</b>\n\n" + "\n\n".join(r for _, r in lines)
    footer = "\n\nMark progress: jobradar mark <id> applied  |  Draft materials: jobradar draft <id>"
    return head, plain + footer, rich + html.escape(footer)


def send_telegram(rich_text: str) -> str:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return "skipped (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)"
    chunks, cur = [], ""
    for block in rich_text.split("\n\n"):  # blocks are whole jobs (fields are one line), so tags never split
        block = block[:3900]
        if cur and len(cur) + len(block) + 2 > 3900:  # Telegram caps messages at 4096 chars
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n\n{block}" if cur else block
    chunks.append(cur)
    try:
        with httpx.Client(timeout=20) as client:
            for chunk in chunks:
                r = client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                json={"chat_id": chat, "text": chunk, "parse_mode": "HTML",
                                      "disable_web_page_preview": True})
                if r.status_code != 200:
                    return f"failed (HTTP {r.status_code})"
    except httpx.HTTPError as exc:
        return f"failed ({type(exc).__name__})"
    return "sent"


def email_settings() -> dict:
    """SMTP settings from the environment (.env locally, repository secrets in GitHub Actions)."""
    env = os.environ.get
    port = (env("SMTP_PORT") or "").strip()
    port_ok = port.isdigit() and 0 < int(port) < 65536
    return {"user": env("SMTP_USER"), "password": env("SMTP_PASSWORD"), "to": env("EMAIL_TO"),
            "host": (env("SMTP_HOST") or "").strip() or "smtp.gmail.com", "port": int(port) if port_ok else 465}


_ADDRESS = re.compile(r"[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[A-Za-z]{2,}")


def send_email(subject: str, plain: str, rich: str, *, to: str | None = None,
               attachments: list[Path] | None = None) -> str:
    """Send through your own mailbox. Port 465 = SSL (Gmail default), 587 = STARTTLS (Outlook, Zoho, most others)."""
    cfg = email_settings()
    to = (to or cfg["to"] or cfg["user"] or "").strip()
    if not (cfg["user"] and cfg["password"] and to):
        return "skipped (SMTP_USER / SMTP_PASSWORD / EMAIL_TO not set)"
    if not _ADDRESS.fullmatch(to) or not _ADDRESS.fullmatch(cfg["user"].strip()):
        return "failed (EMAIL_TO / SMTP_USER must each be one plain email address)"
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg["user"], to
    msg.set_content(plain)
    msg.add_alternative("<div style='font-family:system-ui,sans-serif;white-space:pre-wrap'>"
                        + rich + "</div>", subtype="html")
    for path in attachments or []:
        maintype, subtype = (mimetypes.guess_type(path.name)[0] or "application/octet-stream").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    context = ssl.create_default_context()
    try:
        if cfg["port"] == 465:
            smtp = smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=30)
        else:
            smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
        with smtp:
            if cfg["port"] != 465:
                smtp.ehlo()
                if smtp.has_extn("starttls"):
                    smtp.starttls(context=context)
                    smtp.ehlo()
                elif cfg["host"] not in ("localhost", "127.0.0.1", "::1"):
                    # never send the password in the clear (a server, or someone in between, hid STARTTLS)
                    return "failed (the mail server offered no encryption, so the password was not sent)"
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return "failed (login refused: for Gmail, SMTP_PASSWORD must be an App password, not your normal password)"
    except (smtplib.SMTPException, OSError) as exc:
        return f"failed ({type(exc).__name__})"
    return "sent"


def send_telegram_file(path: Path, caption: str) -> str:
    """Send a file (the spreadsheet) to your Telegram chat; it opens in the Telegram app on any device."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return "skipped (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)"
    try:
        with httpx.Client(timeout=60) as client:
            r = client.post(f"https://api.telegram.org/bot{token}/sendDocument",
                            data={"chat_id": chat, "caption": caption[:1000]},
                            files={"document": (path.name, path.read_bytes(),
                                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    except httpx.HTTPError as exc:
        return f"failed ({type(exc).__name__})"
    return "sent" if r.status_code == 200 else f"failed (HTTP {r.status_code})"


def mail_spreadsheet(store: Store, path: Path, to: str | None = None) -> str:
    """`jobradar mail`: email every tracked job as a spreadsheet, plus today's top matches in the body."""
    top = store.ranked([r for r in store.all() if store.visible(r)])[:10]
    subject = f"jobradar {today().isoformat()}: your jobs spreadsheet ({summary_line(store)})"
    intro = f"Attached: {path.name} with every job jobradar is tracking for you ({summary_line(store)})."
    _, plain, rich = build_digest(top, store, sum(1 for r in top if r.get("first_seen") == today().isoformat()))
    return send_email(subject, intro + "\n\nTop open matches:\n\n" + plain.split("\n\n", 1)[-1],
                      html.escape(intro) + "\n\n<b>Top open matches</b>\n\n" + rich.split("\n\n", 1)[-1],
                      to=to, attachments=[path])


def github_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")


def deliver(store: Store, cfg: dict, total_new: int, spreadsheet: Path | None = None) -> dict:
    """Send today's top matches. With notify.attach_spreadsheet (default on), the email carries jobs.xlsx and
    Telegram gets it as a file."""
    items = pick(store, cfg)
    if not items:
        return {"items": 0}
    subject, plain, rich = build_digest(items, store, total_new)
    attach = [spreadsheet] if spreadsheet and spreadsheet.exists() and cfg.get("attach_spreadsheet", True) else []
    result = {"items": len(items)}
    if cfg.get("telegram", True):
        result["telegram"] = send_telegram(rich)
        if attach and result["telegram"] == "sent":
            result["telegram file"] = send_telegram_file(attach[0], f"All tracked jobs ({summary_line(store)})")
    if cfg.get("email", True):
        result["email"] = send_email(subject, plain, rich, attachments=attach)
    if any(v == "sent" for k, v in result.items() if k != "items"):
        for rec in items:
            rec["notified"] = True
    return result
