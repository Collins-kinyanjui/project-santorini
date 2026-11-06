import os, importlib
from datetime import date, timedelta
from fastapi.testclient import TestClient
os.environ["APP_ENV"]="development"
os.environ["DATABASE_URL"]="sqlite:///./test_santorini.sqlite"
os.environ["ADMIN_PASSWORD"]="test-only-staff-password"
os.environ["SESSION_SECRET"]="test-only-secret"
os.environ["PUBLIC_URL"]=""
app=importlib.import_module("app")
client=TestClient(app.app)
def future(n): return (date.today()+timedelta(days=n)).isoformat()
def test_pages_and_inventory():
    assert client.get("/").status_code==200
    assert client.get("/booking").status_code==200
    assert client.get("/admin").status_code==200
    assert client.get("/styles.css").status_code==200
    assert client.get("/api/health").json()["rooms"]==20
    types={x["category"]:x["active_count"] for x in client.get("/api/rooms").json()}
    assert types=={"single":12,"double":5,"deluxe":3}
def test_booking_mpesa_and_admin():
    body={"category":"single","guest_name":"Test Guest","email":"guest@example.com","phone":"0712345678","checkin":future(10),"checkout":future(12),"guests":1,"requests":"","payment_method":"mpesa"}
    r=client.post("/api/bookings",json=body)
    assert r.status_code==201,r.text
    booking=r.json()
    assert booking["total"]==3000
    ref=booking["booking_id"]
    assert client.get("/api/admin/dashboard").status_code==401
    assert client.post("/api/admin/login",json={"password":"wrong"}).status_code==401
    assert client.post("/api/admin/login",json={"password":"test-only-staff-password"}).status_code==200
    submit=client.post("/api/bookings/mpesa/submit",json={"booking_id":ref,"cancel_code":booking["cancel_code"],"transaction_code":"TTEST1234"})
    assert submit.status_code==200,submit.text
    assert submit.json()["payment_status"]=="submitted"
    verify=client.patch(f"/api/admin/bookings/{ref}/payment",json={"payment_status":"verified"})
    assert verify.status_code==200,verify.text
    dashboard=client.get("/api/admin/dashboard")
    assert dashboard.status_code==200
    assert any(b["id"]==ref and b["payment_status"]=="verified" for b in dashboard.json()["bookings"])
    assert client.get("/api/admin/report.csv").status_code==200
def test_no_automatic_card_charges():
    body={"category":"single","guest_name":"Test Guest","email":"guest@example.com","phone":"0712345678","checkin":future(20),"checkout":future(21),"guests":1,"requests":"","payment_method":"card"}
    assert client.post("/api/bookings",json=body).status_code==422

def test_date_aware_dashboard_and_cancelled_room_release():
    start, end = future(90), future(91)
    login=client.post("/api/admin/login",json={"password":"test-only-staff-password"})
    assert login.status_code==200
    initial=client.get(f"/api/admin/dashboard?on_date={start}").json()["stats"]
    bookings=[]
    for i in range(3):
        reservation=client.post("/api/bookings",json={
            "category":"single","guest_name":f"Dashboard Example {i}",
            "email":f"dashboard{i}@example.com","phone":"0712345678",
            "checkin":start,"checkout":end,"guests":1,"requests":"",
            "payment_method":"arrival"
        })
        assert reservation.status_code==201,reservation.text
        bookings.append(reservation.json())
    for b in bookings[:2]:
        assert client.patch(f"/api/admin/bookings/{b['booking_id']}",json={"status":"confirmed"}).status_code==200
    last=bookings[2]
    assert client.post("/api/bookings/cancel",json={"booking_id":last["booking_id"],"cancel_code":last["cancel_code"]}).status_code==200
    stats=client.get(f"/api/admin/dashboard?on_date={start}").json()["stats"]
    assert stats["confirmed"]==initial["confirmed"]+2
    assert stats["cancelled"]==initial["cancelled"]+1
    assert stats["pending"]==initial["pending"]
    assert stats["reserved_on_date"]==initial["reserved_on_date"]+2
    assert stats["available_rooms"]==initial["available_rooms"]-2
    assert stats["occupied_on_date"]==initial["occupied_on_date"]+2
    assert stats["total_rooms"]==20
    response=client.get(f"/api/availability?checkin={start}&checkout={end}")
    assert response.status_code==200
    singles=next(r for r in response.json()["categories"] if r["category"]=="single")
    assert singles["available"]==10
    today=client.get("/api/admin/dashboard").json()["stats"]
    assert today["on_date"]==today["today"]
    assert today["confirmed"]>=stats["confirmed"]
    assert client.get("/api/admin/dashboard?on_date=not-a-date").status_code==422

