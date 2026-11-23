# Deploy guide

Do this after development is finished. Order matters: database, API, dashboard, then the channels.
Secrets are typed only into the hosting dashboards (Render, Vercel, Supabase). Never commit them and never paste them into chat.

## 1. Supabase (Singapore)
1. Create the project in the Singapore region.
2. SQL editor: run `supabase/migrations/0001_init.sql`, `0002_day2.sql`, `0003_day3.sql` in that order.
3. Authentication > Providers: keep Email on. Turn **off** public sign-ups (Authentication > Sign In / Providers > "Allow new users to sign up"), because people join only by invitation.
4. Note these values: Project URL, anon key, service role key, JWT secret, and the pooled connection string (Database > Connect > Transaction pooler).
5. Create the first owner: Authentication > Users > Add user (email + password). Then in the SQL editor:
   ```sql
   insert into profiles (id, email, full_name, role, whatsapp_alerts_enabled, active, created_at, updated_at)
   select id, email, 'Your name', 'owner', true, true, now(), now() from auth.users where email = 'you@example.com';
   ```
6. **Token signing:** new Supabase projects sign sign-in tokens with an asymmetric key (Project Settings > JWT Keys > "JWT Signing Keys"). The API checks them against the project's public keys, found automatically from `SUPABASE_URL`. You do not need the legacy secret. Set `SUPABASE_JWT_SECRET` only if your project still signs with it.

## 2. Render (API and scheduler)
1. New > Blueprint, pick the repo. Render reads `render.yaml`.
2. Fill every `sync: false` value (DATABASE_URL, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_JWT_SECRET (optional), ANTHROPIC_API_KEY, WHATSAPP_*, EMAIL_*, API_BASE_URL, DASHBOARD_URL, CORS_ORIGINS).
   DATABASE_URL is the pooled string from step 1.4. The API refuses to start in production with weak or missing secrets and says which ones.
3. Deploy. Open `https://<service>.onrender.com/health`. It must answer ok.
4. Keep exactly one instance. The follow-up and report scheduler runs inside the API process, so a second instance would send duplicates.

## 3. Vercel (dashboard)
1. New project, root directory `dashboard`.
2. Environment variables: `NEXT_PUBLIC_API_URL` (the Render URL), `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`. The anon key is public by design; the service role key must never be set here.
3. Deploy, then set `DASHBOARD_URL` and `CORS_ORIGINS` on Render to the Vercel URL and redeploy the API.

## 4. Channels
- **WhatsApp:** Meta app > WhatsApp > Configuration. Callback URL `https://<render>/api/v1/webhooks/whatsapp`, verify token = `WHATSAPP_VERIFY_TOKEN` from Render. Subscribe to `messages`. Add your own number as a test recipient first.
- **Email:** point the provider's inbound webhook at `https://<render>/api/v1/webhooks/email/inbound` (and bounces at `/bounce`) with the same secret as `EMAIL_WEBHOOK_SECRET`. Verify the sending domain (SPF, DKIM) before sending to real customers.
- **Website form:** POST to `/api/v1/public/leads` with header `X-API-Key: <LEAD_WEBHOOK_API_KEY>`. Call it from your server, not from browser code, so the key stays secret.
- **Web chat:** Settings > Website chat shows the one-line script. Set the allowed websites there first.

## 5. First-day checks
1. Sign in to the dashboard as the owner. Settings > Team: invite a manager and a salesperson (with their WhatsApp numbers and alerts on).
2. Knowledge: add your FAQs. Products and prices: add products and rules, then use Price check.
3. Message the WhatsApp test number: give a budget and timeline. The lead goes Hot, the salesperson gets an alert, the dashboard shows a notification.
4. Analytics > "Send today's report now" and check the email.
5. Settings > webhook: Send test event, if you export leads to another system.

## Rolling back
Render keeps previous deploys (Deploys > Rollback). Migrations are forward-only; add a new numbered file instead of editing an applied one.

## 6. Company website (`website/`)
1. Edit `website/lib/site.config.ts`: company name, headline, offers, steps, FAQ, contact email. Have `app/privacy/page.tsx` reviewed before going live.
2. Vercel: new project, root directory `website`. Variables: `NEXT_PUBLIC_API_URL` (Render URL), `NEXT_PUBLIC_SITE_URL` (the site's own URL), and `LEAD_API_KEY` = the API's `LEAD_WEBHOOK_API_KEY`. `LEAD_API_KEY` is server-only; never prefix it with `NEXT_PUBLIC_`.
3. In the dashboard, Settings > Website chat > allowed websites: add the site's URL (for example `https://www.example.com`), or the chat bubble will be refused.
4. Put the same URL in Render's `CORS_ORIGINS` only if the site calls the API from the browser. It does not for the form (the form goes through the site's own server), but the chat bubble does.
