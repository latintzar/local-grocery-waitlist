# Local grocery waitlist

Local grocery waitlist is a landing page and multi-step signup application for a small-producer grocery service. It preserves Vitrina’s original bilingual landing, food imagery, phone-first signup, address entry, taste quiz, position page and referral flow.

The guest mode runs the original FastAPI waitlist handlers with a private, short-lived in-memory store per browser session. Entries expire after one hour of inactivity and disappear on restart. Use sample details. Email, external geocoding and community AI are disabled in guest mode; the original form accepts an address as free text. The guest store is for exploring the app, and does not represent a shared waitlist.

## Run

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
VITRINA_GUEST=1 .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000/. For a mounted deployment set `VITRINA_BASE_PATH`, and set `VITRINA_PUBLIC_BASE_URL` to that same public application URL. The HTTPS guest cookie uses that path and is HttpOnly and SameSite Strict. Use a trusted HTTPS proxy and retain its Host header. The server does not log request bodies.

## Use your own database

The native server can use a recipient-owned Supabase project with the SQL migrations in `product/backend/api/migrations`. Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` privately and leave `VITRINA_GUEST` unset. Configure your own Mapbox, Resend and Anthropic accounts only for features you choose to operate. The migrations, provider credentials, privacy notice, contact destinations and rate limits must be reviewed for your deployment. Never place a service-role key in frontend code. The sample configuration contains blank keys.

The interface is retained as a historical product example. It includes original service copy and historical legal text; replace them with your own actual service terms before collecting registrations. The original company data, customer records and operator configuration are not included.

Run `python tools.py configure --env-file /private/path.env` to enter your own provider credentials through hidden input. `python tools.py doctor --env-file /private/path.env` checks configuration without provider calls; `python tools.py serve --env-file /private/path.env` starts the server.

Run `python -m pytest -q` for isolated guest signup, quiz and access-boundary tests. See [architecture](docs/architecture.md).
