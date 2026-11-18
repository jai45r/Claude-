"""The research agent: Claude with web search/fetch plus one 'save' tool per job.

Three jobs:
  discover  - find new candidate companies that match a search brief
  research  - deep-dive one company, judge fit, find the right person, draft outreach
  followup  - draft a short follow-up to an email that got no reply

Nothing here sends anything. Drafts go into the database for a human to approve.
"""
import json
from datetime import date

import anthropic

from . import config, db

client = anthropic.Anthropic()

WEB_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 12,
     "user_location": {"type": "approximate", "country": "AU"}},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 12},
]

RULES = """
## Rules you must follow

Honesty
- Only state facts you found in a source during this research. Never invent projects, people, emails or claims.
- Never claim PE Designs has worked with a company, or has capabilities, unless the business profile says so.

Contacts and privacy (Australian Spam Act 2003 + Privacy Act)
- Only record an email address that is published on a web page you saw (the company's website, a directory,
  a tender document, a press release). Put that page in email_source_url.
- NEVER guess or construct email addresses from patterns (e.g. firstname.lastname@). If no person's email is
  published, use a published role/generic address (sales@, estimating@, procurement@, info@) if one exists,
  otherwise leave email empty and rely on LinkedIn.
- A contact's role must relate to buying labels/signage or running projects (procurement, purchasing,
  estimating, project/operations/production management, engineering, ownership of a small firm). Otherwise
  we have no grounds to email them.
- LinkedIn profile URLs may come from search results. Do not try to log in to LinkedIn or scrape it.

Writing
- First email: under 120 words, plain text, no emojis, no hype, no "I hope this email finds you well".
- Open with one specific, true detail about their work (a project, product line, expansion, tender, hire).
- Then one sentence on how PE Designs specifically helps with that. Then ONE low-effort question.
- Do NOT write a signature, address or unsubscribe line - the system adds these.
- LinkedIn connection note: under 280 characters, friendly, specific, no pitch-dump.
"""


def _system(job_instructions: str) -> list:
    text = (
        "You are the business-development research assistant for PE Designs, an Australian maker of "
        "engraved traffolyte labels and electrical signage. You work for the owner. Your job is careful, "
        "useful research and genuinely relevant outreach, never spam. A wrong or creepy email costs us "
        "more than a missed lead, so when in doubt, say it is not a fit.\n\n"
        "# Business profile (from the owner)\n\n" + config.business_profile() + "\n" + RULES +
        "\n# Your task\n" + job_instructions
    )
    return [{"type": "text", "text": text}]


def _run(system: list, user: str, save_tool: dict, on_save, use_web=True, max_turns=40):
    """Manual agent loop. Returns True if the save tool was called successfully."""
    tools = (WEB_TOOLS if use_web else []) + [save_tool]
    messages = [{"role": "user", "content": user}]
    saved = False
    for _ in range(max_turns):
        resp = client.beta.messages.create(
            model=config.MODEL,
            max_tokens=16000,
            system=system,
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": config.EFFORT},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("Claude declined this request.")
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue
        if resp.stop_reason == "max_tokens":
            messages.append({"role": "user", "content": "You ran out of room. Be briefer and call the save tool now."})
            continue
        calls = [b for b in resp.content if b.type == "tool_use"]
        if not calls:
            if saved:
                return True
            messages.append({"role": "user", "content": f"Please finish by calling {save_tool['name']}."})
            continue
        results = []
        for call in calls:
            if call.name != save_tool["name"]:
                results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                "content": f"Unknown tool {call.name}"})
                continue
            try:
                msg = on_save(call.input)
                saved = True
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": msg})
            except Exception as e:  # report bad input back so Claude can fix it
                results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                "content": f"Could not save: {e}"})
        messages.append({"role": "user", "content": results})
        if saved and resp.stop_reason == "end_turn":
            return True
    return saved


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


S = {"type": "string"}

# ---------------------------------------------------------------- research

RESEARCH_TOOL = {
    "name": "save_research",
    "description": "Save your findings and outreach drafts for this company. Call exactly once, at the end.",
    "strict": True,
    "input_schema": _obj({
        "company_summary": {**S, "description": "2-4 sentences: what they do, size, where, sectors."},
        "what_they_build": {**S, "description": "Products/services relevant to labelling (switchboards, MCCs, panels, fit-outs...)."},
        "fit_score": {"type": "integer", "description": "1-5. 1-2 = not a fit (no drafts will be made), 3 = maybe, 4-5 = strong."},
        "fit_reasoning": {**S, "description": "Why that score, citing evidence."},
        "opportunity_hooks": {"type": "array", "items": S,
                              "description": "Specific, dated, sourced hooks: current projects, tenders, contract wins, expansions, job ads."},
        "recommended_approach": {**S, "description": "Advice for the owner: angle, timing, who, anything to avoid."},
        "contacts": {"type": "array", "items": _obj({
            "name": {**S, "description": "Person's name, or a role like 'Estimating team' for a generic inbox."},
            "title": S,
            "email": {**S, "description": "Published address only, or empty string."},
            "email_source_url": {**S, "description": "Page where the email is published, or empty string."},
            "linkedin_url": {**S, "description": "LinkedIn profile URL, or empty string."},
            "why_this_person": S,
        })},
        "primary_contact_index": {"type": "integer", "description": "Index in contacts to email, or -1 if no one has a published email."},
        "linkedin_contact_index": {"type": "integer", "description": "Index in contacts to approach on LinkedIn, or -1."},
        "email_subject": {**S, "description": "Short, specific, lowercase-friendly subject. Empty if no email."},
        "email_body": {**S, "description": "Body only, starting 'Hi <first name>,'. No signature. Empty if no email."},
        "linkedin_note": {**S, "description": "Connection note under 280 chars. Empty if no LinkedIn contact."},
        "sources": {"type": "array", "items": S, "description": "URLs you relied on."},
    }),
}

