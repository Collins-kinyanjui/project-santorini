"""Safaricom Daraja M-Pesa Express transport and configuration.

No external calls occur unless all merchant credentials are configured.
Credentials never leave this module; the Till is not a substitute for a store shortcode.
"""
import base64
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

TILL = "3340030"


class DarajaError(Exception):
    pass


def configured():
    required = ("DARAJA_CONSUMER_KEY", "DARAJA_CONSUMER_SECRET",
                "DARAJA_PASSKEY", "DARAJA_STORE_SHORTCODE")
    return all(os.environ.get(k, "").strip() for k in required)


def configuration():
    if not configured():
        raise DarajaError("STK Push is not configured. Set Daraja credentials and the approved store shortcode in Render.")
    mode = os.getenv("DARAJA_ENV", "sandbox").lower()
    if mode not in ("sandbox", "production"):
        raise DarajaError("Invalid DARAJA_ENV; use sandbox or production.")
    store = os.environ["DARAJA_STORE_SHORTCODE"].strip()
    if not re.fullmatch(r"[0-9]{5,10}", store):
        raise DarajaError("DARAJA_STORE_SHORTCODE must be the shortcode issued for M-Pesa Express.")
    return mode, store


def normalize_phone(raw):
    p = re.sub(r"[\s\-()+]", "", str(raw))
    if p.startswith("0") and len(p) == 10:
        p = "254" + p[1:]
    elif p.startswith("7") or p.startswith("1"):
        if len(p) == 9:
            p = "254" + p
    if not re.fullmatch(r"254[71][0-9]{8}", p):
        raise ValueError("Enter a Kenyan Safaricom number such as 0712345678 or 254712345678.")
    return p


def base():
    mode, _ = configuration()
    return "https://api.safaricom.co.ke" if mode == "production" else "https://sandbox.safaricom.co.ke"


def token():
    auth = (os.environ["DARAJA_CONSUMER_KEY"], os.environ["DARAJA_CONSUMER_SECRET"])
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(base() + "/oauth/v1/generate?grant_type=client_credentials", auth=auth)
            r.raise_for_status()
            value = r.json().get("access_token")
            if not value:
                raise DarajaError("No OAuth access token was returned.")
            return value
    except (httpx.HTTPError, ValueError) as exc:
        raise DarajaError("Safaricom authentication failed. Check your Daraja credentials.") from exc


def send_push(phone, amount, booking_reference, callback_url):
    mode, store = configuration()
    if not isinstance(amount, int) or not 1 <= amount <= 250000:
        raise DarajaError("M-Pesa Express amount must be between KES 1 and 250,000.")
    phone = normalize_phone(phone)
    if not callback_url.startswith("https://"):
        raise DarajaError("A public HTTPS callback URL is required.")
    stamp = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d%H%M%S")
    password = base64.b64encode((store + os.environ["DARAJA_PASSKEY"] + stamp).encode()).decode()
    payload = {
        "BusinessShortCode": store,
        "Password": password,
        "Timestamp": stamp,
        "TransactionType": "CustomerBuyGoodsOnline",
        "Amount": amount,
        "PartyA": phone,
        "PartyB": TILL,
        "PhoneNumber": phone,
        "CallBackURL": callback_url,
        "AccountReference": booking_reference.replace("-", "")[:12],
        "TransactionDesc": "Suite booking",
    }
    try:
        with httpx.Client(timeout=25.0) as client:
            r = client.post(base() + "/mpesa/stkpush/v1/processrequest",
                            headers={"Authorization": "Bearer " + token()}, json=payload)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DarajaError("The payment prompt could not be requested from Safaricom. Check Daraja configuration or network.") from exc
    if str(data.get("ResponseCode", "")) != "0" or not data.get("CheckoutRequestID"):
        raise DarajaError(str(data.get("CustomerMessage") or data.get("errorMessage") or "Safaricom rejected the STK request.")[:250])
    return {"checkout_id": str(data["CheckoutRequestID"]), "merchant_request_id": str(data.get("MerchantRequestID", ""))}


def query(checkout_id):
    _, store = configuration()
    stamp = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d%H%M%S")
    pwd = base64.b64encode((store + os.environ["DARAJA_PASSKEY"] + stamp).encode()).decode()
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.post(base() + "/mpesa/stkpushquery/v1/query",
                            headers={"Authorization": "Bearer " + token()},
                            json={"BusinessShortCode": store, "Password": pwd,
                                  "Timestamp": stamp, "CheckoutRequestID": checkout_id})
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DarajaError("Safaricom status query is temporarily unavailable.") from exc
    if str(data.get("ResponseCode", "")) != "0":
        raise DarajaError(str(data.get("errorMessage") or data.get("ResponseDescription") or "Payment status not ready.")[:250])
    if "ResultCode" not in data:
        raise DarajaError("Safaricom has not yet reported the final payment status.")
    return int(data["ResultCode"])
