"""SQLite storage for companies, contacts, drafts and the do-not-contact list."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT UNIQUE,
    website TEXT,
    location TEXT,
    source TEXT,
    notes TEXT,
    -- new | researched | not_a_fit | contacted | replied | won | do_not_contact | error
    status TEXT NOT NULL DEFAULT 'new',
    fit_score INTEGER,
    fit_summary TEXT,
    research_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    name TEXT,
    title TEXT,
    email TEXT,
    email_source_url TEXT,
    linkedin_url TEXT,
    why TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    contact_id INTEGER REFERENCES contacts(id),
    channel TEXT NOT NULL,          -- email | linkedin
    kind TEXT NOT NULL,             -- initial | followup1 | followup2 ...
    subject TEXT,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | rejected
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS suppression (
    value TEXT PRIMARY KEY,         -- an email address or a domain
    reason TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    company_id INTEGER,
    type TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def domain_of(url_or_email: str) -> str:
    s = (url_or_email or "").strip().lower()
    if "@" in s and "/" not in s:
        return s.split("@", 1)[1]
    if "://" not in s:
        s = "https://" + s
    host = urlparse(s).hostname or ""
    return host[4:] if host.startswith("www.") else host


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with connect() as c:
        c.executescript(SCHEMA)


def log(c, company_id, type_, detail=""):
    c.execute(
        "INSERT INTO events (company_id, type, detail, created_at) VALUES (?,?,?,?)",
        (company_id, type_, detail, now()),
    )


def add_company(name, website="", location="", source="manual", notes=""):
    """Insert a company. Returns (id, created). Deduplicates on website domain."""
    domain = domain_of(website) if website else None
    with connect() as c:
        if domain:
            row = c.execute("SELECT id FROM companies WHERE domain=?", (domain,)).fetchone()
            if row:
                return row["id"], False
        row = c.execute("SELECT id FROM companies WHERE lower(name)=lower(?)", (name,)).fetchone()
        if row:
            return row["id"], False
        cur = c.execute(
            "INSERT INTO companies (name, domain, website, location, source, notes, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (name, domain, website, location, source, notes, now(), now()),
        )
        log(c, cur.lastrowid, "added", source)
        return cur.lastrowid, True


def get_company(company_id):
    with connect() as c:
        return c.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()


def set_company_status(company_id, status, detail=""):
    with connect() as c:
        c.execute("UPDATE companies SET status=?, updated_at=? WHERE id=?", (status, now(), company_id))
        log(c, company_id, f"status:{status}", detail)


def is_suppressed(email: str) -> bool:
    email = (email or "").strip().lower()
    with connect() as c:
        row = c.execute(
            "SELECT 1 FROM suppression WHERE value=? OR value=?", (email, domain_of(email))
        ).fetchone()
        return row is not None


def suppress(value: str, reason: str):
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO suppression (value, reason, created_at) VALUES (?,?,?)",
            (value.strip().lower(), reason, now()),
        )


def save_research(company_id: int, result: dict):
    """Store the research agent's findings and create drafts for review."""
    fit = int(result.get("fit_score") or 0)
    with connect() as c:
        status = "researched" if fit >= 3 else "not_a_fit"
        c.execute(
            "UPDATE companies SET status=?, fit_score=?, fit_summary=?, research_json=?, updated_at=? WHERE id=?",
            (status, fit, result.get("fit_reasoning", ""), json.dumps(result), now(), company_id),
        )
        c.execute("DELETE FROM drafts WHERE company_id=? AND status='pending'", (company_id,))
        contact_ids = []
        for ct in result.get("contacts", []):
            cur = c.execute(
                "INSERT INTO contacts (company_id, name, title, email, email_source_url, linkedin_url, why, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (company_id, ct.get("name"), ct.get("title"), (ct.get("email") or "").strip().lower(),
                 ct.get("email_source_url"), ct.get("linkedin_url"), ct.get("why_this_person"), now()),
            )
            contact_ids.append(cur.lastrowid)
        log(c, company_id, "researched", f"fit {fit}/5")
        if fit < 3:
            return
        idx = result.get("primary_contact_index", -1)
        primary = contact_ids[idx] if 0 <= idx < len(contact_ids) else None
        primary_ct = result["contacts"][idx] if primary else {}
        if primary and primary_ct.get("email") and result.get("email_body"):
            c.execute(
                "INSERT INTO drafts (company_id, contact_id, channel, kind, subject, body, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (company_id, primary, "email", "initial", result.get("email_subject"), result["email_body"], now()),
            )
        li_idx = result.get("linkedin_contact_index", -1)
        if 0 <= li_idx < len(contact_ids) and result.get("linkedin_note"):
            c.execute(
                "INSERT INTO drafts (company_id, contact_id, channel, kind, subject, body, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (company_id, contact_ids[li_idx], "linkedin", "initial", None, result["linkedin_note"], now()),
            )
