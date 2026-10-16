"""JSON endpoints behind the live parts of the pages, and the M-Pesa callback."""
import hmac

from flask import Blueprint, abort, current_app, g, jsonify, request
from sqlalchemy import func

from . import mpesa
from .content import search
from .models import Agent, Message, Payment, Shop, Ticket, db
from .services import (agent_business_ids, message_stamp, one_ticket_stamp, refresh_pending, settle, stamp,
                       ticket_stamp)

bp = Blueprint("api", __name__)


@bp.get("/api/search")
def api_search():
    return jsonify(search(request.args.get("q", "")))


# ---------- chat ----------

def _can_chat(bid):
    if g.role == "business":
        return g.user.id == bid
    if g.role == "agent":
        return g.user.status == "approved" and bid in agent_business_ids(g.user)
    return g.role == "admin"


def _msg_json(m):
    return {"id": m.id, "sender": m.sender, "name": m.sender_name, "text": m.text,
            "when": (m.created_at).isoformat() + "Z"}


@bp.get("/api/chat/<int:bid>")
def chat_get(bid):
    if not g.role:
        abort(401)
    if not _can_chat(bid):
        abort(403)
    after = request.args.get("after", 0, type=int)
    rows = db.session.scalars(db.select(Message).where(Message.business_id == bid, Message.id > after)
                              .order_by(Message.id).limit(200)).all()
    return jsonify([_msg_json(m) for m in rows])


@bp.post("/api/chat/<int:bid>")
def chat_post(bid):
    if not g.role:
        abort(401)
    if not _can_chat(bid):
        abort(403)
    text = (request.get_json(silent=True) or {}).get("text", "").strip()
    if not text:
        abort(400, "Type a message first.")
    name = {"business": lambda: g.user.name, "agent": lambda: g.user.name, "admin": lambda: "Helpdesk"}[g.role]()
    m = Message(business_id=bid, sender={"business": "customer"}.get(g.role, g.role), sender_name=name,
                text=text[:2000])
    db.session.add(m)
    db.session.commit()
    return jsonify(_msg_json(m)), 201


# ---------- ticket and dashboard state ----------

@bp.get("/api/tickets/<int:tid>/state")
def ticket_state(tid):
    t = db.session.get(Ticket, tid)
    if not t or not ((g.role == "business" and t.business_id == g.user.id) or g.role == "admin"):
        abort(404)
    p = t.open_payment
    if p:
        refresh_pending(p)
        db.session.commit()
    last = t.payments[0] if t.payments else None
    return jsonify({"status": t.status, "work": t.work, "fee": t.fee,
                    "payment": last and {"status": last.status, "desc": last.result_desc, "receipt": last.receipt},
                    "stamp": one_ticket_stamp(t)})


@bp.get("/api/pulse")
def pulse():
    """A fingerprint of everything the signed-in person's dashboard shows."""
    if g.role == "business":
        s = stamp(ticket_stamp(db.select(Ticket).where(Ticket.business_id == g.user.id)),
                  message_stamp([g.user.id]))
    elif g.role == "agent":
        s = stamp(g.user.status, ticket_stamp(db.select(Ticket).where(Ticket.agent_id == g.user.id)),
                  message_stamp(list(agent_business_ids(g.user))))
    elif g.role == "shop":
        s = stamp(g.user.status)
    elif g.role == "admin":
        pay = db.session.execute(db.select(func.count(Payment.id), func.max(Payment.updated_at))).one()
        s = stamp(ticket_stamp(db.select(Ticket)), message_stamp(), pay[0], pay[1],
                  db.session.scalar(db.select(func.count(Agent.id)).where(Agent.status == "pending")),
                  db.session.scalar(db.select(func.count(Shop.id)).where(Shop.status == "pending")))
    else:
        abort(401)
    return jsonify({"stamp": s})


# ---------- M-Pesa ----------

@bp.post("/mpesa/callback/<token>")
def mpesa_callback(token):
    """Safaricom posts the STK result here. The secret token in the URL keeps strangers out."""
    if not hmac.compare_digest(token, current_app.config["MPESA_CALLBACK_TOKEN"]):
        abort(404)
    try:
        data = mpesa.parse_callback(request.get_json(force=True))
    except (KeyError, TypeError, ValueError):
        current_app.logger.warning("Unreadable M-Pesa callback: %s", request.get_data(as_text=True)[:500])
        return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})
    p = db.session.scalar(db.select(Payment).where(Payment.checkout_request_id == data["checkout_request_id"]))
    if p:
        settle(p, data["result_code"] == 0, data["result_desc"], receipt=data["receipt"], amount=data["amount"])
        db.session.commit()
    else:
        current_app.logger.warning("M-Pesa callback for unknown request %s", data["checkout_request_id"])
    return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})
