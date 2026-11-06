"""New Santorini Suites - independent booking application."""
import os, secrets, csv, io, sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Literal
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import BaseModel, EmailStr, Field

ROOT=Path(__file__).resolve().parent
DATABASE_URL=os.getenv("DATABASE_URL","sqlite:///./santorini.db")
PROD=os.getenv("APP_ENV")=="production"
ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD","")
SECRET=os.getenv("SESSION_SECRET","") or secrets.token_urlsafe(48)
PUBLIC_URL=os.getenv("PUBLIC_URL","").rstrip("/")
if PROD and (not ADMIN_PASSWORD or not os.getenv("SESSION_SECRET") or not DATABASE_URL.startswith(("postgresql://","postgres://"))):
    raise RuntimeError("Production needs ADMIN_PASSWORD, SESSION_SECRET and a PostgreSQL DATABASE_URL")
cookie=URLSafeTimedSerializer(SECRET,salt="santorini-admin")
app=FastAPI(title="New Santorini Suites",docs_url=None,redoc_url=None)
TYPES={"single":("Single Room",12,1500,1),"double":("Double Room",5,2000,2),"deluxe":("Deluxe Suite",3,2500,4)}

def pg(): return DATABASE_URL.startswith(("postgresql://","postgres://"))
@contextmanager
def conn():
    if pg():
        import psycopg2
        from psycopg2.extras import RealDictCursor
        db=psycopg2.connect(DATABASE_URL,connect_timeout=12,sslmode="require",cursor_factory=RealDictCursor)
    else:
        path=DATABASE_URL.removeprefix("sqlite:///")
        db=sqlite3.connect(path,timeout=15)
        db.row_factory=sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally: db.close()
def sql(db,statement,params=(),read=False):
    cur=db.cursor()
    cur.execute(statement.replace("?","%s") if pg() else statement,params)
    data=[dict(x) for x in cur.fetchall()] if read else None
    cur.close()
    return data
def init():
    with conn() as c:
        sql(c,"CREATE TABLE IF NOT EXISTS rooms (id INTEGER PRIMARY KEY, category TEXT NOT NULL, price INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1)")
        sql(c,"""CREATE TABLE IF NOT EXISTS bookings (id TEXT PRIMARY KEY, secret TEXT NOT NULL, room_id INTEGER NOT NULL, guest_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL, checkin TEXT NOT NULL, checkout TEXT NOT NULL, guests INTEGER NOT NULL, requests TEXT NOT NULL, pay_method TEXT NOT NULL, pay_status TEXT NOT NULL DEFAULT 'unpaid', transaction_code TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', total INTEGER NOT NULL, created_at TEXT NOT NULL)""")
        sql(c,"CREATE INDEX IF NOT EXISTS booking_dates_idx ON bookings(room_id,checkin,checkout,status)")
        sql(c,"CREATE TABLE IF NOT EXISTS inquiries (id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL)")
        if not sql(c,"SELECT id FROM rooms LIMIT 1",read=True):
            n=0
            for k,(label,count,price,capacity) in TYPES.items():
                for i in range(count):
                    n+=1
                    sql(c,"INSERT INTO rooms(id,category,price,active) VALUES(?,?,?,1)",(n,k,price))
init()
def moment():return datetime.now(timezone.utc).isoformat(timespec="seconds")
def nights(a,b):
    try: start,end=date.fromisoformat(a),date.fromisoformat(b)
    except ValueError:raise HTTPException(422,"Invalid dates")
    days=(end-start).days
    if start<date.today() or days<1 or days>30:raise HTTPException(422,"Choose 1-30 nights starting today or later")
    return days
def origin(req):
    value=req.headers.get("origin","").rstrip("/")
    if value and value not in {PUBLIC_URL,str(req.base_url).rstrip("/")}:
        raise HTTPException(403,"Invalid request origin")
def admin(req):
    try:
        if cookie.loads(req.cookies.get("santorini_admin",""),max_age=28800)=="staff":return
    except (BadSignature,SignatureExpired):pass
    raise HTTPException(401,"Sign in required")
class Booking(BaseModel):
    category:Literal["single","double","deluxe"]
    guest_name:str=Field(min_length=2,max_length=100)
    email:EmailStr
    phone:str=Field(min_length=8,max_length=25)
    checkin:str
    checkout:str
    guests:int=Field(ge=1,le=4)
    requests:str=Field(default="",max_length=700)
    payment_method:Literal["arrival","mpesa","card"]
