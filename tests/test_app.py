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
