"""Customer side: one business account for both Tukusaidie (helpdesk) and Salama (cyber check)."""
import csv
import io

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, session, url_for

from . import auth, mpesa
from .auth import require
from .content import BAND_ADVICE, BANDS, CATS, HELP, PRIORITIES, TOP_CATS, issue, questions, search, summary
from .models import Assessment, Business, Message, Ticket, db
from .services import (PHONE_RE, business_agent, mpesa_status, nearby_shops, new_ticket, norm_phone,
                       one_ticket_stamp, parse_amount, record_till, start_stk)

bp = Blueprint("customer", __name__)
tuku = Blueprint("tuku", __name__, url_prefix="/tukusaidie")
salama = Blueprint("salama", __name__, url_prefix="/salama")


def _app_for(path):
    return "salama" if (path or "").startswith("/salama") else "tuku"


@bp.get("/")
def index():
    return render_template("index.html", app="portal")


@bp.route("/account/register", methods=["GET", "POST"])
def register():
    nxt = auth.safe_next(url_for("tuku.home"))
    form = {"name": "", "phone": ""}
    if request.method == "POST":
        form = {"name": request.form.get("name", "").strip(), "phone": norm_phone(request.form.get("phone"))}
        pw = request.form.get("password", "")
        err = None
        if not form["name"]:
            err = "Enter the business name."
        elif not PHONE_RE.match(form["phone"]):
            err = "Enter a Kenyan mobile number, for example 0712 345 678."
        elif db.session.scalar(db.select(Business).where(Business.phone == form["phone"])):
            err = "This number is already registered. Sign in instead."
        elif len(pw) < 8:
            err = "Choose a password of at least 8 characters."
        if err:
            flash(err, "error")
        else:
            b = Business(name=form["name"][:120], phone=form["phone"])
            b.set_password(pw)
            db.session.add(b)
            db.session.commit()
            auth.login("business", b)
            flash(f"Karibu, {b.name}. Your account is ready.", "ok")
            return redirect(nxt)
    return render_template("account/register.html", app=_app_for(nxt), form=form, next=nxt)


@bp.route("/account/login", methods=["GET", "POST"])
def login():
    nxt = auth.safe_next(url_for("tuku.home"))
    phone = ""
    if request.method == "POST":
        if auth.throttled():
            auth.throttle_message()
        else:
            phone = norm_phone(request.form.get("phone"))
            b = db.session.scalar(db.select(Business).where(Business.phone == phone))
            if b and b.check_password(request.form.get("password", "")):
                auth.login("business", b)
                return redirect(nxt)
            flash("Phone number or password is not right.", "error")
    return render_template("account/login.html", app=_app_for(nxt), phone=phone, next=nxt)


@bp.post("/account/logout")
def logout():
    back = auth.safe_next(url_for("customer.index"))
    auth.logout()
    return redirect(back)


# ====================== Tukusaidie ======================

def _my_ticket(tid):
    t = db.session.get(Ticket, tid)
    if not t or t.business_id != g.user.id:
        abort(404)
    return t


def _saved_loc():
    loc = session.get("loc") or {}
    return loc.get("lat"), loc.get("lng"), loc.get("place", "")


@tuku.get("/")
def home():
    if g.role != "business":
        return render_template("tuku/landing.html", app="tuku", n_issues=sum(len(c["issues"]) for c in HELP))
    q = request.args.get("q", "")
    lat, lng, place = _saved_loc()
    tickets = db.session.scalars(db.select(Ticket).where(Ticket.business_id == g.user.id)
                                 .order_by(Ticket.id.desc())).all()
    return render_template("tuku/home.html", app="tuku", q=q, hits=search(q) if q else [],
                           top=[CATS[c] for c in TOP_CATS], tickets=tickets,
                           open_tickets=[t for t in tickets if t.work != "resolved"],
                           shops=nearby_shops(lat, lng, place, limit=3))


@tuku.get("/services")
def services():
    return render_template("tuku/services.html", app="tuku")


@tuku.get("/services/<cat_id>")
def category(cat_id):
    cat = CATS.get(cat_id) or abort(404)
    lat, lng, place = _saved_loc()
    return render_template("tuku/category.html", app="tuku", cat=cat, bands=BANDS, advice=BAND_ADVICE,
                           shops=nearby_shops(lat, lng, place, cat=cat_id, limit=3),
                           open_idx=request.args.get("open", type=int))