class Code(BaseModel):
    booking_id:str
    cancel_code:str
    transaction_code:str=Field(min_length=7,max_length=25,pattern="^[A-Za-z0-9]+$")
class Cancel(BaseModel):
    booking_id:str
    cancel_code:str
class Login(BaseModel):
    password:str
class Action(BaseModel):
    status:Literal["confirmed","rejected"]
class Verify(BaseModel):
    payment_status:Literal["verified","rejected"]
class Price(BaseModel):
    price:int=Field(ge=100,le=100000)
class RoomActive(BaseModel):
    active:bool
class Inquiry(BaseModel):
    name:str=Field(min_length=2,max_length=100)
    email:EmailStr
    message:str=Field(min_length=5,max_length=2000)
@app.get("/",response_class=HTMLResponse)
def home():return (ROOT/"index.html").read_text(encoding="utf8")
@app.get("/booking",response_class=HTMLResponse)
def booking_page():return (ROOT/"booking.html").read_text(encoding="utf8")
@app.get("/admin",response_class=HTMLResponse)
def admin_page():return (ROOT/"admin.html").read_text(encoding="utf8")
@app.get("/{filename}")
def assets(filename:str):
    if filename not in {"styles.css","site.js","booking.css","booking.js","admin.js"}:raise HTTPException(404,"Not found")
    return FileResponse(ROOT/filename)
@app.get("/api/health")
def health():return {"ok":True,"rooms":20,"manual_mpesa":True}
@app.get("/api/mpesa/instructions")
def mpesa():return {"payment_type":"buy_goods","recipient":"Alex Waragu Kuriah","till_number":"3340030","automatic_verification":False}
@app.get("/api/rooms")
def rooms():
    with conn() as c:
        rows=sql(c,"SELECT category,COUNT(*) AS total,MIN(price) AS price FROM rooms WHERE active=1 GROUP BY category",read=True)
    data={r["category"]:r for r in rows}
    return [{"category":k,"label":v[0],"active_count":data.get(k,{}).get("total",0),"price":data.get(k,{}).get("price",v[2])} for k,v in TYPES.items()]
@app.get("/api/availability")
def availability(checkin:str,checkout:str):
    days=nights(checkin,checkout)
    with conn() as c:
        rows=sql(c,"""SELECT r.category,COUNT(*) AS qty,MIN(r.price) AS price FROM rooms r WHERE r.active=1 AND NOT EXISTS(SELECT 1 FROM bookings b WHERE b.room_id=r.id AND b.status IN ('pending','confirmed') AND b.checkin < ? AND b.checkout > ?) GROUP BY r.category""",(checkout,checkin),read=True)
    found={r["category"]:r for r in rows}
    return {"nights":days,"categories":[{"category":k,"label":v[0],"available":found.get(k,{}).get("qty",0),"price":found.get(k,{}).get("price",v[2])} for k,v in TYPES.items()]}
