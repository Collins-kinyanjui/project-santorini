# New Santorini Suites
An independent two-page hotel website with 20 rooms, admin management, and manual M-Pesa confirmation.

## Guest experience
- Landing page: elegant cream, blue and warm gold glass UI with moving hero, animated gallery, room previews and amenities.
- Booking page: availability, date/room selection, reserve and pay on arrival or manually by M-Pesa.
- Manual M-Pesa recipient: Collins Kinyanjui Thuo; +254 740 686 453. Submitting a transaction reference does **not** confirm payment.
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