RESEARCH_JOB = f"""Research ONE company (given by the user) and decide whether PE Designs can genuinely help them.
Today is {date.today().isoformat()}.

1. Read their website: what they build, sectors, locations, size, recent news/projects.
2. Search for current or recent projects, tenders, contract awards, expansions and job ads
   (e.g. they are hiring switchboard fitters = growing workload). Note dates; ignore anything stale (>2 years).
3. Decide fit (1-5) against the business profile. Be strict.
4. If fit >= 3, find 1-3 people who'd decide on or influence buying labels/signage
   (procurement, purchasing, estimating, production or project managers; the owner at a small firm).
   Use their website (team/contact pages), press releases, tender documents and web search results for LinkedIn profiles.
5. Draft the email and LinkedIn note following the rules.
6. Call save_research once."""


def research_company(company_id: int) -> dict:
    co = db.get_company(company_id)
    if co is None:
        raise ValueError(f"No company with id {company_id}")
    if co["domain"] and db.is_suppressed("x@" + co["domain"]):
        db.set_company_status(company_id, "do_not_contact", "domain on do-not-contact list")
        return {}
    out = {}

    def on_save(data):
        if not 1 <= int(data["fit_score"]) <= 5:
            raise ValueError("fit_score must be 1-5")
        for ct in data["contacts"]:
            if ct["email"] and not ct["email_source_url"]:
                raise ValueError(f"email {ct['email']} has no email_source_url; remove it or give the page")
            if ct["email"] and db.is_suppressed(ct["email"]):
                ct["email"] = ""
        if len(data["linkedin_note"]) > 300:
            raise ValueError("linkedin_note is over 300 characters")
        db.save_research(company_id, data)
        out.update(data)
        return "Saved."

    user = (f"Company: {co['name']}\nWebsite: {co['website'] or 'unknown - find it'}\n"
            f"Location: {co['location'] or 'unknown'}\nOwner's notes: {co['notes'] or 'none'}")
    ok = _run(_system(RESEARCH_JOB), user, RESEARCH_TOOL, on_save)
    if not ok:
        db.set_company_status(company_id, "error", "research did not finish")
    return out

# ---------------------------------------------------------------- discover

DISCOVER_TOOL = {
    "name": "add_companies",
    "description": "Add candidate companies to the lead list. You may call it more than once.",
    "strict": True,
    "input_schema": _obj({
        "companies": {"type": "array", "items": _obj({
            "name": S,
            "website": {**S, "description": "Homepage URL."},
            "location": {**S, "description": "City/state."},
            "why": {**S, "description": "One line: why they might need traffolyte labels/signage, with evidence."},
            "source_url": S,
        })},
    }),
}


def discover(brief: str, limit: int = 10) -> list:
    added = []

    def on_save(data):
        msgs = []
        for co in data["companies"]:
            if len(added) >= limit:
                break
            cid, created = db.add_company(co["name"], co["website"], co["location"],
                                          source=f"discover: {co['source_url']}", notes=co["why"])
            msgs.append(f"{co['name']}: {'added' if created else 'already on list'}")
            if created:
                added.append(cid)
        return "; ".join(msgs) + f". {len(added)}/{limit} added so far."

    job = (f"Find up to {limit} Australian companies matching the owner's brief that are likely buyers of "
           "traffolyte labels / electrical signage. Prefer companies with evidence of current project work. "
           "Skip competitors and anything outside Australia/NZ. Verify each website exists. "
           "Save them with add_companies, then stop.")
    _run(_system(job), f"Brief: {brief}", DISCOVER_TOOL, on_save)
    return added

# ---------------------------------------------------------------- follow-up

FOLLOWUP_TOOL = {
    "name": "save_followup",
    "description": "Save the follow-up email.",
    "strict": True,
    "input_schema": _obj({"subject": S, "body": {**S, "description": "Under 70 words. No signature."}}),
}


def draft_followup(company_id: int, contact_id: int, previous: list, kind: str):
    co = db.get_company(company_id)
    research = json.loads(co["research_json"] or "{}")
    thread = "\n\n---\n\n".join(f"Subject: {p['subject']}\nSent: {p['sent_at']}\n\n{p['body']}" for p in previous)
    job = ("Write a short, polite follow-up to an outreach email that has had no reply. Add something new "
           "and useful (a different angle or a relevant detail from the research) rather than 'just checking in'. "
           "Make it easy to say no. Keep 'Re:' + the original subject. Call save_followup.")
    user = (f"Company research:\n{json.dumps(research, indent=1)[:6000]}\n\nEmails already sent:\n{thread}")
    result = {}

    def on_save(data):
        result.update(data)
        with db.connect() as c:
            c.execute(
                "INSERT INTO drafts (company_id, contact_id, channel, kind, subject, body, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (company_id, contact_id, "email", kind, data["subject"], data["body"], db.now()),
            )
        return "Saved."

    _run(_system(job), user, FOLLOWUP_TOOL, on_save, use_web=False, max_turns=4)
    return result