@app.post("/api/bookings",status_code=201)
def reserve(b:Booking,request:Request):
    origin(request)
    days=nights(b.checkin,b.checkout)
    if b.guests>TYPES[b.category][3]:raise HTTPException(422,"Guest count exceeds room capacity")
    if b.payment_method=="card":raise HTTPException(422,"Card processing is not yet available")
    with conn() as c:
        if pg(): sql(c,"SELECT pg_advisory_xact_lock(900101)")
        else: sql(c,"BEGIN IMMEDIATE")
        matches=sql(c,"""SELECT r.id,r.price FROM rooms r WHERE r.active=1 AND r.category=? AND NOT EXISTS(SELECT 1 FROM bookings b WHERE b.room_id=r.id AND b.status IN ('pending','confirmed') AND b.checkin < ? AND b.checkout > ?) ORDER BY r.id LIMIT 1""",(b.category,b.checkout,b.checkin),read=True)
        if not matches:raise HTTPException(409,"No rooms available for these dates")
        selected=matches[0];bid="NS-"+secrets.token_hex(4).upper();private=secrets.token_urlsafe(18)
        total=int(selected["price"])*days
        sql(c,"""INSERT INTO bookings(id,secret,room_id,guest_name,email,phone,checkin,checkout,guests,requests,pay_method,total,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(bid,private,selected["id"],b.guest_name.strip(),str(b.email),b.phone.strip(),b.checkin,b.checkout,b.guests,b.requests.strip(),b.payment_method,total,moment()))
    return {"booking_id":bid,"cancel_code":private,"status":"pending","payment_status":"unpaid","payment_method":b.payment_method,"total":total,"currency":"KES","nights":days,"note":"Pending property approval. This is a booking acknowledgment, not a tax receipt."}

class BookingIdentity(BaseModel):
    booking_id:str
    cancel_code:str

def guest_booking(b:BookingIdentity):
    with conn() as db:
        rows=sql(db,"SELECT id,secret,room_id,guest_name,checkin,checkout,guests,pay_method,pay_status,status,total FROM bookings WHERE id=?",(b.booking_id,),read=True)
    if not rows or not secrets.compare_digest(rows[0]["secret"],b.cancel_code):
        raise HTTPException(404,"Booking reference or private code not found")
    return rows[0]

def acknowledgement_allowed(record):
    return record["status"] not in ("rejected","cancelled") and record["pay_status"]!="rejected"

@app.post("/api/bookings/status")
def booking_status(b:BookingIdentity,request:Request):
    origin(request)
    record=guest_booking(b)
    return {"booking_id":record["id"],"status":record["status"],"payment_status":record["pay_status"],"print_allowed":acknowledgement_allowed(record)}

@app.post("/api/bookings/acknowledgment",response_class=HTMLResponse)
def booking_acknowledgment(b:BookingIdentity,request:Request):
    origin(request)
    record=guest_booking(b)
    if not acknowledgement_allowed(record):
        raise HTTPException(403,"This reservation was cancelled or rejected. Acknowledgment printing is disabled.")
    from html import escape
    details=[("Booking reference",record["id"]),("Guest",record["guest_name"]),
        ("Room number",record["room_id"]),("Check-in",record["checkin"]),
        ("Check-out",record["checkout"]),("Guests",record["guests"]),
        ("Amount",f"KES {record['total']:,}"),("Payment method",record["pay_method"]),
        ("Booking status",record["status"]),("Payment status",record["pay_status"])]
    rows="".join("<tr><th>"+escape(str(k))+"</th><td>"+escape(str(v))+"</td></tr>" for k,v in details)
    html="<html><head><meta charset='utf-8'><title>Booking acknowledgment</title><style>body{font:16px Arial,sans-serif;max-width:720px;margin:50px auto;padding:20px;color:#172d42}h1{font:36px Georgia,serif}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:12px;border-bottom:1px solid #ddd}p{line-height:1.7}.note{border:1px solid #bbb;padding:15px;margin:22px 0}@media print{body{margin:0;max-width:none}}</style></head><body><h1>New Santorini Suites</h1><p>Opposite Olake Petrol Station, Busia–Kisumu Highway</p><div class='note'><strong>BOOKING ACKNOWLEDGMENT — NOT A PAYMENT RECEIPT</strong><p>Subject to staff confirmation. This document does not confirm that payment was received.</p></div><table>"+rows+"</table><p>Contact: +254 740 686 453</p></body></html>"
    return HTMLResponse(html,headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})

@app.post("/api/bookings/mpesa/submit")
def mpesa_submit(b:Code,request:Request):
    origin(request)
    with conn() as c:
        row=sql(c,"SELECT secret,pay_method,status,pay_status,transaction_code FROM bookings WHERE id=?",(b.booking_id,),read=True)
        if not row or not secrets.compare_digest(row[0]["secret"],b.cancel_code):raise HTTPException(404,"Invalid booking credentials")
        r=row[0]
        if r["pay_method"]!="mpesa" or r["status"] in ("cancelled","rejected") or r["pay_status"] in ("verified","rejected"):
            raise HTTPException(409,"This booking cannot accept a transaction code")
        if r["transaction_code"] and r["transaction_code"]!=b.transaction_code.upper():raise HTTPException(409,"Code already submitted; contact reception")
        sql(c,"UPDATE bookings SET transaction_code=?,pay_status='submitted' WHERE id=?",(b.transaction_code.upper(),b.booking_id))
    return {"payment_status":"submitted","message":"Code received for staff verification. Payment is not yet confirmed."}
@app.post("/api/bookings/cancel")
def cancel(b:Cancel,request:Request):
    origin(request)
    with conn() as c:
        row=sql(c,"SELECT secret,status FROM bookings WHERE id=?",(b.booking_id,),read=True)
        if not row or not secrets.compare_digest(row[0]["secret"],b.cancel_code):raise HTTPException(404,"Incorrect booking reference or private code")
        if row[0]["status"]=="rejected":raise HTTPException(409,"Already rejected")
        sql(c,"UPDATE bookings SET status='cancelled' WHERE id=?",(b.booking_id,))
    return {"status":"cancelled"}
@app.post("/api/inquiries",status_code=201)
def inquiry(b:Inquiry,request:Request):
    origin(request)
    with conn() as c:sql(c,"INSERT INTO inquiries(id,name,email,message,created_at) VALUES(?,?,?,?,?)",(secrets.token_hex(10),b.name,str(b.email),b.message,moment()))
    return {"received":True}
@app.post("/api/admin/login")
def login(b:Login,request:Request):
    origin(request)
    if not ADMIN_PASSWORD or not secrets.compare_digest(b.password.encode(),ADMIN_PASSWORD.encode()):raise HTTPException(401,"Invalid password")
    resp=JSONResponse({"ok":True})
    resp.set_cookie("santorini_admin",cookie.dumps("staff"),httponly=True,max_age=28800,samesite="lax",secure=PROD)
    return resp
@app.post("/api/admin/logout")
def logout(request:Request):
    resp=JSONResponse({"ok":True});resp.delete_cookie("santorini_admin");return resp
@app.get("/api/admin/dashboard")
def dashboard(request:Request, on_date:str|None=None):
    admin(request)
    # Property calendar follows Kenya local dates, not the server's UTC timezone.
    from zoneinfo import ZoneInfo
    today=datetime.now(ZoneInfo("Africa/Nairobi")).date()
    if on_date:
        try: selected=date.fromisoformat(on_date)
        except ValueError: raise HTTPException(422,"Invalid calendar date")
    else: selected=today
    target=selected.isoformat()
    with conn() as c:
        reservations=sql(c,"""SELECT b.id,b.room_id,r.category,b.guest_name,b.email,b.phone,b.checkin,b.checkout,b.guests,b.requests,b.pay_method AS payment_method,b.pay_status AS payment_status,b.transaction_code AS mpesa_code,b.status,b.total,b.created_at FROM bookings b JOIN rooms r ON b.room_id=r.id ORDER BY b.created_at DESC LIMIT 500""",read=True)
        inquiries=sql(c,"SELECT * FROM inquiries ORDER BY created_at DESC LIMIT 100",read=True)
        rooms=sql(c,"SELECT id,category,price,active FROM rooms ORDER BY id",read=True)
        stk_paid=sql(c,"SELECT booking_id,COALESCE(SUM(amount),0) AS paid FROM stk_attempts WHERE status='verified' GROUP BY booking_id",read=True)
        counts=sql(c,"SELECT status,COUNT(*) AS qty FROM bookings GROUP BY status",read=True)
        blocked=sql(c,"""SELECT DISTINCT room_id,status FROM bookings WHERE checkin<=? AND checkout>? AND status IN ('pending','confirmed')""",(target,target),read=True)
        stays=sql(c,"""SELECT room_id,id,guest_name,status,pay_status,checkin,checkout FROM bookings WHERE checkin<=? AND checkout>? AND status IN ('pending','confirmed') ORDER BY created_at""",(target,target),read=True)
    statuses={row["status"]:row["qty"] for row in counts}
    active={r["id"] for r in rooms if r["active"]}
    reserved={row["room_id"] for row in blocked}
    confirmed_rooms={row["room_id"] for row in blocked if row["status"]=="confirmed"}
    stays_by_room={item["room_id"]:item for item in stays}
    room_calendar=[]
    for room in rooms:
        stay=stays_by_room.get(room["id"])
        state="out_of_service" if not room["active"] else ("unbooked" if not stay else ("booked" if stay["status"]=="confirmed" else "reserved_pending"))
        room_calendar.append({"room_id":room["id"],"category":room["category"],"price":room["price"],"active":bool(room["active"]),"availability":state,"booking_reference":stay["id"] if stay else None,"guest_name":stay["guest_name"] if stay else None,"payment_status":stay["pay_status"] if stay else None,"checkin":stay["checkin"] if stay else None,"checkout":stay["checkout"] if stay else None})
    stats={
        "total_rooms":len(rooms),
        "enabled_rooms":len(active),
        "active_rooms":len(active),
        "available_rooms":len(active-reserved),
        "reserved_on_date":len(active & reserved),
        "occupied_on_date":len(active & confirmed_rooms),
        "occupied_now":len(active & confirmed_rooms) if selected==today else None,
        "pending":statuses.get("pending",0),
        "confirmed":statuses.get("confirmed",0),
        "cancelled":statuses.get("cancelled",0),
        "rejected":statuses.get("rejected",0),
        "inquiries":len(inquiries),
        "on_date":target,
        "today":today.isoformat()
    }
    return {"bookings":reservations,"inquiries":inquiries,"rooms":rooms,"room_calendar":room_calendar,"payment_balances":{p["booking_id"]:p["paid"] for p in stk_paid},"stats":stats}
@app.patch("/api/admin/bookings/{bid}")
def booking_action(bid:str,b:Action,request:Request):
    admin(request);origin(request)
    with conn() as c:
        rows=sql(c,"SELECT status FROM bookings WHERE id=?",(bid,),read=True)
        if not rows:raise HTTPException(404,"Not found")
        if rows[0]["status"]!="pending":raise HTTPException(409,"Only pending bookings may be updated")
        sql(c,"UPDATE bookings SET status=? WHERE id=?",(b.status,bid))
    return {"status":b.status}
@app.patch("/api/admin/bookings/{bid}/payment")
def verify(bid:str,b:Verify,request:Request):
    admin(request);origin(request)
    with conn() as c:
        rows=sql(c,"SELECT pay_method,pay_status,transaction_code,status FROM bookings WHERE id=?",(bid,),read=True)
        if not rows:raise HTTPException(404,"Not found")
        r=rows[0]
        if r["pay_method"]!="mpesa" or r["pay_status"]!="submitted" or not r["transaction_code"] or r["status"] in ("cancelled","rejected"):
            raise HTTPException(409,"No valid submitted payment to review")
        sql(c,"UPDATE bookings SET pay_status=?,status=CASE WHEN ?='rejected' THEN 'rejected' WHEN ?='verified' AND status='pending' THEN 'confirmed' ELSE status END WHERE id=?",(b.payment_status,b.payment_status,b.payment_status,bid))
    return {"payment_status":b.payment_status,"warning":"Verify actual M-Pesa receipt independently"}
@app.patch("/api/admin/rooms/{rid}/price")
def price(rid:int,b:Price,request:Request):
    admin(request);origin(request)
    with conn() as c:
        if not sql(c,"SELECT id FROM rooms WHERE id=?",(rid,),read=True):raise HTTPException(404,"Room not found")
        sql(c,"UPDATE rooms SET price=? WHERE id=?",(b.price,rid))
    return {"updated":True}
@app.patch("/api/admin/rooms/{rid}/active")
def room_active(rid:int,b:RoomActive,request:Request):
    admin(request);origin(request)
    with conn() as c:
        if not sql(c,"SELECT id FROM rooms WHERE id=?",(rid,),read=True):raise HTTPException(404,"Room not found")
        sql(c,"UPDATE rooms SET active=? WHERE id=?",(int(b.active),rid))
    return {"updated":True}
@app.get("/api/admin/report.csv")
def report(request:Request):
    admin(request)
    with conn() as c:
        records=sql(c,"SELECT id,room_id,guest_name,email,checkin,checkout,total,pay_method,pay_status,status,created_at FROM bookings ORDER BY created_at DESC",read=True)
    keys=["id","room_id","guest_name","email","checkin","checkout","total","pay_method","pay_status","status","created_at"]
    buf=io.StringIO();writer=csv.writer(buf);writer.writerow(keys)
    for rec in records:writer.writerow([("'"+str(rec[k])) if str(rec[k]).startswith(("=","+","-","@")) else rec[k] for k in keys])
    return Response(buf.getvalue(),media_type="text/csv",headers={"Content-Disposition":'attachment; filename="santorini-bookings.csv"'})

# Optional STK integration; remains disabled without explicit credentials.
from stk_routes import router as stk_router
app.include_router(stk_router)
