# New Santorini Suites
An independent two-page hotel website with 20 rooms, admin management, and manual M-Pesa confirmation.

## Guest experience
- Landing page: elegant cream, blue and warm gold glass UI with moving hero, animated gallery, room previews and amenities.
- Booking page: availability, date/room selection, reserve and pay on arrival or manually by M-Pesa.
- Manual M-Pesa Buy Goods Till: 3340030; registered recipient: Alex Waragu Kuriah (different from the New Santorini Suites trading name). Submitting a transaction reference does **not** confirm payment.
- 12 single rooms (KES 1,500/night), 5 doubles (KES 2,000/night) and 3 deluxe suites (KES 2,500/night).

## Render deployment
The application requires a persistent PostgreSQL database via `DATABASE_URL`. Do not run a production hotel booking system on an ephemeral SQLite database.
Build: `pip install -r requirements.txt`
Start: `uvicorn app:app --host 0.0.0.0 --port $PORT`
Private environment: `DATABASE_URL`, `ADMIN_PASSWORD`, `SESSION_SECRET`; public setting: `PUBLIC_URL=https://<your-domain>`, `APP_ENV=production`.
Do not share admin passwords or database credentials in GitHub.

## Limitations
- M-Pesa is manual. Admin must independently verify the real payment in their own account before approving it as paid.
- Card gateway and M-Pesa STK Push are not implemented.
- Confirmation shown in browser is a booking acknowledgment, not a fiscal receipt or proof of payment.
- Images are design placeholders pending real property photos.
- Add booking rules (check-in/out times, cancellation policy), email notices, backups, monitoring, and a verified map pin before production sales.

## M-Pesa Express / STK Push (optional, disabled by default)

The payment UI offers manual Buy Goods payments to **Till 3340030**, registered under **Alex Waragu Kuriah**. The separate automatic STK payment feature is staged but intentionally **disabled** until Safaricom approves merchant API access.

To enable safely:

1. Confirm the merchant has Safaricom Daraja production **M-Pesa Express** approval for Buy Goods Till **3340030**. Obtain the associated **store/head-office shortcode**, which may differ from the Till. A Till alone cannot authenticate M-Pesa Express.
2. Obtain the production Daraja consumer key, consumer secret, and **Lipa na M-Pesa Online passkey** from Safaricom for that merchant. Do not paste or commit them to source code.
3. In **Render → new-santorini-suites → Environment**, add these private variables:
   - `DARAJA_CONSUMER_KEY`
   - `DARAJA_CONSUMER_SECRET`
   - `DARAJA_PASSKEY`
   - `DARAJA_STORE_SHORTCODE` (approved HO/store shortcode)
   - `DARAJA_ENV=production`
   - `DARAJA_ENABLED=true` **only after** credentials and store/till association have been checked.
4. Keep `PUBLIC_URL=https://new-santorini-suites.onrender.com` and `SESSION_SECRET` stable. The callback path derives its secret from `SESSION_SECRET`; changing it invalidates pending callbacks. The STK callback URL is supplied automatically with each request; no secret should appear in the browser.
5. Make a controlled payment with the merchant's authorization using a real Safaricom phone number. Verify funds land in the designated Till and that the booking changes to **confirmed / verified**. Confirm negative responses, wrong amount, wrong phone, duplicates, delayed callbacks, and admin push consent.
6. Check staff dashboard for **Request M-Pesa STK Push**: select an unpaid reservation, enter the customer's phone, confirm permission, and send. The amount comes from the booking and cannot be changed in the UI. Requests are rate limited.
7. When an STK push is accepted, **it is not proof of payment**. A successful callback matching the expected amount, phone and checkout ID, **plus a successful Safaricom STK status query**, is required before the system automatically marks the reservation as paid. Missing callbacks or query failures remain unverified for manual investigation; the status can be rechecked via the guest or admin interface. Manual payment and pay-on-arrival remain available.

Security notes: merchant credentials are server-only, not embedded in the frontend; the callback URL includes a non-public token; duplicate receipt identifiers are rejected for automatic confirmation. A cancelled or rejected reservation cannot be charged via the integrated flow. Requests made during ambiguous network timeouts are kept unresolved to avoid accidental duplicate billing.

**Production handover blocker:** the free Render Postgres database expires on 2026-12-06; move to durable database hosting and backups before relying on this service for actual customer payments and reservations.
