"""Business rules shared by the customer, agent and admin views."""
import math
import re
from datetime import timedelta

from flask import current_app
from sqlalchemy import func

from . import mpesa
from .content import BANDS, CATS
from .models import Agent, Message, Payment, Shop, Ticket, db, utcnow

PHONE_RE = re.compile(r"^0[17]\d{8}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def norm_phone(v):
    v = re.sub(r"[\s-]", "", v or "")
    return re.sub(r"^\+?254", "0", v)


def parse_amount(v):
    v = re.sub(r"[\s,]", "", (v or "")).removeprefix("KSh").removeprefix("ksh")
    return int(v) if v.isdigit() else None


def ksh(n):
    return f"KSh {int(n or 0):,}"


# ---------- tickets and agents ----------

def new_ticket(business, cat_id, idx, priority, note, place, lat, lng, acc):
    cat = CATS[cat_id]
    band = cat["issues"][idx]["band"]
    t = Ticket(business=business, cat_id=cat_id, issue_idx=idx, title=cat["issues"][idx]["title"],
               priority=priority, band=band, fee=BANDS[band]["fee"], note=note[:2000], place=place[:160],
               lat=lat, lng=lng, accuracy=acc)
    db.session.add(t)
    db.session.flush()
    auto_assign(t)
    return t


def open_count(agent_id):
    return db.session.scalar(db.select(func.count(Ticket.id)).where(
        Ticket.agent_id == agent_id, Ticket.work != "resolved")) or 0


def eligible_agents(ticket):
    agents = db.session.scalars(db.select(Agent).where(Agent.status == "approved")).all()
    return [a for a in agents if ticket.cat_id in (a.specs or [])]


def assign(ticket, agent):
    ticket.agent = agent
    ticket.work = "assigned" if agent else None


def auto_assign(ticket):
    """Give the ticket to the approved agent with this speciality and the fewest open tickets."""
    choices = sorted(eligible_agents(ticket), key=lambda a: open_count(a.id))
    if choices:
        assign(ticket, choices[0])
    return choices[0] if choices else None


def agent_business_ids(agent):
    rows = db.session.scalars(db.select(Ticket.business_id).where(Ticket.agent_id == agent.id).distinct())
    return set(rows)


def business_agent(business):
    t = db.session.scalars(db.select(Ticket).where(Ticket.business_id == business.id, Ticket.agent_id.is_not(None))
                           .order_by(Ticket.id.desc())).first()
    return t.agent if t else None


# ---------- payments ----------

def start_stk(ticket, phone):
    merchant_id, checkout_id = mpesa.stk_push(phone, ticket.fee, ticket.code, "IT support")
    p = Payment(ticket=ticket, method="stk", phone=mpesa.to_msisdn(phone), amount=ticket.fee,
                merchant_request_id=merchant_id, checkout_request_id=checkout_id,
                simulated=mpesa.mode() == "simulate")
    db.session.add(p)
    return p


def record_till(ticket, code):
    p = Payment(ticket=ticket, method="till", amount=ticket.fee, receipt=code, status="review",
                result_desc="Waiting for the admin to confirm the code on the M-Pesa statement")
    db.session.add(p)
    return p


def settle(payment, ok, desc="", receipt=None, amount=None):
    """Apply a final result to a payment and its ticket. Ignores repeats."""
    if payment.status in ("success", "failed"):
        return
    if ok and amount is not None and int(float(amount)) < payment.amount:
        ok, desc = False, f"Amount paid ({amount}) is less than {payment.amount}"
    payment.status = "success" if ok else "failed"
    payment.result_desc = desc[:255]
    if receipt:
        payment.receipt = receipt
    if ok:
        payment.ticket.status = "paid"


def refresh_pending(payment):
    """Resolve a pending STK payment without waiting for the callback (simulate mode, or callback lost)."""
    if payment.status != "pending" or payment.method != "stk":
        return
    age = utcnow() - payment.created_at
    if payment.simulated:
        if age > timedelta(seconds=6):
            settle(payment, True, "Simulated payment", receipt="SIM" + payment.checkout_request_id[-7:])
        return
    if age < timedelta(seconds=20):
        return
    try:
        code, desc = mpesa.stk_query(payment.checkout_request_id)
    except mpesa.MpesaError:
        return
    if code is not None:
        settle(payment, code == 0, desc)
    elif age > timedelta(minutes=3):
        settle(payment, False, "No answer from M-Pesa. The prompt expired.")


# ---------- shops ----------

def km(a_lat, a_lng, b_lat, b_lng):
    r = 6371
    dlat, dlng = math.radians(b_lat - a_lat), math.radians(b_lng - a_lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a_lat)) * math.cos(math.radians(b_lat)) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def nearby_shops(lat=None, lng=None, place="", cat=None, limit=None):
    shops = db.session.scalars(db.select(Shop).where(Shop.status == "approved")).all()
    if cat:
        shops = [s for s in shops if cat in (s.cats or [])]
    place = (place or "").lower()
    out = []
    for s in shops:
        d = km(lat, lng, s.lat, s.lng) if lat is not None and s.lat is not None else None
        town = bool(place and s.town and s.town.lower() in place)
        out.append({"shop": s, "km": d, "town": town})
    out.sort(key=lambda x: (x["km"] is None, x["km"] or 0, not x["town"], x["shop"].name.lower()))
    return out[:limit] if limit else out


def map_link(shop):
    if shop.lat is not None:
        return f"https://www.google.com/maps/dir/?api=1&destination={shop.lat},{shop.lng}"
    from urllib.parse import quote
    return "https://www.google.com/maps/search/?api=1&query=" + quote(f"{shop.name} {shop.address or ''} {shop.town}")


# ---------- live updates ----------

def stamp(*parts):
    """A cheap fingerprint of what a dashboard shows; the page reloads its data when it changes."""
    return "-".join(str(p) for p in parts)


def one_ticket_stamp(t):
    last = t.payments[0] if t.payments else None
    return stamp(t.status, t.work, t.fee, t.agent_id, last and last.id, last and last.status)


def ticket_stamp(query):
    row = db.session.execute(query.with_only_columns(func.count(Ticket.id), func.max(Ticket.updated_at))).one()
    return stamp(row[0], row[1])


def message_stamp(business_ids=None):
    q = db.select(func.count(Message.id), func.max(Message.id))
    if business_ids is not None:
        q = q.where(Message.business_id.in_(business_ids or [-1]))
    row = db.session.execute(q).one()
    return stamp(row[0], row[1])


def mpesa_status():
    m = current_app.config["MPESA_ENV"]
    return {"mode": m, "ready": mpesa.configured(), "till": mpesa.till_number(),
            "paybill": current_app.config["MPESA_TRANSACTION_TYPE"] != "CustomerBuyGoodsOnline"}
