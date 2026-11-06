"""Safaricom sandbox STK bridge; test transactions cannot settle real hotel bookings."""
import os, secrets, hashlib, hmac
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import httpx, stk
from app import admin, origin, conn, sql, PUBLIC_URL, SECRET, moment

router = APIRouter()

class Begin(BaseModel):
    booking_id: str
    phone: str
    amount: int
    customer_consented: bool

class GatewayBegin(BaseModel):
    phone: str
    amount: int

class Check(BaseModel):
    attempt_id: str

def init():
    with conn() as db:
        sql(db, """CREATE TABLE IF NOT EXISTS sandbox_gateway_attempts (
          id TEXT PRIMARY KEY, phone TEXT NOT NULL, amount INTEGER NOT NULL,
          checkout_id TEXT UNIQUE, status TEXT NOT NULL, result_code INTEGER,
          receipt TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
init()

def configured_bridge():
    return bool(os.getenv("SANDBOX_BRIDGE_SECRET") and os.getenv("SANDBOX_BRIDGE_URL"))

def gateway_auth(request):
    key=os.getenv("SANDBOX_BRIDGE_SECRET","")
    if not key or not secrets.compare_digest(key,request.headers.get("X-Sandbox-Bridge","")):
        raise HTTPException(403,"Unauthorized sandbox gateway.")
    if os.getenv("APP_ENV")=="production" or os.getenv("DARAJA_ENV")!="sandbox" or os.getenv("DARAJA_ENABLED")!="true" or not stk.configured():
        raise HTTPException(503,"Sandbox gateway is inactive.")

def callback_key():
    return hmac.new(SECRET.encode(),b"santorini-sandbox-gateway",hashlib.sha256).hexdigest()

def gateway_call(endpoint,payload):
    base=os.getenv("SANDBOX_BRIDGE_URL","").rstrip("/")
    key=os.getenv("SANDBOX_BRIDGE_SECRET","")
    if not base.startswith("https://") or not key:
        raise HTTPException(503,"Sandbox STK bridge is not configured.")
    try:
        with httpx.Client(timeout=35.0) as client:
            response=client.post(base+endpoint,json=payload,headers={"X-Sandbox-Bridge":key})
            data=response.json()
        if response.status_code>=400:
            raise HTTPException(response.status_code,str(data.get("detail","Sandbox request rejected.")))
        return data
    except (httpx.HTTPError,ValueError) as exc:
        raise HTTPException(502,"Sandbox gateway temporarily unavailable.") from exc

@router.get("/api/admin/stk/sandbox/config")
def bridge_config(request:Request):
    admin(request)
    return {"enabled":configured_bridge()}

@router.post("/api/admin/stk/sandbox/request")
def bridge_begin(b:Begin,request:Request):
    admin(request);origin(request)
    if not b.customer_consented:
        raise HTTPException(422,"Customer agreement required.")
    with conn() as db:
        records=sql(db,"SELECT total,status,pay_status FROM bookings WHERE id=?",(b.booking_id,),read=True)
    if not records or records[0]["status"] in ("cancelled","rejected") or records[0]["pay_status"]=="verified":
        raise HTTPException(409,"Choose an eligible unpaid booking.")
    if not 1<=b.amount<=int(records[0]["total"]):
        raise HTTPException(422,"Amount must be between KES 1 and booking total.")
    try:phone=stk.normalize_phone(b.phone)
    except ValueError as exc:raise HTTPException(422,str(exc))
    out=gateway_call("/api/internal/sandbox/start",{"phone":phone,"amount":b.amount})
    return {"status":"prompt_sent","attempt_id":out["attempt_id"],"amount":b.amount,
            "message":"Safaricom sandbox request sent. This is a test transaction, not payment received by the hotel."}

@router.post("/api/admin/stk/sandbox/status")
def bridge_status(b:Check,request:Request):
    admin(request);origin(request)
    if len(b.attempt_id)!=32 or any(ch not in "0123456789abcdef" for ch in b.attempt_id):
        raise HTTPException(422,"Invalid payment request reference.")
    return gateway_call("/api/internal/sandbox/status",{"attempt_id":b.attempt_id})

@router.post("/api/internal/sandbox/start")
def gateway_begin(b:GatewayBegin,request:Request):
    gateway_auth(request)
    if not 1<=b.amount<=250000:
        raise HTTPException(422,"Invalid amount.")
    try:phone=stk.normalize_phone(b.phone)
    except ValueError as exc:raise HTTPException(422,str(exc))
    if not PUBLIC_URL.startswith("https://"):
        raise HTTPException(503,"Secure public callback URL missing.")
    ident=secrets.token_hex(16)
    with conn() as db:
        sql(db,"INSERT INTO sandbox_gateway_attempts(id,phone,amount,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (ident,phone,b.amount,"initiating",moment(),moment()))
    try:
        pushed=stk.send_push(phone,b.amount,"NS"+ident[:10].upper(),PUBLIC_URL+"/api/internal/sandbox/callback/"+callback_key())
    except stk.DarajaError as exc:
        with conn() as db:
            sql(db,"UPDATE sandbox_gateway_attempts SET status='unknown',updated_at=? WHERE id=?",(moment(),ident))
        raise HTTPException(502,str(exc))
    with conn() as db:
        sql(db,"UPDATE sandbox_gateway_attempts SET status='sent',checkout_id=?,updated_at=? WHERE id=?",
            (pushed["checkout_id"],moment(),ident))
    return {"attempt_id":ident,"status":"prompt_sent"}

@router.post("/api/internal/sandbox/callback/{key}")
async def gateway_callback(key:str,request:Request):
    if not secrets.compare_digest(key,callback_key()):
        raise HTTPException(404,"Not found")
    try:
        body=await request.json()
        data=body["Body"]["stkCallback"]
        checkout=str(data["CheckoutRequestID"])
        result=int(data["ResultCode"])
    except (KeyError,TypeError,ValueError):
        raise HTTPException(400,"Invalid callback.")
    with conn() as db:
        rows=sql(db,"SELECT * FROM sandbox_gateway_attempts WHERE checkout_id=?",(checkout,),read=True)
        if not rows:return {"ResultCode":0,"ResultDesc":"Acknowledged"}
        row=rows[0]
        if row["status"] in ("verified","failed","mismatch"):
            return {"ResultCode":0,"ResultDesc":"Acknowledged"}
        if result:
            sql(db,"UPDATE sandbox_gateway_attempts SET status='failed',result_code=?,updated_at=? WHERE id=?",(result,moment(),row["id"]))
        else:
            metadata={x.get("Name"):x.get("Value") for x in data.get("CallbackMetadata",{}).get("Item",[]) if isinstance(x,dict)}
            try:
                valid=(float(metadata["Amount"])==row["amount"] and
                       stk.normalize_phone(metadata["PhoneNumber"])==row["phone"] and
                       bool(metadata["MpesaReceiptNumber"]))
                receipt=str(metadata["MpesaReceiptNumber"]) if valid else None
            except (ValueError,TypeError,KeyError):
                valid=False;receipt=None
            sql(db,"UPDATE sandbox_gateway_attempts SET status=?,result_code=?,receipt=?,updated_at=? WHERE id=?",
                ("callback_success" if valid else "mismatch",result,receipt,moment(),row["id"]))
    return {"ResultCode":0,"ResultDesc":"Acknowledged"}

@router.post("/api/internal/sandbox/status")
def gateway_status(b:Check,request:Request):
    gateway_auth(request)
    with conn() as db:
        records=sql(db,"SELECT * FROM sandbox_gateway_attempts WHERE id=?",(b.attempt_id,),read=True)
    if not records:raise HTTPException(404,"Sandbox request not found; temporary storage may have reset.")
    record=records[0]
    if record["status"] not in ("verified","failed","mismatch") and record["checkout_id"]:
        try: result=stk.query(record["checkout_id"])
        except stk.DarajaError: result=None
        if result is not None:
            with conn() as db:
                if result!=0:
                    sql(db,"UPDATE sandbox_gateway_attempts SET status='failed',result_code=?,updated_at=? WHERE id=?",(result,moment(),record["id"]))
                elif record["status"]=="callback_success" and record["receipt"]:
                    sql(db,"UPDATE sandbox_gateway_attempts SET status='verified',result_code=0,updated_at=? WHERE id=?",(moment(),record["id"]))
            with conn() as db:
                record=sql(db,"SELECT * FROM sandbox_gateway_attempts WHERE id=?",(b.attempt_id,),read=True)[0]
    return {"stk_status":record["status"],"result_code":record["result_code"],
            "verified_test_amount":record["amount"] if record["status"]=="verified" else 0,
            "sandbox":True,
            "message":"Sandbox test only. No real hotel booking has been paid or confirmed."}
