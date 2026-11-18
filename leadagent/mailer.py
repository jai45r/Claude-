"""Sending approved emails (Microsoft 365 via Graph, or dry-run) and checking for replies."""
import re
from datetime import datetime, timedelta, timezone

import requests

from . import config, db

GRAPH = "https://graph.microsoft.com/v1.0"
UNSUB_WORDS = re.compile(r"\b(unsubscribe|remove me|stop emailing|do not contact|don't contact|not interested)\b", re.I)


def footer() -> str:
    """Spam Act: identify the sender and give a working way to opt out."""
    return (
        "\n\n--\n"
        "{signature}\n"
        "P Designs | pdesigns.com.au | " + config.SENDER_EMAIL + "\n"
        "You're receiving this because your contact details are published in connection with your role. "
        "If you'd rather not hear from us, reply \"unsubscribe\" and we won't contact you again."
    )


def signature() -> str:
    """Pull the 'Sender name' line from the business profile."""
    for line in config.business_profile().splitlines():
        if "Sender name" in line and ":" in line:
            return line.split(":", 1)[1].strip()
    return "P Designs"


def _token() -> str:
    if not (config.MS_TENANT_ID and config.MS_CLIENT_ID and config.MS_CLIENT_SECRET):
        raise RuntimeError("Microsoft 365 is not configured. Set MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET in .env")
    r = requests.post(
        f"https://login.microsoftonline.com/{config.MS_TENANT_ID}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials", "client_id": config.MS_CLIENT_ID,
              "client_secret": config.MS_CLIENT_SECRET, "scope": "https://graph.microsoft.com/.default"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def sends_today() -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with db.connect() as c:
        return c.execute("SELECT count(*) FROM drafts WHERE channel='email' AND status='sent' AND sent_at>=?",
                         (start,)).fetchone()[0]


def send_draft(draft_id: int, subject: str, body: str) -> str:
    """Send one approved email draft. Returns a short status message; raises on refusal."""
    with db.connect() as c:
        d = c.execute(
            "SELECT d.*, ct.email, ct.name AS contact_name, co.status AS co_status FROM drafts d "
            "JOIN contacts ct ON ct.id=d.contact_id JOIN companies co ON co.id=d.company_id WHERE d.id=?",
            (draft_id,),
        ).fetchone()
    if d is None or d["channel"] != "email":
        raise ValueError("Not an email draft")
    if d["status"] != "pending":
        raise ValueError(f"Draft is already {d['status']}")
    to = (d["email"] or "").strip()
    if not to:
        raise ValueError("Contact has no email address")
    if db.is_suppressed(to) or d["co_status"] == "do_not_contact":
        raise ValueError(f"{to} is on the do-not-contact list")
    if sends_today() >= config.MAX_SENDS_PER_DAY:
        raise ValueError(f"Daily limit of {config.MAX_SENDS_PER_DAY} emails reached. Try again tomorrow.")

    if config.MAIL_MODE == "graph" and "[" in signature():
        raise ValueError("Fill in your name and title in config/business_profile.md before sending live emails.")
    full_body = body.rstrip() + footer().replace("{signature}", signature())

    if config.MAIL_MODE == "graph":
        r = requests.post(
            f"{GRAPH}/users/{config.SENDER_EMAIL}/sendMail",
            headers={"Authorization": f"Bearer {_token()}"},
            json={"message": {"subject": subject, "body": {"contentType": "Text", "content": full_body},
                              "toRecipients": [{"emailAddress": {"address": to, "name": d["contact_name"] or ""}}]},
                  "saveToSentItems": True},
            timeout=30,
        )
        if r.status_code >= 300:
            raise RuntimeError(f"Microsoft 365 refused the send ({r.status_code}): {r.text[:300]}")
        where = "sent"
    else:
        config.OUTBOX_DIR.mkdir(exist_ok=True)
        path = config.OUTBOX_DIR / f"draft-{draft_id}.txt"
        path.write_text(f"To: {to}\nSubject: {subject}\n\n{full_body}\n", encoding="utf-8")
        where = f"DRY RUN - written to {path} (nothing was emailed)"

    with db.connect() as c:
        c.execute("UPDATE drafts SET status='sent', subject=?, body=?, sent_at=? WHERE id=?",
                  (subject, body, db.now(), draft_id))
        c.execute("UPDATE companies SET status='contacted', updated_at=? WHERE id=? AND status='researched'",
                  (db.now(), d["company_id"]))
        db.log(c, d["company_id"], "email_sent", f"{to}: {subject} ({config.MAIL_MODE})")
    return where


def check_replies(days: int = 45) -> list:
    """Look in the Microsoft 365 inbox for replies from people we've emailed.

    Marks the company 'replied' and, if the reply asks to stop, adds the address to the
    do-not-contact list so no follow-ups go out.
    """
    if config.MAIL_MODE != "graph":
        return ["Reply checking needs MAIL_MODE=graph. In dry-run mode, mark replies in the dashboard."]
    with db.connect() as c:
        rows = c.execute(
            "SELECT DISTINCT ct.email, d.company_id FROM drafts d JOIN contacts ct ON ct.id=d.contact_id "
            "WHERE d.channel='email' AND d.status='sent'").fetchall()
    sent_to = {r["email"].lower(): r["company_id"] for r in rows if r["email"]}
    if not sent_to:
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (f"{GRAPH}/users/{config.SENDER_EMAIL}/mailFolders/inbox/messages"
           f"?$filter=receivedDateTime ge {since}&$select=from,subject,bodyPreview,receivedDateTime&$top=100")
    headers = {"Authorization": f"Bearer {_token()}"}
    found = []
    while url:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        for m in data.get("value", []):
            sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
            if sender not in sent_to:
                continue
            cid = sent_to[sender]
            text = f"{m.get('subject', '')} {m.get('bodyPreview', '')}"
            co = db.get_company(cid)
            if UNSUB_WORDS.search(text):
                db.suppress(sender, "asked to stop: " + text[:120])
                if co["status"] != "do_not_contact":
                    db.set_company_status(cid, "do_not_contact", f"{sender} opted out")
                    found.append(f"{co['name']}: {sender} opted out (added to do-not-contact)")
            elif co["status"] in ("contacted", "researched"):
                db.set_company_status(cid, "replied", f"{sender}: {m.get('subject', '')}")
                found.append(f"{co['name']}: reply from {sender} - \"{m.get('subject', '')}\"")
        url = data.get("@odata.nextLink")
    return found
