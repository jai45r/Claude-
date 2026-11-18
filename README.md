# PE Designs: lead generation assistant

An AI research assistant that finds companies likely to need **traffolyte labels and electrical signage**.
These are switchboard builders, electrical contractors, panel builders and head contractors. For each one it:

1. reads the company's website and searches for current projects, tenders, contract wins and job ads
2. decides honestly whether PE Designs can help (fit score 1–5, with the evidence)
3. finds the right person (procurement, estimating, production or project manager, or the owner at a small firm)
4. writes a short, specific email and a LinkedIn connection note

**Nothing is sent without your OK.** You open a local dashboard, read the research, edit the draft and press
**Send**. LinkedIn notes are copied and sent by you, so the PE Designs LinkedIn account is never logged into by a bot.

## How a week looks

```
python -m leadagent discover "switchboard manufacturers in NSW doing data centre work" --limit 10
python -m leadagent add "Big Switchboards Pty Ltd" --website https://example.com.au --notes "met their PM at a trade night"
python -m leadagent research --all-new --max 10     # ~1-3 minutes per company
python -m leadagent dashboard                       # review, edit, send at http://127.0.0.1:5050
python -m leadagent check-replies                   # marks replies / opt-outs from the inbox
python -m leadagent followups                       # drafts follow-ups for no-reply emails (7 days)
```

You can also import a list of tier-1 companies you already know: make a CSV with the columns
`name,website,location,notes` and run `python -m leadagent import mylist.csv`.

## Setup (one time, about 20 minutes)

1. Install Python 3.11+, then in this folder run:
   ```
   python -m venv .venv
   .venv\Scripts\activate        (Windows)   or   source .venv/bin/activate   (Mac)
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env`.
3. Get a Claude API key at console.anthropic.com → API keys. Put it in `.env` as `ANTHROPIC_API_KEY=`.
4. **Edit `config/business_profile.md`.** Add your name, phone, ABN, turnaround times, materials and any
   projects you can mention. The agent only claims what is written there, so this file sets how good the emails are.
5. Leave `MAIL_MODE=dry_run` for the first batch. Approved emails are written to `data/outbox/` instead of
   being sent, so you can check them first.

### Connect Microsoft 365 (to send from admin@pdesigns.com.au)

The app sends through Microsoft Graph, not a password. Microsoft has switched off basic password sign-in for
SMTP, and Graph is the supported way to send. Whoever administers your Microsoft 365 does this once:

1. Go to entra.microsoft.com → **App registrations → New registration** and name it "PE Designs lead assistant".
2. **API permissions → Add → Microsoft Graph → Application permissions**, then add `Mail.Send` and `Mail.Read`.
   Then click **Grant admin consent**.
3. **Certificates & secrets → New client secret.** Copy the value.
4. Put the **Directory (tenant) ID**, **Application (client) ID** and the secret into `.env` as `MS_TENANT_ID`,
   `MS_CLIENT_ID` and `MS_CLIENT_SECRET`, then set `MAIL_MODE=graph`.
5. Recommended: limit the app to the admin mailbox only with an Exchange
   [application access policy](https://learn.microsoft.com/graph/auth-limit-mailbox-access). Otherwise the app could
   read or send as any mailbox in the tenant.

Sent emails appear in the admin mailbox's **Sent Items** as normal, and replies arrive in the inbox as usual.

## Staying on the right side of the Spam Act 2003 and LinkedIn

These rules are built in, not left to good intentions:

- **Consent:** the agent only uses email addresses **published** on a web page, and saves the page so you can check it.
  It never guesses addresses like firstname.lastname@. It only targets people whose role relates to buying labels or
  running projects, which is the basis for "inferred consent" for B2B email in Australia.
- **Identify:** every email gets a footer with your name, PE Designs and how to reach you.
- **Unsubscribe:** every email says "reply unsubscribe". `check-replies` puts anyone who replies
  unsubscribe, stop or "not interested" on the do-not-contact list automatically. The law requires opt-outs to be
  honoured within 5 business days, so run `check-replies` at least twice a week.
- **Volume:** at most `MAX_SENDS_PER_DAY` emails (default 15) and 2 follow-ups per person. Keeping the volume low
  protects your domain from spam filters.
- **LinkedIn:** the agent uses public search results only. You send messages yourself, which keeps the
  company page within LinkedIn's User Agreement.

This is a sensible setup, not legal advice. If you scale up a lot, a short chat with your accountant or lawyer
about the Spam Act is worthwhile.

## Costs

The research uses Claude Opus 5.5 with web search. Expect very roughly **AU$0.50–$2 per company researched**,
plus Anthropic's web search fee. To make it cheaper, set `LEADAGENT_EFFORT=medium` in `.env`. Set a monthly
spend limit in the Anthropic console.

## Files

| Path | What it is |
|---|---|
| `config/business_profile.md` | What PE Designs sells and who it's for. **Edit this.** |
| `leadagent/agent.py` | The research agent: prompts, rules and what it saves |
| `leadagent/web.py` | The review dashboard |
| `leadagent/mailer.py` | Microsoft 365 sending, reply and opt-out checking, footer |
| `leadagent/db.py` | Local database (`data/leads.db`) |
| `data/` | Your leads, kept on your computer. Not committed to git. Back it up. |
