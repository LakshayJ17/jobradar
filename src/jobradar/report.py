"""Reports: data/reports/latest.md (+ dated copy), latest.html (interactive, self-contained), data/jobs.csv."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from urllib.parse import quote_plus

from .filters import is_internship, is_unpaid
from .salary import in_units, monthly, stipend_label, stipend_short, unit
from .salary import label as pay_label
from .salary import short as pay_short
from .store import Store, status_label
from .util import clean_line, clip, days_ago, safe_url, today


def why(rec: dict) -> str:
    ai = rec.get("ai") or {}
    if ai.get("why"):
        gaps = f"; gaps: {', '.join(ai['gaps'])}" if ai.get("gaps") else ""
        return ai["why"] + gaps
    return "; ".join(rec.get("signals") or [])


def reviews_url(company: str) -> str:
    """A web search for the company's AmbitionBox / Glassdoor reviews, opened by you in the browser.
    Neither site has a public API and both forbid automated access, so jobradar never fetches ratings itself."""
    return "https://www.google.com/search?q=" + quote_plus(f"{company} reviews ambitionbox glassdoor")


def internship(rec: dict) -> bool:
    return is_internship(rec.get("title"), rec.get("employment_type"))


def pay_text(rec: dict, *, short: bool = False) -> str:
    """Pay as it should read: '₹6-9 LPA' for a job, '₹11k-20k a month' for an internship ('unpaid' when it says so).
    short=True: '6-9' / '11k-20k/mo', for table columns headed by the unit. '' when unknown."""
    sal = rec.get("salary")
    if internship(rec):
        if not sal and is_unpaid(rec.get("employment_type"), rec.get("description")):
            return "unpaid"
        return stipend_short(sal) if short else stipend_label(sal)
    return pay_short(sal) if short else pay_label(sal)


def pay_columns(rec: dict) -> tuple[float | None, float | None, str]:
    """(min, max, unit) for spreadsheets: LPA (or thousands a year) for jobs, a month for internships."""
    if internship(rec):
        vals = monthly(rec.get("salary"))
        return (round(vals[0]), round(vals[1]), "a month") if vals else (None, None, "")
    vals = in_units(rec.get("salary"))
    return (round(vals[0], 1), round(vals[1], 1), unit()) if vals else (None, None, "")


def posted_label(rec: dict) -> str:
    d = days_ago(rec.get("posted_at"))
    if d is None:
        return "?"
    return "today" if d <= 0 else f"{d}d ago"


def score_label(rec: dict) -> str:
    ai = rec.get("ai") or {}
    if isinstance(ai.get("fit"), int):
        return f"{ai['fit']} ({ai.get('verdict', '')})"
    return f"{rec.get('rule_score', 0)} (rules)"


_MD_SPECIAL = re.compile(r"([\\`*_\[\]!|#~])")


def _md_cell(text: str) -> str:
    """Job text in a markdown table cell (also shown as the GitHub Actions run summary): no HTML, no links or
    images of its own, no table breakouts, one line."""
    text = clean_line(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _MD_SPECIAL.sub(r"\\\1", text)


def _md_link(title: str, url: str) -> str:
    url = safe_url(url)
    if not url:
        return _md_cell(title)
    return f"[{_md_cell(title)}](<{url.replace('>', '%3E').replace('<', '%3C')}>)"


def _md_table(rows: list[dict]) -> str:
    lines = ["| ID | Score | Company | Role | Location | Pay | Posted | Status | Why |",
             "|---:|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| {id} | {score} | {company} | {title} | {loc} | {pay} | {posted} | {status} | {why} |".format(
            id=r["id"], score=score_label(r), company=_md_cell(r["company"]), title=_md_link(r["title"], r["url"]),
            loc=_md_cell(clip(r.get("location") or "", 40)),
            pay=pay_text(r) or "-",
            posted=posted_label(r), status=_md_cell(status_label(r)), why=_md_cell(clip(why(r), 140))))
    return "\n".join(lines)


def render_markdown(store: Store, stats: dict) -> str:
    visible = store.ranked([r for r in store.all() if store.visible(r)])
    new = [r for r in visible if r.get("first_seen") == today().isoformat()]
    active = [r for r in store.ranked() if r.get("status") in ("saved", "applied", "oa", "interview", "offer")]
    parts = [f"# jobradar - {today().isoformat()}", "",
             f"{len(new)} new matches today, {len(visible)} open matches, {len(active)} in progress, "
             f"{stats.get('fetched', 0)} postings checked across {stats.get('targets', 0)} boards", ""]
    if new:
        parts += [f"## New today ({len(new)})", "", _md_table(new[:40]), ""]
    if active:
        parts += [f"## In progress ({len(active)})", "", _md_table(active), ""]
    parts += [f"## All open matches ({len(visible)})", "", _md_table(visible[:200]), ""]
    if stats.get("errors"):
        parts += ["<details><summary>Boards that failed this run</summary>", ""]
        parts += [f"- {_md_cell(k)}: {_md_cell(v)}" for k, v in sorted(stats["errors"].items())]
        parts += ["", "</details>", ""]
    return "\n".join(parts)


def write_csv(store: Store, path: Path) -> None:
    def safe(value) -> str:  # stop spreadsheet formula injection from job text
        s = "" if value is None else str(value)
        return "'" + s if s.lstrip(" \t\r\n")[:1] in ("=", "+", "-", "@") or s[:1] in ("\t", "\r") else s

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "score", "ai_verdict", "ai_eligible", "company", "title", "location", "preferred_location",
                    "pay_min", "pay_max", "pay_unit", "posted_at", "first_seen", "status", "url", "why", "type"])
        for r in store.ranked([r for r in store.all() if not r.get("closed")]):
            ai = r.get("ai") or {}
            lo, hi, pay_unit = pay_columns(r)
            w.writerow([safe(x) for x in (r["id"], store.score_of(r), ai.get("verdict", ""), ai.get("eligible", ""),
                                          r["company"], r["title"], r.get("location"),
                                          "yes" if r.get("rank_bonus") else "", "" if lo is None else lo,
                                          "" if hi is None else hi, pay_unit, r.get("posted_at"),
                                          r.get("first_seen"), r.get("status"), r["url"], why(r),
                                          "internship" if internship(r) else "job")])


HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>jobradar</title>
<style>
:root{--bg:#f7f7f5;--panel:#fff;--text:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;--accent:#2f5bd3;--good:#1f7a4d;--mid:#9a6a00;--bad:#a33;}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--panel:#1d1d1d;--text:#ecece8;--muted:#9a9a94;--line:#2e2e2e;--accent:#7d9cff;--good:#5fc38f;--mid:#e0b04f;--bad:#f07c7c;}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:20px 16px 8px;max-width:1300px;margin:auto}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--muted)}
.controls{display:flex;flex-wrap:wrap;gap:8px;padding:8px 16px;max-width:1300px;margin:auto}
input,select{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 8px}
input[type=search]{flex:1;min-width:200px}label{display:flex;align-items:center;gap:6px;color:var(--muted)}
.wrap{max-width:1300px;margin:auto;padding:8px 16px 40px;overflow-x:auto}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted);cursor:pointer;white-space:nowrap;user-select:none}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.pill{display:inline-block;padding:1px 7px;border-radius:99px;font-size:12px;border:1px solid var(--line)}
.apply{color:var(--good);border-color:currentColor}.maybe{color:var(--mid);border-color:currentColor}.skip,.no{color:var(--bad);border-color:currentColor}
.why{color:var(--muted);max-width:420px}.rev{font-size:12px;color:var(--muted)}.pref{color:var(--accent);border-color:currentColor}.new{font-weight:600}
</style></head><body>
<header><h1>jobradar</h1><div class="sub" id="sub"></div></header>
<div class="controls">
<input type="search" id="q" placeholder="Search company, role, location, skills">
<select id="verdict"><option value="">Any verdict</option><option value="apply">AI: apply</option><option value="maybe">AI: maybe</option><option value="unscored">Not AI-scored</option></select>
<select id="status"><option value="">Any status</option></select>
<select id="kind"><option value="">Jobs and internships</option><option value="job">Jobs only</option><option value="intern">Internships only</option></select>
<label><input type="checkbox" id="onlynew"> new today</label>
<label><input type="checkbox" id="pref"> preferred cities + remote</label>
<label><input type="checkbox" id="paid"> pay listed</label>
<label><input type="checkbox" id="hidden"> show hidden</label>
</div>
<div class="wrap"><table><thead><tr>
<th data-k="id">#</th><th data-k="score">Score</th><th data-k="company">Company</th><th data-k="title">Role</th>
<th data-k="location">Location</th><th data-k="pay_max">Pay</th><th data-k="posted">Posted</th><th data-k="status">Status</th><th>Why</th>
</tr></thead><tbody id="rows"></tbody></table></div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);const rows=D.jobs;let sortK='rank',dir=-1;
document.getElementById('sub').textContent=D.summary;
const st=document.getElementById('status');[...new Set(rows.map(r=>r.status))].sort().forEach(s=>{const o=document.createElement('option');o.value=s;o.textContent=s;st.appendChild(o)});
function el(t,c,txt){const e=document.createElement(t);if(c)e.className=c;if(txt!=null)e.textContent=txt;return e}
function render(){const q=document.getElementById('q').value.toLowerCase(),v=document.getElementById('verdict').value,s=st.value,
on=document.getElementById('onlynew').checked,hid=document.getElementById('hidden').checked,pf=document.getElementById('pref').checked,pd=document.getElementById('paid').checked,
k=document.getElementById('kind').value;const tb=document.getElementById('rows');tb.textContent='';
rows.filter(r=>(hid||r.visible)&&(!on||r.is_new)&&(!pf||r.pref)&&(!pd||r.paid)&&(!k||(k==='intern')===r.intern)&&(!s||r.status===s)&&(!v||(v==='unscored'?!r.verdict:r.verdict===v))&&(!q||r.search.includes(q)))
.sort((a,b)=>{const x=a[sortK],y=b[sortK];return (x>y?1:x<y?-1:0)*dir}).forEach(r=>{const tr=el('tr',r.is_new?'new':'');
tr.appendChild(el('td','num',String(r.id)));const sc=el('td','num',String(r.score)+' ');if(r.verdict)sc.appendChild(el('span','pill '+r.verdict,r.verdict));
if(r.eligible==='no')sc.appendChild(el('span','pill no','ineligible'));tr.appendChild(sc);const co=el('td','',r.company+' ');const rv=el('a','rev','reviews');rv.href=r.reviews;rv.target='_blank';rv.rel='noopener noreferrer';co.appendChild(rv);tr.appendChild(co);
const t=el('td');const a=el('a','',r.title);a.href=r.url;a.target='_blank';a.rel='noopener noreferrer';t.appendChild(a);if(r.intern){t.appendChild(document.createTextNode(' '));t.appendChild(el('span','pill','internship'))}tr.appendChild(t);
const lo=el('td','',r.location+' ');if(r.pref)lo.appendChild(el('span','pill pref','preferred'));tr.appendChild(lo);tr.appendChild(el('td','num',r.pay||'-'));tr.appendChild(el('td','num',r.posted_label));tr.appendChild(el('td','',r.status));tr.appendChild(el('td','why',r.why));tb.appendChild(tr)})}
document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{const k=th.dataset.k;dir=(sortK===k)?-dir:(k==='score'?-1:1);sortK=k;render()});
['q','verdict','status','kind','onlynew','hidden','pref','paid'].forEach(id=>document.getElementById(id).addEventListener('input',render));render();
</script></body></html>"""


