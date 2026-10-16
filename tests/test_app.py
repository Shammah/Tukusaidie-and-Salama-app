"""End-to-end checks of every role's main flow, using Flask's test client and a throwaway SQLite database."""
import re
from datetime import timedelta

import pytest

from app import create_app
from app.models import Admin, Agent, Payment, Shop, Ticket, db


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MPESA_ENV", "simulate")
    monkeypatch.setenv("ADMIN_EMAIL", "owner@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "owner-pass-123")
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 't.db'}", "TESTING": True})
    with app.app_context():
        db.create_all()
        if not db.session.scalar(db.select(Admin)):
            a = Admin(email="owner@example.com")
            a.set_password("owner-pass-123")
            db.session.add(a)
            db.session.commit()
    yield app


def csrf(client, url="/"):
    html = client.get(url).get_data(as_text=True)
    return re.search(r'name="csrf" content="([^"]+)"', html).group(1)


def post(client, url, data=None, page="/", **kw):
    data = dict(data or {})
    data["csrf"] = csrf(client, page)
    return client.post(url, data=data, **kw)


def register_business(client, phone="0712345678", name="Kisii Fresh"):
    return post(client, "/account/register", {"name": name, "phone": phone, "password": "secret-pass"},
                page="/account/register", follow_redirects=True)


def test_public_pages(app):
    c = app.test_client()
    for url in ["/", "/tukusaidie/", "/tukusaidie/services", "/tukusaidie/services/print", "/tukusaidie/shops",
                "/salama/", "/account/register", "/account/login", "/agent/login", "/agent/register",
                "/shop/login", "/shop/register", "/admin/login"]:
        r = c.get(url)
        assert r.status_code == 200, url
    assert c.get("/tukusaidie/services/nope").status_code == 404
    assert c.get("/api/search?q=printer").json


def test_csrf_required(app):
    c = app.test_client()
    r = c.post("/account/register", data={"name": "x", "phone": "0712345678", "password": "secret-pass"})
    assert r.status_code == 400


def test_register_validation_and_login(app):
    c = app.test_client()
    r = post(c, "/account/register", {"name": "A", "phone": "123", "password": "secret-pass"}, page="/account/register")
    assert b"Kenyan mobile number" in r.data
    assert b"Karibu" in register_business(c).data
    r = post(c, "/account/register", {"name": "B", "phone": "+254 712 345 678", "password": "secret-pass"},
             page="/account/register")
    assert b"already registered" in r.data
    c2 = app.test_client()
    r = post(c2, "/account/login", {"phone": "0712345678", "password": "wrong-pass"}, page="/account/login")
    assert b"not right" in r.data
    r = post(c2, "/account/login", {"phone": "0712 345 678", "password": "secret-pass"}, page="/account/login",
             follow_redirects=True)
    assert b"Habari, Kisii Fresh" in r.data


def test_ticket_offer_admin_and_simulated_stk(app):
    c = app.test_client()
    register_business(c)
    r = post(c, "/tukusaidie/services/print/1/ticket", {"priority": "Urgent", "note": "jam", "place": "Kisii"},
             page="/tukusaidie/services/print/1/ticket")
    assert r.status_code == 302
    with app.app_context():
        t = db.session.scalar(db.select(Ticket))
        tid, fee = t.id, t.fee
        assert t.status == "quote" and t.priority == "Urgent"
    page = f"/tukusaidie/tickets/{tid}"
    assert b"Here is the price" in c.get(page).data

    # customer offers less, admin keeps the price, customer accepts
    post(c, f"{page}/offer", {"offer": str(fee - 50), "note": "two printers"}, page=page)
    with app.app_context():
        assert db.session.get(Ticket, tid).status == "negotiating"
    admin = app.test_client()
    post(admin, "/admin/login", {"email": "owner@example.com", "password": "owner-pass-123"}, page="/admin/login")
    assert b"Customer offer" in admin.get(f"/admin/tickets/{tid}").data
    post(admin, f"/admin/tickets/{tid}/offer-accept", page="/admin/")
    with app.app_context():
        t = db.session.get(Ticket, tid)
        assert t.status == "accepted" and t.fee == fee - 50

    # pay with a simulated M-Pesa prompt; it settles after a few seconds
    post(c, f"{page}/pay", {"method": "stk", "phone": "0712345678"}, page=page)
    with app.app_context():
        p = db.session.scalar(db.select(Payment))
        assert p.status == "pending" and p.simulated and p.phone == "254712345678"
        p.created_at -= timedelta(seconds=10)
        db.session.commit()
    s = c.get(f"/api/tickets/{tid}/state").json
    assert s["status"] == "paid" and s["payment"]["status"] == "success"