@tuku.route("/services/<cat_id>/<int:idx>/ticket", methods=["GET", "POST"])
@require("business")
def raise_ticket(cat_id, idx):
    cat, iss = issue(cat_id, idx)
    if not cat:
        abort(404)
    if request.method == "POST":
        prio = request.form.get("priority", "Normal")
        if prio not in [p[0] for p in PRIORITIES]:
            prio = "Normal"
        f = request.form
        lat, lng, acc = f.get("lat", type=float), f.get("lng", type=float), f.get("acc", type=float)
        if lat is not None and not (-90 <= lat <= 90 and -180 <= (lng or 999) <= 180):
            lat = lng = acc = None
        place = f.get("place", "").strip()
        t = new_ticket(g.user, cat_id, idx, prio, f.get("note", "").strip(), place, lat, lng,
                       int(acc) if acc else None)
        if lat is not None or place:
            session["loc"] = {"lat": lat, "lng": lng, "place": place}
        db.session.commit()
        return redirect(url_for("tuku.ticket", tid=t.id))
    return render_template("tuku/raise.html", app="tuku", cat=cat, iss=iss, idx=idx, band=BANDS[iss["band"]],
                           place=_saved_loc()[2])


@tuku.get("/tickets")
@require("business")
def tickets():
    rows = db.session.scalars(db.select(Ticket).where(Ticket.business_id == g.user.id).order_by(Ticket.id.desc())).all()
    return render_template("tuku/tickets.html", app="tuku", tickets=rows)


@tuku.get("/tickets/<int:tid>")
@require("business")
def ticket(tid):
    t = _my_ticket(tid)
    return render_template("tuku/ticket.html", app="tuku", t=t, band=BANDS[t.band], cat=CATS[t.cat_id],
                           pay=mpesa_status(), payment=t.payments[0] if t.payments else None,
                           live_stamp=one_ticket_stamp(t))


@tuku.post("/tickets/<int:tid>/accept")
@require("business")
def accept(tid):
    t = _my_ticket(tid)
    if t.status in ("quote", "negotiating"):
        t.status, t.offer, t.offer_note = "accepted", None, ""
        db.session.commit()
    return redirect(url_for("tuku.ticket", tid=t.id, _anchor="pay"))


@tuku.post("/tickets/<int:tid>/offer")
@require("business")
def offer(tid):
    t = _my_ticket(tid)
    amount = parse_amount(request.form.get("offer"))
    if t.status not in ("quote", "negotiating"):
        flash("This ticket is past the price stage.", "error")
    elif not amount or amount >= t.fee:
        flash(f"Enter an amount below {t.fee:,} shillings.", "error")
    else:
        t.status, t.offer, t.offer_note = "negotiating", amount, request.form.get("note", "").strip()[:300]
        db.session.commit()
        flash("Offer sent. You will see the answer here as soon as the helpdesk replies.", "ok")
    return redirect(url_for("tuku.ticket", tid=t.id))


@tuku.post("/tickets/<int:tid>/pay")
@require("business")
def pay(tid):
    t = _my_ticket(tid)
    if t.status != "accepted":
        flash("Accept the price before paying.", "error")
        return redirect(url_for("tuku.ticket", tid=t.id))
    if t.open_payment:
        flash("A payment for this ticket is already being processed.", "error")
        return redirect(url_for("tuku.ticket", tid=t.id, _anchor="pay"))
    method = request.form.get("method")
    if method == "stk":
        if not mpesa.configured():
            flash("M-Pesa prompts are not switched on yet. Pay by till number instead.", "error")
        else:
            try:
                start_stk(t, request.form.get("phone", ""))
                db.session.commit()
                flash("Check your phone and enter your M-Pesa PIN on the prompt.", "ok")
            except mpesa.MpesaError as e:
                db.session.rollback()
                flash(str(e), "error")
    elif method == "till":
        code = request.form.get("code", "").strip().upper()
        if not (len(code) == 10 and code.isalnum()):
            flash("Enter the 10-character M-Pesa confirmation code, for example QJ61ABCD23.", "error")
        else:
            record_till(t, code)
            db.session.commit()
            flash("Code received. The helpdesk confirms it against the M-Pesa statement.", "ok")
    return redirect(url_for("tuku.ticket", tid=t.id, _anchor="pay"))


