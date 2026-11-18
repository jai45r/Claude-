"""Local review dashboard: read the research, edit drafts, approve & send, track LinkedIn and replies."""
import json
import secrets

from flask import Flask, abort, flash, redirect, render_template_string, request, url_for

from . import config, db, mailer

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
TOKEN = secrets.token_urlsafe(16)  # blocks other websites from posting to this local app


@app.before_request
def _csrf():
    if request.method == "POST" and request.form.get("_t") != TOKEN:
        abort(403)


BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>P Designs leads</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--mute:#5d6678;--line:#dfe3ea;--accent:#1f5fbf;--ok:#1d7a46;--warn:#a5531a}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--card:#1d2128;--ink:#e6e9ef;--mute:#9aa3b2;--line:#2e343e;--accent:#6ea2ff;--ok:#5cc28a;--warn:#e39a5f}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--ink)}
header{display:flex;gap:18px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);background:var(--card);flex-wrap:wrap}
header b{margin-right:auto}a{color:var(--accent)}main{max-width:980px;margin:0 auto;padding:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:0 0 16px}
.mute{color:var(--mute);font-size:13px}.pill{display:inline-block;padding:1px 8px;border-radius:99px;border:1px solid var(--line);font-size:12px}
input[type=text],textarea{width:100%;font:inherit;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
textarea{min-height:190px}button{font:inherit;padding:7px 14px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.flash{padding:10px;border-radius:6px;border:1px solid var(--line);background:var(--card);margin-bottom:12px}
table{width:100%;border-collapse:collapse}td,th{padding:6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
details summary{cursor:pointer;color:var(--mute)}ul{padding-left:20px;margin:6px 0}.mode{color:var(--warn);font-weight:600}
</style></head><body><header><b>P Designs · Lead assistant</b>
<a href="{{ url_for('queue') }}">Review queue</a><a href="{{ url_for('companies') }}">Companies</a>
<a href="{{ url_for('dnc') }}">Do not contact</a>
<span class="mode">{{ 'DRY RUN' if mode != 'graph' else 'LIVE: sending from ' + sender }}</span></header><main>
{% for m in get_flashed_messages() %}<div class="flash">{{ m }}</div>{% endfor %}
{{ body|safe }}</main></body></html>"""


def page(tpl, **kw):
    body = render_template_string(tpl, t=TOKEN, **kw)
    return render_template_string(BASE, body=body, mode=config.MAIL_MODE, sender=config.SENDER_EMAIL)


QUEUE = """
<p class="mute">{{ drafts|length }} drafts waiting. Sent today: {{ sent_today }}/{{ cap }}.
Nothing is sent until you press Send.</p>
{% for d in drafts %}{% set r = research.get(d.company_id, {}) %}
<div class="card">
  <div><b>{{ d.company }}</b> <span class="pill">fit {{ d.fit_score }}/5</span>
   <span class="pill">{{ d.channel }} · {{ d.kind }}</span>
   {% if d.website %}<a class="mute" href="{{ d.website }}" target="_blank" rel="noopener">{{ d.website }}</a>{% endif %}</div>
  <p>{{ r.get('company_summary','') }}</p>
  <details><summary>Research, contacts and sources</summary>
   <p><b>Why a fit:</b> {{ d.fit_summary }}</p>
   {% if r.get('opportunity_hooks') %}<b>Opportunities</b><ul>{% for h in r.opportunity_hooks %}<li>{{ h }}</li>{% endfor %}</ul>{% endif %}
   <p><b>Suggested approach:</b> {{ r.get('recommended_approach','') }}</p>
   <b>Contacts</b><ul>{% for c in r.get('contacts',[]) %}<li>{{ c.name }}, {{ c.title }}
     {% if c.email %} · {{ c.email }} (<a href="{{ c.email_source_url }}" target="_blank" rel="noopener">where published</a>){% endif %}
     {% if c.linkedin_url %} · <a href="{{ c.linkedin_url }}" target="_blank" rel="noopener">LinkedIn</a>{% endif %}
     <div class="mute">{{ c.why_this_person }}</div></li>{% endfor %}</ul>
   <b>Sources</b><ul>{% for s in r.get('sources',[]) %}<li><a href="{{ s }}" target="_blank" rel="noopener">{{ s }}</a></li>{% endfor %}</ul>
  </details>
  <p><b>To:</b> {{ d.contact_name }}{% if d.contact_title %}, {{ d.contact_title }}{% endif %}
   {% if d.channel == 'email' %}&lt;{{ d.email }}&gt;{% elif d.linkedin_url %} · <a href="{{ d.linkedin_url }}" target="_blank" rel="noopener">open LinkedIn profile</a>{% endif %}</p>
  <form method="post" action="{{ url_for('act', draft_id=d.id) }}"><input type="hidden" name="_t" value="{{ t }}">
   {% if d.channel == 'email' %}<input type="text" name="subject" value="{{ d.subject or '' }}" aria-label="Subject">{% endif %}
   <textarea name="body" aria-label="Message" id="b{{ d.id }}" style="margin-top:6px">{{ d.body }}</textarea>
   {% if d.channel == 'email' %}<p class="mute">Your signature, business details and the unsubscribe line are added automatically.</p>
   {% else %}<p class="mute">{{ d.body|length }} characters. Paste this into LinkedIn yourself, then mark it sent.</p>{% endif %}
   <div class="row">
    {% if d.channel == 'email' %}<button class="primary" name="action" value="send">Send email</button>
    {% else %}<button type="button" onclick="navigator.clipboard.writeText(document.getElementById('b{{ d.id }}').value)">Copy</button>
    <button class="primary" name="action" value="li_sent">I sent it on LinkedIn</button>{% endif %}
    <button name="action" value="save">Save edits</button>
    <button name="action" value="reject">Skip</button>
    <button name="action" value="dnc">Skip &amp; never contact this company</button>
   </div></form>
</div>{% else %}<div class="card">No drafts waiting. Run <code>python -m leadagent research --all-new</code>.</div>{% endfor %}
"""


@app.route("/")
def queue():
    with db.connect() as c:
        drafts = [dict(r) for r in c.execute(
            "SELECT d.*, co.name AS company, co.website, co.fit_score, co.fit_summary, co.research_json, "
            "ct.name AS contact_name, ct.title AS contact_title, ct.email, ct.linkedin_url "
            "FROM drafts d JOIN companies co ON co.id=d.company_id LEFT JOIN contacts ct ON ct.id=d.contact_id "
            "WHERE d.status='pending' AND co.status NOT IN ('do_not_contact') "
            "ORDER BY co.fit_score DESC, d.id")]
    research = {d["company_id"]: json.loads(d["research_json"] or "{}") for d in drafts}
    return page(QUEUE, drafts=drafts, research=research, sent_today=mailer.sends_today(),
                cap=config.MAX_SENDS_PER_DAY)


@app.post("/draft/<int:draft_id>")
def act(draft_id):
    action = request.form.get("action")
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    with db.connect() as c:
        d = c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if d is None:
            abort(404)
        if action in ("save", "send", "li_sent"):
            c.execute("UPDATE drafts SET subject=?, body=? WHERE id=?", (subject or d["subject"], body, draft_id))
    if action == "send":
        try:
            flash("Email: " + mailer.send_draft(draft_id, subject, body))
        except Exception as e:
            flash(f"Not sent: {e}")
    elif action == "li_sent":
        with db.connect() as c:
            c.execute("UPDATE drafts SET status='sent', sent_at=? WHERE id=?", (db.now(), draft_id))
            c.execute("UPDATE companies SET status='contacted', updated_at=? WHERE id=? AND status='researched'",
                      (db.now(), d["company_id"]))
            db.log(c, d["company_id"], "linkedin_sent", body[:200])
        flash("Marked as sent on LinkedIn.")
    elif action == "reject":
        with db.connect() as c:
            c.execute("UPDATE drafts SET status='rejected' WHERE id=?", (draft_id,))
        flash("Skipped.")
    elif action == "dnc":
        co = db.get_company(d["company_id"])
        with db.connect() as c:
            c.execute("UPDATE drafts SET status='rejected' WHERE company_id=? AND status='pending'", (d["company_id"],))
        if co["domain"]:
            db.suppress(co["domain"], "owner chose not to contact")
        db.set_company_status(d["company_id"], "do_not_contact", "from review queue")
        flash(f"{co['name']} will not be contacted.")
    else:
        flash("Saved.")
    return redirect(url_for("queue"))


COMPANIES = """
<div class="card"><table><tr><th>Company</th><th>Status</th><th>Fit</th><th>Last update</th><th>Set status</th></tr>
{% for co in rows %}<tr><td><b>{{ co.name }}</b><div class="mute">{{ co.location or '' }} {{ co.website or '' }}</div>
 <div class="mute">{{ (co.fit_summary or co.notes or '')[:180] }}</div></td>
 <td>{{ co.status }}</td><td>{{ co.fit_score or '-' }}</td><td class="mute">{{ co.updated_at[:10] }}</td>
 <td><form method="post" action="{{ url_for('set_status', cid=co.id) }}"><input type="hidden" name="_t" value="{{ t }}">
 <div class="row" style="margin:0">{% for s in ['replied','won','not_a_fit','do_not_contact','new'] %}
 <button name="status" value="{{ s }}">{{ s.replace('_',' ') }}</button>{% endfor %}</div></form></td></tr>
{% endfor %}</table></div>
<p class="mute">"new" queues a company to be researched again on the next <code>research --all-new</code> run.</p>"""


@app.route("/companies")
def companies():
    with db.connect() as c:
        rows = c.execute("SELECT * FROM companies ORDER BY updated_at DESC").fetchall()
    return page(COMPANIES, rows=rows)


@app.post("/companies/<int:cid>")
def set_status(cid):
    status = request.form["status"]
    co = db.get_company(cid)
    if status == "do_not_contact" and co["domain"]:
        db.suppress(co["domain"], "owner chose not to contact")
    if status in ("do_not_contact", "replied", "won", "not_a_fit"):
        with db.connect() as c:  # stop any queued drafts/follow-ups
            c.execute("UPDATE drafts SET status='rejected' WHERE company_id=? AND status='pending'", (cid,))
    db.set_company_status(cid, status, "set in dashboard")
    flash(f"{co['name']}: {status.replace('_', ' ')}")
    return redirect(url_for("companies"))


DNC = """<div class="card"><form method="post"><input type="hidden" name="_t" value="{{ t }}">
<label>Email address or domain (e.g. someone@acme.com.au or acme.com.au)</label>
<div class="row"><input type="text" name="value" required><input type="text" name="reason" placeholder="Reason (optional)">
<button class="primary">Add</button></div></form></div>
<div class="card"><table><tr><th>Address / domain</th><th>Reason</th><th>Added</th></tr>
{% for r in rows %}<tr><td>{{ r.value }}</td><td>{{ r.reason }}</td><td class="mute">{{ r.created_at[:10] }}</td></tr>{% endfor %}
</table></div>"""


@app.route("/do-not-contact", methods=["GET", "POST"])
def dnc():
    if request.method == "POST":
        db.suppress(request.form["value"], request.form.get("reason") or "added manually")
        flash("Added to do-not-contact list.")
        return redirect(url_for("dnc"))
    with db.connect() as c:
        rows = c.execute("SELECT * FROM suppression ORDER BY created_at DESC").fetchall()
    return page(DNC, rows=rows)
