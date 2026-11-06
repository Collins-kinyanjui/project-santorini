"""STK Push API: booking-bound requests and independently reconciled results.

Daraja calls are unavailable until explicitly enabled with merchant credentials.
Callback alone NEVER marks a payment verified; it must also pass a Daraja status query.
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
import stk

from app import conn, sql, origin, admin, SECRET, PUBLIC_URL, moment

router = APIRouter()

def initialize():
    with conn() as db:
        sql(db, """CREATE TABLE IF NOT EXISTS stk_attempts (
            id TEXT PRIMARY KEY,
            booking_id TEXT NOT NULL,
            phone TEXT NOT NULL,
            amount INTEGER NOT NULL,
            checkout_id TEXT UNIQUE,
            merchant_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            callback_code INTEGER,
            callback_receipt TEXT,
            callback_amount INTEGER,
            callback_phone TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        sql(db, "CREATE INDEX IF NOT EXISTS stk_bookings_idx ON stk_attempts(booking_id,created_at)")
initialize()

def active():
    return (os.getenv("DARAJA_ENABLED", "").lower() == "true" and stk.configured()
            and (os.getenv("APP_ENV") != "production" or os.getenv("DARAJA_ENV") == "production"))

def callback_secret():
    return hmac.new(SECRET.encode(), b"santorini-daraja-stk-callback", hashlib.sha256).hexdigest()

def public_callback():
    if not PUBLIC_URL.startswith("https://"):
        raise HTTPException(503,"PUBLIC_URL must be a public HTTPS URL before STK can be enabled.")
    return PUBLIC_URL + "/api/mpesa/stk/callback/" + callback_secret()

def need_active():
    if not active():
        raise HTTPException(503, "Automated STK Push is not enabled yet. Please use the manual Till payment option.")

class GuestPush(BaseModel):
    booking_id: str
    cancel_code: str
    phone: str

class StaffPush(BaseModel):
    booking_id: str
    phone: str
    customer_consented: bool
    amount: int

class GuestStatus(BaseModel):
    booking_id: str
    cancel_code: str

class StaffStatus(BaseModel):
    booking_id: str

def booking_for_push(booking_id):
    with conn() as db:
        rows=sql(db,"SELECT id,secret,total,status,pay_status,pay_method FROM bookings WHERE id=?",(booking_id,),read=True)
    if not rows:
        raise HTTPException(404,"Booking not found")
    book=rows[0]
    if book["status"] in ("rejected","cancelled") or book["pay_status"]=="verified":
        raise HTTPException(409,"This reservation is no longer eligible for an STK payment.")
    return book

def start(booking_id, phone, requested_amount=None):
    need_active()
    try: phone=stk.normalize_phone(phone)
    except ValueError as exc: raise HTTPException(422,str(exc))
    book=booking_for_push(booking_id)
    with conn() as db:
        previous_paid=sql(db,"SELECT COALESCE(SUM(amount),0) AS paid FROM stk_attempts WHERE booking_id=? AND status='verified'",(booking_id,),read=True)[0]["paid"]
    remaining=int(book["total"])-int(previous_paid)
    if remaining<=0:raise HTTPException(409,"Booking paid in full.")
    if requested_amount is None:requested_amount=remaining
    if not isinstance(requested_amount,int) or isinstance(requested_amount,bool) or not 1<=requested_amount<=remaining:
        raise HTTPException(422,f"Amount must be between KES 1 and the outstanding balance of KES {remaining}.")
    now=datetime.now(timezone.utc)
    with conn() as db:
        # Use an application-wide advisory lock for concurrent STK requests in production.
        from app import pg
        if pg():sql(db,"SELECT pg_advisory_xact_lock(900102)")
        else:sql(db,"BEGIN IMMEDIATE")
        previous=sql(db,"SELECT id,created_at,status FROM stk_attempts WHERE booking_id=? ORDER BY created_at DESC LIMIT 1",(booking_id,),read=True)
        if previous:
            recent=now-datetime.fromisoformat(previous[0]["created_at"])
            if recent<timedelta(seconds=120):
                raise HTTPException(429,"A prompt was recently requested. Wait two minutes before trying again.")
            if previous[0]["status"] in ("sent","callback_success","unknown","initiating"):
                raise HTTPException(409,"A previous payment request is still unresolved. Refresh its status before retrying.")
        attempt_id=secrets.token_hex(16)
        sql(db,"INSERT INTO stk_attempts(id,booking_id,phone,amount,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (attempt_id,booking_id,phone,requested_amount,"initiating",moment(),moment()))
    try:
        data=stk.send_push(phone,requested_amount,booking_id,public_callback())
    except (stk.DarajaError,HTTPException) as exc:
        # A timeout can be ambiguous; never silently claim that the customer has not paid.
        with conn() as db:
            sql(db,"UPDATE stk_attempts SET status='unknown',updated_at=? WHERE id=?",(moment(),attempt_id))
        if isinstance(exc,HTTPException):raise
        raise HTTPException(502,str(exc))
    with conn() as db:
        sql(db,"UPDATE stk_attempts SET checkout_id=?,merchant_id=?,status='sent',updated_at=? WHERE id=?",
            (data["checkout_id"],data["merchant_request_id"],moment(),attempt_id))
    return {"request_id":attempt_id,"booking_id":booking_id,"amount":requested_amount,"remaining_after_success":remaining-requested_amount,
            "status":"prompt_sent","message":"STK prompt requested. A phone PIN approval is not proof of payment; await Safaricom confirmation."}

def read_attempt(booking_id):
    with conn() as db:
        records=sql(db,"SELECT id,checkout_id,status,callback_code,callback_receipt,callback_amount,callback_phone,phone,amount FROM stk_attempts WHERE booking_id=? ORDER BY created_at DESC LIMIT 1",(booking_id,),read=True)
    return records[0] if records else None

def reconcile(attempt):
    if not attempt or not attempt["checkout_id"] or attempt["status"] in ("verified","failed","mismatch"):
        return attempt
    try: query_result=stk.query(attempt["checkout_id"])
    except stk.DarajaError: return attempt
    with conn() as db:
        rows=sql(db,"SELECT * FROM stk_attempts WHERE id=?",(attempt["id"],),read=True)
        if not rows:return attempt
        item=rows[0]
        if item["status"] in ("verified","failed","mismatch"):return item
        if query_result != 0:
            sql(db,"UPDATE stk_attempts SET status='failed',updated_at=? WHERE id=?",(moment(),item["id"]))
        elif (item["callback_code"]==0 and item["callback_receipt"]
              and item["callback_amount"]==item["amount"]
              and item["callback_phone"]==item["phone"]):
            # Both a matching successful callback and a successful Safaricom query are required.
            booking=sql(db,"SELECT pay_status,status FROM bookings WHERE id=?",(item["booking_id"],),read=True)
            if booking and booking[0]["status"] not in ("rejected","cancelled"):
                receipt_used=sql(db,"SELECT id FROM bookings WHERE transaction_code=? AND id<>?",(item["callback_receipt"],item["booking_id"]),read=True)
                other_stk=sql(db,"SELECT id FROM stk_attempts WHERE callback_receipt=? AND id<>? AND status='verified'",(item["callback_receipt"],item["id"]),read=True)
                if not receipt_used and not other_stk:
                    sql(db,"UPDATE stk_attempts SET status='verified',updated_at=? WHERE id=?",(moment(),item["id"]))
                    # Only the full verified sum can settle the booking.
                    total=sql(db,"SELECT total FROM bookings WHERE id=?",(item["booking_id"],),read=True)[0]["total"]
                    paid=sql(db,"SELECT COALESCE(SUM(amount),0) AS paid FROM stk_attempts WHERE booking_id=? AND status='verified'",(item["booking_id"],),read=True)[0]["paid"]
                    if paid>=total:
                        sql(db,"UPDATE bookings SET pay_method='mpesa',pay_status='verified',status='confirmed',transaction_code=? WHERE id=?",
                            (item["callback_receipt"],item["booking_id"]))
                    else:
                        sql(db,"UPDATE bookings SET pay_method='mpesa',pay_status='partial' WHERE id=?",(item["booking_id"],))
                else:sql(db,"UPDATE stk_attempts SET status='mismatch',updated_at=? WHERE id=?",(moment(),item["id"]))
        else:
            sql(db,"UPDATE stk_attempts SET status='callback_success',updated_at=? WHERE id=?",(moment(),item["id"]))
    return read_attempt(item["booking_id"])

@router.get("/api/mpesa/stk/config")
def payment_config():
    # Public status only. Never return keys, passkeys, store shortcode or callback secret.
    return {"enabled":active(),"till_number":stk.TILL,"recipient":"Alex Waragu Kuriah","automatic_verification":active()}

@router.post("/api/bookings/stk/request")
def guest_start(b:GuestPush,request:Request):
    origin(request)
    book=booking_for_push(b.booking_id)
    if not secrets.compare_digest(book["secret"],b.cancel_code):
        raise HTTPException(404,"Booking credentials not found")
    if book["pay_method"]!="mpesa":
        raise HTTPException(409,"Choose M-Pesa at reservation to request a guest STK prompt.")
    return start(b.booking_id,b.phone)

@router.post("/api/admin/stk/request")
def staff_start(b:StaffPush,request:Request):
    admin(request);origin(request)
    if not b.customer_consented:
        raise HTTPException(422,"Confirm the customer has agreed to receive this payment request.")
    return start(b.booking_id,b.phone,b.amount)

@router.post("/api/bookings/stk/status")
def guest_payment_status(b:GuestStatus,request:Request):
    origin(request)
    book=booking_for_status(b.booking_id,b.cancel_code)
    attempt=read_attempt(b.booking_id)
    if attempt and active() and attempt["status"] not in ("verified","failed","mismatch"):
        attempt=reconcile(attempt)
    return {"booking_id":b.booking_id,"payment_status":book["pay_status"] if not attempt or attempt["status"]!="verified" else "verified",
            "stk_status":attempt["status"] if attempt else "not_requested"}

def booking_for_status(bid,code):
    with conn() as db:
        rows=sql(db,"SELECT secret,pay_status FROM bookings WHERE id=?",(bid,),read=True)
    if not rows or not secrets.compare_digest(rows[0]["secret"],code):
        raise HTTPException(404,"Booking credentials not found")
    return rows[0]

@router.post("/api/admin/stk/reconcile")
def staff_reconcile(b:StaffStatus,request:Request):
    admin(request);origin(request)
    attempt=read_attempt(b.booking_id)
    if not attempt:raise HTTPException(404,"No STK attempt for that booking.")
    attempt=reconcile(attempt) if active() else attempt
    return {"booking_id":b.booking_id,"stk_status":attempt["status"]}

@router.post("/api/mpesa/stk/callback/{key}")
async def safaricom_callback(key:str,request:Request):
    if not secrets.compare_digest(key,callback_secret()):
        raise HTTPException(404,"Not found")
    try:
        body=await request.json()
        data=body["Body"]["stkCallback"]
        checkout=data["CheckoutRequestID"]
        result=int(data["ResultCode"])
        if not isinstance(checkout,str) or len(checkout)>150:raise ValueError("invalid checkout")
    except (KeyError,TypeError,ValueError):
        raise HTTPException(400,"Invalid callback format")
    with conn() as db:
        attempts=sql(db,"SELECT * FROM stk_attempts WHERE checkout_id=?",(checkout,),read=True)
        if not attempts:return {"ResultCode":0,"ResultDesc":"Acknowledged"}
        attempt=attempts[0]
        if attempt["status"] in ("verified","failed","mismatch"):
            return {"ResultCode":0,"ResultDesc":"Acknowledged"}
        if result!=0:
            sql(db,"UPDATE stk_attempts SET status='failed',callback_code=?,updated_at=? WHERE id=?",(result,moment(),attempt["id"]))
        else:
            metadata={}
            for pair in data.get("CallbackMetadata",{}).get("Item",[]):
                if isinstance(pair,dict) and "Name" in pair:
                    metadata[pair["Name"]]=pair.get("Value")
            try:
                receipt=str(metadata["MpesaReceiptNumber"])
                amount=int(float(metadata["Amount"]))
                phone=stk.normalize_phone(str(metadata["PhoneNumber"]))
                valid=bool(receipt) and float(metadata["Amount"])==attempt["amount"] and phone==attempt["phone"]
            except (KeyError,ValueError,TypeError):
                valid=False
                receipt="";amount=None;phone=""
            status="callback_success" if valid else "mismatch"
            sql(db,"UPDATE stk_attempts SET callback_code=?,callback_receipt=?,callback_amount=?,callback_phone=?,status=?,updated_at=? WHERE id=?",
                (result,receipt,amount,phone,status,moment(),attempt["id"]))
    if result==0 and valid and active():
        reconcile(read_attempt(attempt["booking_id"]))
    return {"ResultCode":0,"ResultDesc":"Acknowledged"}