@tuku.get("/chat")
@require("business")
def chat():
    msgs = db.session.scalars(db.select(Message).where(Message.business_id == g.user.id).order_by(Message.id)).all()
    return render_template("tuku/chat.html", app="tuku", msgs=msgs, agent=business_agent(g.user),
                           thread=g.user.id)


@tuku.get("/shops")
def shops():
    lat, lng = request.args.get("lat", type=float), request.args.get("lng", type=float)
    place = request.args.get("place", "").strip()
    if lat is not None or place:
        session["loc"] = {"lat": lat, "lng": lng, "place": place}
    else:
        lat, lng, place = _saved_loc()
    cat = request.args.get("cat") if request.args.get("cat") in CATS else None
    return render_template("tuku/shops.html", app="tuku", shops=nearby_shops(lat, lng, place, cat=cat),
                           cat=cat, place=place, located=lat is not None)


# ====================== Salama ======================

def _assessment():
    a = g.user.assessment
    if not a:
        a = Assessment(business=g.user, size="S", answers={})
        db.session.add(a)
        db.session.flush()
    return a


@salama.get("/")
def overview():
    if g.role != "business":
        return render_template("salama/landing.html", app="salama")
    a = _assessment()
    db.session.commit()
    return render_template("salama/overview.html", app="salama", a=a, r=summary(a.size, a.answers))


@salama.post("/size")
@require("business")
def size():
    a = _assessment()
    if request.form.get("size") in ("S", "L"):
        a.size = request.form["size"]
    db.session.commit()
    return redirect(url_for("salama.overview"))


@salama.route("/check", methods=["GET", "POST"])
@require("business")
def check():
    a = _assessment()
    qs = questions(a.size)
    if request.method == "POST":
        i = max(0, min(request.form.get("i", 0, type=int), len(qs) - 1))
        ans = request.form.get("answer")
        if ans in ("yes", "partly", "no", "unsure", "na"):
            a.answers = {**a.answers, qs[i]["key"]: ans}
            db.session.commit()
        if request.form.get("go") == "back":
            return redirect(url_for("salama.check", i=max(0, i - 1)))
        if i + 1 >= len(qs):
            return redirect(url_for("salama.results"))
        return redirect(url_for("salama.check", i=i + 1))
    if "i" in request.args:
        i = request.args.get("i", 0, type=int)
    elif "sec" in request.args:
        i = next((n for n, q in enumerate(qs) if q["sec"]["id"] == request.args["sec"]), 0)
    else:  # continue at the first unanswered question
        i = next((n for n, q in enumerate(qs) if not a.answers.get(q["key"])), 0)
    i = max(0, min(i, len(qs) - 1))
    db.session.commit()
    return render_template("salama/check.html", app="salama", q=qs[i], i=i, total=len(qs), a=a,
                           answered=sum(1 for q in qs if a.answers.get(q["key"])))


@salama.get("/results")
@require("business")
def results():
    a = _assessment()
    db.session.commit()
    return render_template("salama/results.html", app="salama", a=a, r=summary(a.size, a.answers))


@salama.get("/results.csv")
@require("business")
def results_csv():
    a = _assessment()
    r = summary(a.size, a.answers)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Business", g.user.name])
    w.writerow(["Profile", "Large company" if a.size == "L" else "Small business"])
    w.writerow(["Overall score", r["overall"] if r["overall"] is not None else "", r["level"][0]])
    w.writerow([])
    w.writerow(["Section", "Question", "Essential", "Answer", "Action if not in place"])
    from .content import LABEL
    for q in r["qs"]:
        w.writerow([q["sec"]["name"], q["text"], "yes" if q["weight"] == 2 else "",
                    LABEL.get(a.answers.get(q["key"]), "Not answered"), q["action"]])
    db.session.commit()
    name = "".join(ch if ch.isalnum() else "-" for ch in g.user.name).strip("-") or "business"
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="salama-{name}.csv"'})


@salama.post("/reset")
@require("business")
def reset():
    a = _assessment()
    a.answers = {}
    db.session.commit()
    flash("Your answers were cleared.", "ok")
    return redirect(url_for("salama.overview"))