def render_html(store: Store, stats: dict) -> str:
    today_iso = today().isoformat()
    jobs = []
    for r in store.ranked():
        ai = r.get("ai") or {}
        url = safe_url(r["url"]) or "#"
        jobs.append({
            "id": r["id"], "score": store.score_of(r), "rank": store.score_of(r) + (r.get("rank_bonus") or 0),
            "pref": bool(r.get("rank_bonus")), "pay": pay_text(r), "paid": bool(r.get("salary")), "intern": internship(r),
            "pay_max": (r.get("salary") or [0, 0])[1], "reviews": reviews_url(r["company"]),
            "verdict": ai.get("verdict", ""),
            "eligible": ai.get("eligible", ""), "company": r["company"], "title": r["title"], "url": url,
            "location": clip(r.get("location") or "", 60), "posted": r.get("posted_at") or "",
            "posted_label": posted_label(r), "status": status_label(r), "why": clip(why(r), 260),
            "visible": store.visible(r), "is_new": r.get("first_seen") == today_iso,
            "search": " ".join([r["company"], r["title"], r.get("location") or "", why(r)]).lower(),
        })
    visible = sum(1 for j in jobs if j["visible"])
    new = sum(1 for j in jobs if j["visible"] and j["is_new"])
    summary = (f"{today_iso}: {new} new today, {visible} open matches, "
               f"{stats.get('fetched', 0)} postings checked across {stats.get('targets', 0)} boards")
    # Valid JSON that can't end the <script> element or open an HTML comment: <, > and & as \\u escapes.
    data = json.dumps({"summary": summary, "jobs": jobs}, ensure_ascii=False)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return HTML_TEMPLATE.replace("__DATA__", data)


def write_reports(store: Store, stats: dict, reports_dir: Path, csv_path: Path) -> dict[str, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    md = render_markdown(store, stats)
    paths = {"markdown": reports_dir / "latest.md", "html": reports_dir / "latest.html", "csv": csv_path}
    paths["markdown"].write_text(md, encoding="utf-8")
    (reports_dir / f"{today().isoformat()}.md").write_text(md, encoding="utf-8")
    paths["html"].write_text(render_html(store, stats), encoding="utf-8")
    write_csv(store, csv_path)
    return paths