def test_till_code_needs_admin_confirmation(app):
    c = app.test_client()
    register_business(c)
    post(c, "/tukusaidie/services/mail/0/ticket", {}, page="/tukusaidie/")
    with app.app_context():
        tid = db.session.scalar(db.select(Ticket.id))
    page = f"/tukusaidie/tickets/{tid}"
    post(c, f"{page}/accept", page=page)
    r = post(c, f"{page}/pay", {"method": "till", "code": "short"}, page=page, follow_redirects=True)
    assert b"10-character" in r.data
    post(c, f"{page}/pay", {"method": "till", "code": "qj61abcd23"}, page=page)
    with app.app_context():
        p = db.session.scalar(db.select(Payment))
        assert p.status == "review" and p.receipt == "QJ61ABCD23"
        pid = p.id
    admin = app.test_client()
    post(admin, "/admin/login", {"email": "owner@example.com", "password": "owner-pass-123"}, page="/admin/login")
    assert b"Confirm till code QJ61ABCD23" in admin.get("/admin/").data
    post(admin, f"/admin/tickets/{tid}/payment-ok", {"payment": pid}, page="/admin/")
    with app.app_context():
        assert db.session.get(Ticket, tid).status == "paid"


def test_mpesa_callback(app):
    c = app.test_client()
    register_business(c)
    post(c, "/tukusaidie/services/pc/0/ticket", {}, page="/tukusaidie/")
    with app.app_context():
        t = db.session.scalar(db.select(Ticket))
        t.status = "accepted"
        db.session.add(Payment(ticket=t, method="stk", amount=t.fee, checkout_request_id="ws_CO_123"))
        db.session.commit()
        tid, fee = t.id, t.fee
        token = app.config["MPESA_CALLBACK_TOKEN"]
    body = lambda amount, code=0: {"Body": {"stkCallback": {
        "MerchantRequestID": "m1", "CheckoutRequestID": "ws_CO_123", "ResultCode": code, "ResultDesc": "ok",
        "CallbackMetadata": {"Item": [{"Name": "Amount", "Value": amount}, {"Name": "MpesaReceiptNumber", "Value": "RKT1234567"},
                                      {"Name": "PhoneNumber", "Value": 254712345678}]}}}}
    assert c.post("/mpesa/callback/wrong-token", json=body(fee)).status_code == 404
    r = c.post(f"/mpesa/callback/{token}", json=body(fee))
    assert r.json["ResultCode"] == 0
    with app.app_context():
        assert db.session.get(Ticket, tid).status == "paid"
        assert db.session.scalar(db.select(Payment)).receipt == "RKT1234567"


def test_underpaid_callback_is_rejected(app):
    c = app.test_client()
    register_business(c)
    post(c, "/tukusaidie/services/pc/0/ticket", {}, page="/tukusaidie/")
    with app.app_context():
        t = db.session.scalar(db.select(Ticket))
        t.status = "accepted"
        db.session.add(Payment(ticket=t, method="stk", amount=t.fee, checkout_request_id="ws_CO_9"))
        db.session.commit()
        token, tid = app.config["MPESA_CALLBACK_TOKEN"], t.id
    c.post(f"/mpesa/callback/{token}", json={"Body": {"stkCallback": {"CheckoutRequestID": "ws_CO_9", "ResultCode": 0,
           "CallbackMetadata": {"Item": [{"Name": "Amount", "Value": 1}]}}}})
    with app.app_context():
        assert db.session.get(Ticket, tid).status == "accepted"
        assert db.session.scalar(db.select(Payment)).status == "failed"


