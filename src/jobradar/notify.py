"""Digest delivery: Telegram bot, email (SMTP, e.g. Gmail app password), GitHub Actions run summary."""

from __future__ import annotations

import html
import os
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from .report import posted_label, why
from .store import Store
from .salary import label as pay_label
from .util import clip, today


def pick(store: Store, cfg: dict) -> list[dict]:
    """Unsent, visible matches at or above the score threshold - best first."""
    min_score = int(cfg.get("min_score", 55))
    items = [r for r in store.all() if store.visible(r) and not r.get("notified") and store.score_of(r) >= min_score]
    return store.ranked(items)[: int(cfg.get("top_n", 15))]


def _line(rec: dict, store: Store) -> tuple[str, str]:
    ai = rec.get("ai") or {}
    tag = f" [{ai['verdict']}]" if ai.get("verdict") else ""
    pay = f", {pay_label(rec.get('salary'))}" if rec.get("salary") else ""
    plain = (f"#{rec['id']} {store.score_of(rec)}{tag} {rec['company']} - {rec['title']} "
             f"({clip(rec.get('location') or '', 40)}{pay}, {posted_label(rec)})\n{rec['url']}\n{clip(why(rec), 160)}")
    rich = (f"<b>#{rec['id']} · {store.score_of(rec)}{html.escape(tag)}</b> {html.escape(rec['company'])} - "
            f"<a href=\"{html.escape(rec['url'], quote=True)}\">{html.escape(rec['title'])}</a>\n"
            f"<i>{html.escape(clip(rec.get('location') or '', 40))}{html.escape(pay.replace(',', ' ·', 1))} · "
            f"{posted_label(rec)}</i>\n"
            f"{html.escape(clip(why(rec), 160))}")
    return plain, rich


def build_digest(items: list[dict], store: Store, total_new: int) -> tuple[str, str, str]:
    head = f"jobradar {today().isoformat()}: {len(items)} top matches ({total_new} new today)"
    lines = [_line(r, store) for r in items]
    plain = head + "\n\n" + "\n\n".join(p for p, _ in lines)
    rich = f"<b>{html.escape(head)}</b>\n\n" + "\n\n".join(r for _, r in lines)
    footer = "\n\nMark progress: jobradar mark <id> applied   ·   Draft materials: jobradar draft <id>"
    return head, plain + footer, rich + html.escape(footer)


def send_telegram(rich_text: str) -> str:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return "skipped (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)"
    chunks, cur = [], ""
    for block in rich_text.split("\n\n"):  # Telegram caps messages at 4096 chars
        if len(cur) + len(block) + 2 > 3900:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n\n{block}" if cur else block
    chunks.append(cur)
    with httpx.Client(timeout=20) as client:
        for chunk in chunks:
            r = client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                            json={"chat_id": chat, "text": chunk, "parse_mode": "HTML",
                                  "disable_web_page_preview": True})
            if r.status_code != 200:
                return f"failed (HTTP {r.status_code})"
    return "sent"


def send_email(subject: str, plain: str, rich: str) -> str:
    user, password, to = (os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"))
    if not (user and password and to):
        return "skipped (SMTP_USER / SMTP_PASSWORD / EMAIL_TO not set)"
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(plain)
    msg.add_alternative("<div style='font-family:system-ui,sans-serif;white-space:pre-wrap'>"
                        + rich + "</div>", subtype="html")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    try:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        return f"failed ({type(exc).__name__})"
    return "sent"


def github_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")


def deliver(store: Store, cfg: dict, total_new: int) -> dict:
    items = pick(store, cfg)
    if not items:
        return {"items": 0}
    subject, plain, rich = build_digest(items, store, total_new)
    result = {"items": len(items)}
    if cfg.get("telegram", True):
        result["telegram"] = send_telegram(rich)
    if cfg.get("email", True):
        result["email"] = send_email(subject, plain, rich)
    if any(v == "sent" for k, v in result.items() if k != "items"):
        for rec in items:
            rec["notified"] = True
    return result
