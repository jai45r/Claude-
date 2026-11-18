"""Command line for the P Designs lead agent.

    python -m leadagent add "Company name" --website https://... [--location "Melbourne VIC"] [--notes "..."]
    python -m leadagent import companies.csv          # columns: name, website, location, notes
    python -m leadagent discover "switchboard manufacturers in Victoria" --limit 10
    python -m leadagent research [--id 3 | --all-new] [--max 5]
    python -m leadagent check-replies
    python -m leadagent followups
    python -m leadagent dashboard
    python -m leadagent list
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone

from . import config, db


def cmd_add(a):
    cid, created = db.add_company(a.name, a.website or "", a.location or "", "manual", a.notes or "")
    print(f"{'Added' if created else 'Already on list'}: #{cid} {a.name}")


def cmd_import(a):
    n = 0
    with open(a.file, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("name"):
                continue
            _, created = db.add_company(row["name"], row.get("website", ""), row.get("location", ""),
                                        "import", row.get("notes", ""))
            n += created
    print(f"Imported {n} new companies.")


def cmd_discover(a):
    from .agent import discover
    print(f"Searching for companies: {a.brief!r} ...")
    ids = discover(a.brief, a.limit)
    for cid in ids:
        co = db.get_company(cid)
        print(f"  #{cid} {co['name']} - {co['website']}  ({co['notes']})")
    print(f"{len(ids)} new companies added. Next: python -m leadagent research --all-new")


def cmd_research(a):
    from .agent import research_company
    with db.connect() as c:
        if a.id:
            ids = [a.id]
        else:
            ids = [r["id"] for r in c.execute(
                "SELECT id FROM companies WHERE status IN ('new','error') ORDER BY id LIMIT ?", (a.max,))]
    if not ids:
        print("Nothing to research. Add companies with 'add', 'import' or 'discover'.")
    for cid in ids:
        co = db.get_company(cid)
        print(f"Researching #{cid} {co['name']} ...", flush=True)
        try:
            r = research_company(cid)
            if r:
                print(f"  fit {r['fit_score']}/5 - {r['fit_reasoning'][:160]}")
        except Exception as e:
            db.set_company_status(cid, "error", str(e)[:300])
            print(f"  failed: {e}")
    print(f"Done. Review drafts: python -m leadagent dashboard")


def cmd_check_replies(a):
    from .mailer import check_replies
    for line in check_replies() or ["No new replies."]:
        print(line)


def cmd_followups(a):
    """Draft follow-ups for emails with no reply after FOLLOWUP_AFTER_DAYS. Drafts still need approval."""
    from .agent import draft_followup
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.FOLLOWUP_AFTER_DAYS)).isoformat()
    with db.connect() as c:
        threads = c.execute(
            "SELECT d.company_id, d.contact_id, count(*) AS n, max(d.sent_at) AS last_sent "
            "FROM drafts d JOIN companies co ON co.id=d.company_id "
            "WHERE d.channel='email' AND d.status='sent' AND co.status='contacted' "
            "GROUP BY d.company_id, d.contact_id").fetchall()
        pending = {(r[0], r[1]) for r in c.execute(
            "SELECT company_id, contact_id FROM drafts WHERE channel='email' AND status='pending'")}
    made = 0
    for t in threads:
        key = (t["company_id"], t["contact_id"])
        if t["last_sent"] > cutoff or t["n"] > config.MAX_FOLLOWUPS or key in pending:
            continue
        with db.connect() as c:
            prev = [dict(r) for r in c.execute(
                "SELECT subject, body, sent_at FROM drafts WHERE company_id=? AND contact_id=? "
                "AND channel='email' AND status='sent' ORDER BY sent_at", key)]
            email = c.execute("SELECT email FROM contacts WHERE id=?", (t["contact_id"],)).fetchone()[0]
        if db.is_suppressed(email):
            continue
        co = db.get_company(t["company_id"])
        print(f"Drafting follow-up {t['n']} for {co['name']} ...")
        draft_followup(t["company_id"], t["contact_id"], prev, f"followup{t['n']}")
        made += 1
    print(f"{made} follow-up drafts waiting for approval in the dashboard.")


def cmd_list(a):
    with db.connect() as c:
        for r in c.execute("SELECT id, name, status, fit_score, website FROM companies ORDER BY id"):
            print(f"#{r['id']:<4} {r['status']:<15} fit {r['fit_score'] or '-'}  {r['name']}  {r['website'] or ''}")


def cmd_dashboard(a):
    from .web import app
    print(f"Open http://127.0.0.1:{config.DASHBOARD_PORT}  (mail mode: {config.MAIL_MODE})")
    app.run(host="127.0.0.1", port=config.DASHBOARD_PORT, debug=False)


def main():
    db.init()
    p = argparse.ArgumentParser(prog="leadagent", description="P Designs lead generation assistant")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add", help="add a company you already know about")
    s.add_argument("name"); s.add_argument("--website"); s.add_argument("--location"); s.add_argument("--notes")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("import", help="import companies from a CSV (name, website, location, notes)")
    s.add_argument("file"); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("discover", help="let the agent find new companies")
    s.add_argument("brief"); s.add_argument("--limit", type=int, default=10); s.set_defaults(fn=cmd_discover)
    s = sub.add_parser("research", help="research companies and draft outreach")
    s.add_argument("--id", type=int); s.add_argument("--all-new", action="store_true")
    s.add_argument("--max", type=int, default=5); s.set_defaults(fn=cmd_research)
    sub.add_parser("check-replies", help="check the inbox for replies / opt-outs").set_defaults(fn=cmd_check_replies)
    sub.add_parser("followups", help="draft follow-ups for unanswered emails").set_defaults(fn=cmd_followups)
    sub.add_parser("list", help="list companies").set_defaults(fn=cmd_list)
    sub.add_parser("dashboard", help="open the review & approve dashboard").set_defaults(fn=cmd_dashboard)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
