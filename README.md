# AI Sales Executive MVP

Spec: `docs/ai-sales-executive-mvp.json`. Build one day at a time; wait for owner approval between days.

## Status
- Day 1 (backend foundation, intake, agent, WhatsApp test mode, web chat): done.
- Day 2 (grading, price rules and quotes, guardrails, follow-ups, handoff and takeover, email): done.
- Day 3 (dashboard, analytics, daily report, CSV import/export, webhook export, widget embed, deploy files, end-to-end scenarios): done, built without real keys. 125 backend tests and 7 dashboard tests pass.
- Not done on purpose: registering the outside services (Anthropic, WhatsApp, email provider, Supabase, Render, Vercel). See `docs/DEPLOY.md`.

## Day 3 in short
- Dashboard (`dashboard/`, Next.js): login, leads (search, filters, new lead, CSV import and export), lead detail with live chat, takeover, pause AI, assign, edit, draft approval; approvals (AI drafts and price quotes); knowledge (documents, products and price rules, price check, test search); analytics with stuck leads and "send report now"; notifications; settings (business, follow-ups, report, webhook export, website chat embed and allowed websites, team invites). Every screen has loading, empty, error and no-permission states. Screens update by polling; a new Hot lead or handoff shows as a highlighted toast.
- Roles: owner (everything), admin (knowledge, settings, users; no lead screens), manager (leads, approvals, quotes, analytics, import/export), sales (only leads assigned to them; notes only). The backend enforces this; `tests/test_roles.py` checks every endpoint against the table.
- Webhook export: lead created and stage changed events, signed with HMAC (`X-Signature`), retried with backoff.
- Daily report email at the set time (India time) with counts, Hot leads and stuck leads; stuck leads also raise one alert per silence.
- Motion: AI orb (idle, thinking, paused, human), typing dots, grade badge transitions, page transitions, Hot toast. Everything that loops stops under reduced motion.
- `tests/test_e2e_scenarios.py` holds 10 scripted journeys (WhatsApp to Hot to handoff, silent form lead and follow-ups, web chat from knowledge, quote approval, discount handoff, email and unsubscribe, STOP, takeover drafts, CSV and webhook, roles and report).

## Company website
`website/` is a separate Next.js site: hero with a scripted demo chat, what you get, a scroll-driven "how it works", an enquiry form, FAQ, privacy page, and the real chat bubble loaded from the API. The form posts to the site's own `/api/enquiry`, which checks it (consent, honeypot, 5 per 10 minutes per address) and forwards it to `/api/v1/public/leads` with the API key kept on the server. All wording lives in `website/lib/site.config.ts` (placeholders for now).
```
cd website && npm install && cp .env.example .env.local && npm run dev   # http://localhost:3100
npm run lint && npm test && npm run build
```

## Day 2 behaviour in short
- Grading: the AI calls `set_grade`. Hot is accepted only when the stored data supports it (budget and timeline both saved, or the customer asked for a quote or a person); otherwise it is saved as warm. Hot alerts the assigned salesperson, or all owners and managers when nobody is assigned, on WhatsApp and in `/notifications`.
- Prices come only from `get_price` / price rules (fixed, per_unit, tiered, formula; formulas run in a restricted evaluator). The model cannot enter a price.
- Quotations are drafted with `draft_quotation`, held as `pending`, and sent only by `POST /quotations/{id}/approve` (owner, admin or manager).
- Every AI message passes guardrails (amounts, discounts, delivery promises, length, AI disclosure). One rewrite is allowed; a second failure sends nothing and opens a handoff.
- Follow-ups at day 1, 3 and 7 after our last message, only 09:00-19:00 Asia/Kolkata (configurable), skipped after a reply, STOP, won/lost/booked, takeover, pause or an open handoff. The scheduler runs in the API process when `SCHEDULER_ENABLED=true`.
- Takeover: `POST /leads/{id}/takeover` makes the AI write drafts (`pending_approval`) that a person approves, edits or rejects at `/messages/{id}/approve|reject`.
- Email: sends through Resend (assumed provider, swap in `app/channels/email.py`), inbound at `POST /webhooks/email/inbound` (normalized JSON, HMAC `x-email-signature`), unsubscribe link in every email, bounce or complaint blocks email only.

## Known limits
- WhatsApp free-form messages only work inside the customer's 24-hour window. Follow-ups after that need approved templates (not built); a failed send is recorded on the message.
- Alerts to staff over WhatsApp have the same 24-hour rule; the dashboard notification is always saved.
- Knowledge search is keyword based; an embeddings provider is still to be chosen.
- Dashboard updates by polling every few seconds, not by push.
- Sign-in tokens are verified with the Supabase legacy JWT secret (HS256). Projects that only use asymmetric signing keys need a small change.
- Webhook export is lead created and stage changed only.

## Run locally
```
cd backend
pip install -r requirements.txt
cp .env.example .env            # fill in values
python scripts/generate_sql.py <table names> > ../supabase/migrations/000N_name.sql   # new tables only; never edit applied migrations
uvicorn app.main:app --reload
pytest
```
Apply `supabase/migrations/0001_init.sql`, `0002_day2.sql`, `0003_day3.sql`, in order, to the Supabase project (Singapore) with the SQL editor or the Supabase CLI. In production the API refuses to start with development secrets (see `.env.example`).

## Dashboard locally
```
cd dashboard
npm install
cp .env.example .env.local      # NEXT_PUBLIC_API_URL, and the Supabase values when you have them
npm run dev                     # http://localhost:3000
npm run lint && npm test && npm run build
```
Without Supabase values the login page asks for a developer token (a JWT signed with `SUPABASE_JWT_SECRET`, `sub` = a profile id). This only exists while `NEXT_PUBLIC_SUPABASE_URL` is empty.

## Deploy
`render.yaml` (API and scheduler) and `dashboard/vercel.json`. Step by step in `docs/DEPLOY.md`. Keep one API instance: the scheduler runs inside it.

## Test the agent end to end (needs real keys)
1. Set ANTHROPIC_API_KEY and the WhatsApp variables. Point Meta's webhook at `/api/v1/webhooks/whatsapp` with your verify token.
2. Add your own number as a test recipient in the Meta app, then message the test number.
