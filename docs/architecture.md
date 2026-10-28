# Architecture

The original static landing lives in `product/public/bg/live`, the multi-screen waitlist in `product/public/bg/waitlist`, and the FastAPI handlers and Supabase REST client in `product/backend`. The owner database and original score migrations are included. Geocoding uses the original Mapbox proxy and its atomic Postgres usage cap when an owner configures it.

`app.py` mounts the original frontend and API at the selected path. Guest mode substitutes a per-browser in-memory implementation of the storage calls, keeping the original signup, answer and ranking handlers. It uses an HttpOnly Secure SameSite Strict cookie, same-origin mutation checks, an hour-long inactivity limit and a bounded number of sessions. Visitor entries are private to that session and volatile. The guest process refuses outbound email, geocoding and AI curation. It has no payment implementation.

One worker is required for the in-memory guest store. A production implementation that stores personal information needs a private database, a reviewed retention policy, provider contracts, correct legal notices, rate limiting and an HTTPS proxy.