def test_inventory_and_print_revocation_on_rejected_payment():
    checkin,checkout=future(130),future(131)
    client.post("/api/admin/login",json={"password":"test-only-staff-password"})
    response=client.post("/api/bookings",json={
        "category":"deluxe","guest_name":"Print Revocation Test",
        "email":"revocation@example.com","phone":"0712345678",
        "checkin":checkin,"checkout":checkout,"guests":2,
        "requests":"","payment_method":"mpesa"
    })
    assert response.status_code==201,response.text
    b=response.json()
    creds={"booking_id":b["booking_id"],"cancel_code":b["cancel_code"]}
    dashboard=client.get(f"/api/admin/dashboard?on_date={checkin}").json()
    booked_room=next(x for x in dashboard["room_calendar"] if x["booking_reference"]==b["booking_id"])
    assert booked_room["availability"]=="reserved_pending"
    assert client.post("/api/bookings/status",json=creds).json()["print_allowed"] is True
    assert client.post("/api/bookings/acknowledgment",json=creds).status_code==200
    submit=client.post("/api/bookings/mpesa/submit",json={**creds,"transaction_code":"REJECT123ABC"})
    assert submit.status_code==200
    rejected=client.patch(f"/api/admin/bookings/{b['booking_id']}/payment",json={"payment_status":"rejected"})
    assert rejected.status_code==200,rejected.text
    state=client.post("/api/bookings/status",json=creds).json()
    assert state["payment_status"]=="rejected"
    assert state["status"]=="rejected"
    assert state["print_allowed"] is False
    assert client.post("/api/bookings/acknowledgment",json=creds).status_code==403
    assert client.post("/api/bookings/mpesa/submit",json={**creds,"transaction_code":"REJECT123ABC"}).status_code==409
    after=client.get(f"/api/admin/dashboard?on_date={checkin}").json()
    freed=next(x for x in after["room_calendar"] if x["room_id"]==booked_room["room_id"])
    assert freed["availability"]=="unbooked"
    assert freed["booking_reference"] is None
    available=client.get(f"/api/availability?checkin={checkin}&checkout={checkout}").json()
    assert next(x for x in available["categories"] if x["category"]=="deluxe")["available"]==3


def test_confirmed_payment_marks_room_booked_and_blocks_double_booking():
    checkin,checkout=future(140),future(141)
    client.post("/api/admin/login",json={"password":"test-only-staff-password"})
    def make():
        return client.post("/api/bookings",json={
            "category":"deluxe","guest_name":"Inventory Example","email":"inventory@example.com",
            "phone":"0712345678","checkin":checkin,"checkout":checkout,"guests":2,
            "requests":"","payment_method":"mpesa"
        })
    reservations=[]
    for i in range(3):
        response=make()
        assert response.status_code==201,response.text
        reservations.append(response.json())
    full=make()
    assert full.status_code==409
    ref=reservations[0]
    submit=client.post("/api/bookings/mpesa/submit",json={
        "booking_id":ref["booking_id"],"cancel_code":ref["cancel_code"],"transaction_code":"VERIFIED123ABC"
    })
    assert submit.status_code==200
    verified=client.patch(f"/api/admin/bookings/{ref['booking_id']}/payment",json={"payment_status":"verified"})
    assert verified.status_code==200,verified.text
    status=client.post("/api/bookings/status",json={"booking_id":ref["booking_id"],"cancel_code":ref["cancel_code"]}).json()
    assert status["status"]=="confirmed"
    assert status["payment_status"]=="verified"
    calendar=client.get(f"/api/admin/dashboard?on_date={checkin}").json()["room_calendar"]
    assert len([x for x in calendar if x["category"]=="deluxe" and x["availability"]=="booked"])==1
    assert len([x for x in calendar if x["category"]=="deluxe" and x["availability"]=="reserved_pending"])==2
    # Cancel only a still-pending reservation and verify room inventory returns.
    b=reservations[1]
    assert client.post("/api/bookings/cancel",json={"booking_id":b["booking_id"],"cancel_code":b["cancel_code"]}).status_code==200
    assert make().status_code==201