def test_agent_flow_assignment_and_chat(app):
    g = app.test_client()
    r = post(g, "/agent/register", {"name": "Brian M", "email": "brian@example.com", "password": "agent-pass",
                                    "specs": ["print", "pc"], "certs": ["CompTIA A+"], "cert_ref": "A+ 123"},
             page="/agent/register", follow_redirects=True)
    assert b"Almost there" in r.data

    c = app.test_client()
    register_business(c)
    post(c, "/tukusaidie/services/print/0/ticket", {}, page="/tukusaidie/")
    with app.app_context():
        assert db.session.scalar(db.select(Ticket)).agent_id is None  # agent still pending

    admin = app.test_client()
    post(admin, "/admin/login", {"email": "owner@example.com", "password": "owner-pass-123"}, page="/admin/login")
    with app.app_context():
        aid = db.session.scalar(db.select(Agent.id))
    r = post(admin, f"/admin/agents/{aid}/approve", page="/admin/", follow_redirects=True)
    assert b"1 waiting ticket assigned" in r.data
    with app.app_context():
        t = db.session.scalar(db.select(Ticket))
        assert t.agent_id == aid and t.work == "assigned"
        tid, bid = t.id, t.business_id

    assert b"Start work" in g.get("/agent/").data
    post(g, f"/agent/tickets/{tid}/work", {"step": "start"}, page="/agent/")
    post(g, f"/agent/tickets/{tid}/work", {"step": "done"}, page="/agent/")
    with app.app_context():
        assert db.session.get(Ticket, tid).work == "resolved"

    # chat: customer writes, agent sees and answers; outsiders cannot read it
    tok = csrf(c, "/tukusaidie/chat")
    assert c.post(f"/api/chat/{bid}", json={"text": "Hello"}, headers={"X-CSRF": tok}).status_code == 201
    msgs = g.get(f"/api/chat/{bid}").json
    assert msgs[0]["text"] == "Hello" and msgs[0]["sender"] == "customer"
    tok = csrf(g, "/agent/")
    g.post(f"/api/chat/{bid}", json={"text": "On my way"}, headers={"X-CSRF": tok})
    assert [m["text"] for m in c.get(f"/api/chat/{bid}?after={msgs[0]['id']}").json] == ["On my way"]
    other = app.test_client()
    register_business(other, phone="0798765432", name="Other")
    assert other.get(f"/api/chat/{bid}").status_code == 403


def test_shop_flow_and_nearby(app):
    s = app.test_client()
    post(s, "/shop/register", {"name": "Kisii Tech", "phone": "0711000009", "password": "shop-pass-1", "town": "Kisii",
                               "cats": ["pc", "print"]}, page="/shop/register")
    post(s, "/shop/services", {"name": "Printer repair", "cat": "print", "price": "1,200"}, page="/shop/")
    post(s, "/shop/records", {"customer": "Jane", "amount": "1200", "service": "Printer repair"}, page="/shop/")
    post(s, "/shop/profile", {"town": "Kisii", "address": "Hospital Rd", "lat": "-0.68", "lng": "34.77"}, page="/shop/")
    with app.app_context():
        shop = db.session.scalar(db.select(Shop))
        assert shop.services[0].price == 1200 and shop.records[0].amount == 1200 and shop.lat == -0.68
        sid = shop.id
    c = app.test_client()
    assert b"Kisii Tech" not in c.get("/tukusaidie/shops").data  # pending shops stay hidden
    admin = app.test_client()
    post(admin, "/admin/login", {"email": "owner@example.com", "password": "owner-pass-123"}, page="/admin/login")
    post(admin, f"/admin/shops/{sid}/approve", page="/admin/")
    r = c.get("/tukusaidie/shops?lat=-0.69&lng=34.77")
    assert b"Kisii Tech" in r.data and b"1.1 km" in r.data


def test_salama_check_results_and_csv(app):
    c = app.test_client()
    register_business(c)
    assert b"Ready when you are" in c.get("/salama/").data
    post(c, "/salama/check", {"i": 0, "answer": "yes"}, page="/salama/check")
    post(c, "/salama/check", {"i": 1, "answer": "no"}, page="/salama/check")
    r = c.get("/salama/results")
    assert b"Change the router or Starlink admin login" in r.data  # the "no" shows up as a fix
    csv = c.get("/salama/results.csv").get_data(as_text=True)
    assert "Kisii Fresh" in csv and "Yes" in csv
    post(c, "/salama/size", {"size": "L"}, page="/salama/")
    assert b"Large company" in c.get("/salama/results").data


def test_roles_are_separate(app):
    c = app.test_client()
    register_business(c)
    assert c.get("/admin/").status_code == 302
    assert c.get("/agent/").status_code == 302
    assert c.get("/api/pulse").status_code == 200
    anon = app.test_client()
    assert anon.get("/tukusaidie/tickets").status_code == 302
    assert anon.get("/api/pulse").status_code == 401


def test_admin_pages_render_with_demo_data(app):
    from app.demo import seed
    with app.app_context():
        seed()
    admin = app.test_client()
    post(admin, "/admin/login", {"email": "owner@example.com", "password": "owner-pass-123"}, page="/admin/login")
    for url in ["/admin/", "/admin/tickets", "/admin/tickets?status=negotiating", "/admin/tickets/1", "/admin/agents",
                "/admin/shops", "/admin/chats", "/admin/businesses", "/admin/businesses?b=1"]:
        assert admin.get(url).status_code == 200, url
