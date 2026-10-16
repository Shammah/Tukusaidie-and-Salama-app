"""Staff side: service agents, computer shops and the owner's admin."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func

from . import auth
from .auth import require
from .content import BANDS, CATS, CERTS, summary
from .models import Agent, Assessment, Business, Message, Payment, Shop, ShopRecord, ShopService, Ticket, db
from .services import (EMAIL_RE, PHONE_RE, agent_business_ids, assign, auto_assign, eligible_agents, mpesa_status,
                       norm_phone, open_count, parse_amount, settle)

agent_bp = Blueprint("agent", __name__, url_prefix="/agent")
shop_bp = Blueprint("shop", __name__, url_prefix="/shop")
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _threads(business_ids=None):
    """Conversations, newest first, as (business, last message)."""
    q = db.select(Message.business_id, func.max(Message.id)).group_by(Message.business_id)
    if business_ids is not None:
        q = q.where(Message.business_id.in_(business_ids or [-1]))
    rows = db.session.execute(q).all()
    out = []
    for bid, mid in sorted(rows, key=lambda r: -r[1]):
        out.append((db.session.get(Business, bid), db.session.get(Message, mid)))
    return out


def _messages(bid):
    return db.session.scalars(db.select(Message).where(Message.business_id == bid).order_by(Message.id)).all()


# ====================== agents ======================

@agent_bp.route("/register", methods=["GET", "POST"], endpoint="register")
def agent_register():
    form = {"name": "", "email": "", "specs": [], "certs": [], "other": "", "cert_ref": ""}
    if request.method == "POST":
        f = request.form
        form = {"name": f.get("name", "").strip(), "email": f.get("email", "").strip().lower(),
                "specs": [s for s in f.getlist("specs") if s in CATS],
                "certs": [c for c in f.getlist("certs") if c in CERTS],
                "other": f.get("other", "").strip(), "cert_ref": f.get("cert_ref", "").strip()}
        pw = f.get("password", "")
        certs = form["certs"] + ([form["other"][:120]] if form["other"] else [])
        err = None
        if not form["name"]:
            err = "Enter your full name."
        elif not EMAIL_RE.match(form["email"]):
            err = "Enter your email address."
        elif db.session.scalar(db.select(Agent).where(Agent.email == form["email"])):
            err = "An agent account with this email already exists. Sign in instead."
        elif len(pw) < 8:
            err = "Choose a password of at least 8 characters."
        elif not form["specs"]:
            err = "Pick at least one speciality."
        elif not certs:
            err = "A certification is required. Tick one or type it in."
        elif not form["cert_ref"]:
            err = "Enter the certificate number or the institution that issued it."
        if err:
            flash(err, "error")
        else:
            a = Agent(name=form["name"][:120], email=form["email"], specs=form["specs"], certs=certs,
                      cert_ref=form["cert_ref"][:200])
            a.set_password(pw)
            db.session.add(a)
            db.session.commit()
            auth.login("agent", a)
            return redirect(url_for("agent.dashboard"))
    return render_template("agent/register.html", app="tuku", form=form)


@agent_bp.route("/login", methods=["GET", "POST"], endpoint="login")
def agent_login():
    email = ""
    if request.method == "POST":
        if auth.throttled():
            auth.throttle_message()
        else:
            email = request.form.get("email", "").strip().lower()
            a = db.session.scalar(db.select(Agent).where(Agent.email == email))
            if a and a.check_password(request.form.get("password", "")):
                auth.login("agent", a)
                return redirect(auth.safe_next(url_for("agent.dashboard")))
            flash("Email or password is not right.", "error")
    return render_template("staff_login.html", app="tuku", who="agent", title="Agent sign in", ident=email)


@agent_bp.post("/logout", endpoint="logout")
def agent_logout():
    auth.logout()
    return redirect(url_for("tuku.home"))


@agent_bp.get("/", endpoint="dashboard")
@require("agent")
def agent_dashboard():
    a = g.user
    tab = request.args.get("tab", "tickets")
    mine = db.session.scalars(db.select(Ticket).where(Ticket.agent_id == a.id).order_by(Ticket.id.desc())).all()
    biz_ids = agent_business_ids(a)
    threads = _threads(biz_ids)
    cur = request.args.get("b", type=int)
    if cur not in biz_ids:
        cur = threads[0][0].id if threads else (next(iter(biz_ids)) if biz_ids else None)
    return render_template("agent/dashboard.html", app="tuku", a=a, tab=tab, mine=mine,
                           open_n=sum(1 for t in mine if t.work != "resolved"), threads=threads,
                           cur=db.session.get(Business, cur) if cur else None,
                           msgs=_messages(cur) if cur else [], biz_list=[db.session.get(Business, b) for b in biz_ids])


@agent_bp.post("/tickets/<int:tid>/work", endpoint="work")
@require("agent")
def agent_work(tid):
    t = db.session.get(Ticket, tid)
    if not t or t.agent_id != g.user.id or g.user.status != "approved":
        abort(404)
    step = request.form.get("step")
    if step == "start" and t.work == "assigned":
        t.work = "in progress"
    elif step == "done" and t.work == "in progress":
        t.work = "resolved"
    elif step == "reopen" and t.work == "resolved":
        t.work = "in progress"
    db.session.commit()
    return redirect(url_for("agent.dashboard"))


# ====================== computer shops ======================

def _shop_form():
    f = request.form
    return {"name": f.get("name", "").strip(), "phone": norm_phone(f.get("phone")), "town": f.get("town", "").strip(),
            "address": f.get("address", "").strip(), "cats": [c for c in f.getlist("cats") if c in CATS]}


@shop_bp.route("/register", methods=["GET", "POST"], endpoint="register")
def shop_register():
    form = {"name": "", "phone": "", "town": "", "address": "", "cats": []}
    if request.method == "POST":
        form = _shop_form()
        pw = request.form.get("password", "")
        err = None
        if not form["name"]:
            err = "Enter the shop name."
        elif not PHONE_RE.match(form["phone"]):
            err = "Enter a Kenyan mobile number, for example 0712 345 678."
        elif db.session.scalar(db.select(Shop).where(Shop.phone == form["phone"])):
            err = "A shop with this number is already registered. Sign in instead."
        elif len(pw) < 8:
            err = "Choose a password of at least 8 characters."
        elif not form["town"]:
            err = "Enter the town your shop is in."
        elif not form["cats"]:
            err = "Pick at least one service you offer."
        if err:
            flash(err, "error")
        else:
            s = Shop(name=form["name"][:120], phone=form["phone"], town=form["town"][:80],
                     address=form["address"][:160], cats=form["cats"])
            s.set_password(pw)
            db.session.add(s)
            db.session.commit()
            auth.login("shop", s)
            return redirect(url_for("shop.dashboard"))
    return render_template("shop/register.html", app="tuku", form=form)


@shop_bp.route("/login", methods=["GET", "POST"], endpoint="login")
def shop_login():
    phone = ""
    if request.method == "POST":
        if auth.throttled():
            auth.throttle_message()
        else:
            phone = norm_phone(request.form.get("phone"))
            s = db.session.scalar(db.select(Shop).where(Shop.phone == phone))
            if s and s.check_password(request.form.get("password", "")):
                auth.login("shop", s)
                return redirect(auth.safe_next(url_for("shop.dashboard")))
            flash("Phone number or password is not right.", "error")
    return render_template("staff_login.html", app="tuku", who="shop", title="Shop sign in", ident=phone)


@shop_bp.post("/logout", endpoint="logout")
def shop_logout():
    auth.logout()
    return redirect(url_for("tuku.home"))


@shop_bp.get("/", endpoint="dashboard")
@require("shop")
def shop_dashboard():
    s = g.user
    return render_template("shop/dashboard.html", app="tuku", s=s, tab=request.args.get("tab", "services"),
                           total=sum(r.amount for r in s.records))


@shop_bp.post("/cats", endpoint="cats")
@require("shop")
def shop_cats():
    chosen = [c for c in request.form.getlist("cats") if c in CATS]
    if chosen:
        g.user.cats = chosen
        db.session.commit()
        flash("Saved.", "ok")
    else:
        flash("Pick at least one service you offer.", "error")
    return redirect(url_for("shop.dashboard"))


@shop_bp.post("/services", endpoint="add_service")
@require("shop")
def shop_add_service():
    name, cat = request.form.get("name", "").strip(), request.form.get("cat")
    price = parse_amount(request.form.get("price")) if request.form.get("price", "").strip() else None
    if not name:
        flash("Enter the service name.", "error")
    elif cat not in CATS:
        flash("Pick the problem area.", "error")
    elif request.form.get("price", "").strip() and price is None:
        flash("Enter the price as a number, or leave it empty.", "error")
    else:
        db.session.add(ShopService(shop=g.user, name=name[:120], cat=cat, price=price))
        db.session.commit()
        flash("Service added.", "ok")
    return redirect(url_for("shop.dashboard"))


@shop_bp.post("/services/<int:sid>/delete", endpoint="delete_service")
@require("shop")
def shop_delete_service(sid):
    item = db.session.get(ShopService, sid)
    if item and item.shop_id == g.user.id:
        db.session.delete(item)
        db.session.commit()
    return redirect(url_for("shop.dashboard"))


@shop_bp.post("/records", endpoint="add_record")
@require("shop")
def shop_add_record():
    f = request.form
    amount = parse_amount(f.get("amount"))
    if not f.get("customer", "").strip():
        flash("Enter the customer name.", "error")
    elif amount is None:
        flash("Enter the amount charged.", "error")
    else:
        db.session.add(ShopRecord(shop=g.user, customer=f["customer"].strip()[:120], phone=f.get("phone", "").strip()[:20],
                                  service=(f.get("service") or "Other")[:120], amount=amount))
        db.session.commit()
        flash("Record saved.", "ok")
    return redirect(url_for("shop.dashboard", tab="records"))


@shop_bp.post("/profile", endpoint="profile")
@require("shop")
def shop_profile():
    s, f = g.user, request.form
    if not f.get("town", "").strip():
        flash("Enter the town your shop is in.", "error")
        return redirect(url_for("shop.dashboard", tab="profile"))
    s.town, s.address = f["town"].strip()[:80], f.get("address", "").strip()[:160]
    lat, lng = f.get("lat", type=float), f.get("lng", type=float)
    if lat is not None and lng is not None and -90 <= lat <= 90 and -180 <= lng <= 180:
        s.lat, s.lng = round(lat, 5), round(lng, 5)
    if f.get("clear_loc"):
        s.lat = s.lng = None
    db.session.commit()
    flash("Shop details saved.", "ok")
    return redirect(url_for("shop.dashboard", tab="profile"))


# ====================== admin ======================

@admin_bp.route("/login", methods=["GET", "POST"], endpoint="login")
def admin_login():
    email = ""
    if request.method == "POST":
        if auth.throttled():
            auth.throttle_message()
        else:
            from .models import Admin
            email = request.form.get("email", "").strip().lower()
            a = db.session.scalar(db.select(Admin).where(Admin.email == email))
            if a and a.check_password(request.form.get("password", "")):
                auth.login("admin", a)
                return redirect(auth.safe_next(url_for("admin.dashboard")))
            flash("Email or password is not right.", "error")
    from .models import Admin
    has_admin = db.session.scalar(db.select(func.count(Admin.id))) > 0
    return render_template("staff_login.html", app="tuku", who="admin", title="Admin sign in", ident=email,
                           has_admin=has_admin)


@admin_bp.post("/logout", endpoint="logout")
def admin_logout():
    auth.logout()
    return redirect(url_for("customer.index"))


@admin_bp.get("/", endpoint="dashboard")
@require("admin")
def admin_dashboard():
    T = db.session.scalars(db.select(Ticket)).all()
    stats = {
        "tickets": len(T),
        "quoted": sum(t.fee for t in T),
        "paid": sum(t.fee for t in T if t.status == "paid"),
        "negotiating": sum(1 for t in T if t.status == "negotiating"),
        "unassigned": sum(1 for t in T if not t.agent_id),
        "businesses": db.session.scalar(db.select(func.count(Business.id))),
    }
    review = db.session.scalars(db.select(Payment).where(Payment.status == "review").order_by(Payment.id)).all()
    pending_agents = db.session.scalars(db.select(Agent).where(Agent.status == "pending")).all()
    pending_shops = db.session.scalars(db.select(Shop).where(Shop.status == "pending")).all()
    nego = [t for t in T if t.status == "negotiating"]
    recent = sorted(T, key=lambda t: t.id, reverse=True)[:6]
    return render_template("admin/dashboard.html", app="tuku", tab="home", stats=stats, review=review,
                           pending_agents=pending_agents, pending_shops=pending_shops, nego=nego, recent=recent,
                           pay=mpesa_status())


@admin_bp.get("/tickets", endpoint="tickets")
@require("admin")
def admin_tickets():
    f = request.args.get("status", "all")
    q = db.select(Ticket).order_by(Ticket.id.desc())
    if f == "unassigned":
        q = q.where(Ticket.agent_id.is_(None))
    elif f != "all":
        q = q.where(Ticket.status == f)
    return render_template("admin/tickets.html", app="tuku", tab="tickets", f=f, tickets=db.session.scalars(q).all())


@admin_bp.get("/tickets/<int:tid>", endpoint="ticket")
@require("admin")
def admin_ticket(tid):
    t = db.session.get(Ticket, tid) or abort(404)
    agents = db.session.scalars(db.select(Agent).where(Agent.status == "approved").order_by(Agent.name)).all()
    return render_template("admin/ticket.html", app="tuku", tab="tickets", t=t, band=BANDS[t.band],
                           agents=agents, eligible={a.id for a in eligible_agents(t)},
                           load={a.id: open_count(a.id) for a in agents}, confirm=request.args.get("confirm"))


@admin_bp.post("/tickets/<int:tid>/<action>", endpoint="ticket_action")
@require("admin")
def admin_ticket_action(tid, action):
    t = db.session.get(Ticket, tid) or abort(404)
    if action == "price":
        fee = parse_amount(request.form.get("fee"))
        if fee is None or fee < 1:
            flash("Enter the price as a whole number of shillings.", "error")
        elif t.status == "paid":
            flash("This ticket is already paid. The price can no longer change.", "error")
        elif t.open_payment:
            flash("A payment is in progress. Wait for it to finish first.", "error")
        else:
            t.fee = fee
            if t.status in ("accepted", "negotiating"):
                t.status, t.offer = "quote", None  # customer must agree to the new price (a counter-offer)
            flash(f"Price for {t.code} set to KSh {fee:,}.", "ok")
    elif action == "assign":
        aid = request.form.get("agent", type=int)
        agent = db.session.get(Agent, aid) if aid else None
        if agent and agent.status != "approved":
            flash("Only approved agents can take tickets.", "error")
        else:
            assign(t, agent)
            flash(f"{t.code} assigned to {agent.name}." if agent else f"{t.code} unassigned.", "ok")
    elif action == "offer-accept" and t.status == "negotiating":
        t.fee, t.status, t.offer = t.offer, "accepted", None
        flash(f"Offer accepted. {t.business.name} can now pay KSh {t.fee:,}.", "ok")
    elif action == "offer-keep" and t.status == "negotiating":
        t.status, t.offer = "quote", None
        flash(f"Price kept at KSh {t.fee:,}. The customer is asked to accept or offer again.", "ok")
    elif action in ("payment-ok", "payment-reject"):
        p = db.session.get(Payment, request.form.get("payment", type=int))
        if p and p.ticket_id == t.id and p.status == "review":
            if action == "payment-ok":
                settle(p, True, "Confirmed by admin against the M-Pesa statement")
                flash(f"Payment {p.receipt} confirmed. {t.code} is paid.", "ok")
            else:
                settle(p, False, "The admin could not find this code on the M-Pesa statement")
                flash(f"Code {p.receipt} rejected. The customer can pay again.", "ok")
    elif action == "delete":
        if request.form.get("confirm") != t.code:
            flash(f"Type {t.code} to confirm the delete.", "error")
            return redirect(url_for("admin.ticket", tid=t.id, confirm=1))
        db.session.delete(t)
        db.session.commit()
        flash(f"{t.code} deleted.", "ok")
        return redirect(url_for("admin.tickets"))
    db.session.commit()
    return redirect(request.form.get("back") or url_for("admin.ticket", tid=t.id))


@admin_bp.post("/tickets/auto-assign", endpoint="auto_assign_all")
@require("admin")
def admin_auto_assign_all():
    n = 0
    for t in db.session.scalars(db.select(Ticket).where(Ticket.agent_id.is_(None))).all():
        if auto_assign(t):
            n += 1
        db.session.flush()
    db.session.commit()
    flash(f"{n} ticket{'s' if n != 1 else ''} assigned." if n else "No unassigned ticket has a matching approved agent.",
          "ok" if n else "error")
    return redirect(url_for("admin.tickets"))


@admin_bp.get("/agents", endpoint="agents")
@require("admin")
def admin_agents():
    rows = db.session.scalars(db.select(Agent).order_by(Agent.status != "pending", Agent.name)).all()
    return render_template("admin/agents.html", app="tuku", tab="agents", agents=rows,
                           load={a.id: open_count(a.id) for a in rows}, confirm=request.args.get("confirm", type=int))


@admin_bp.post("/agents/<int:aid>/<action>", endpoint="agent_action")
@require("admin")
def admin_agent_action(aid, action):
    a = db.session.get(Agent, aid) or abort(404)
    if action == "approve":
        a.status = "approved"
        db.session.flush()
        n = sum(1 for t in db.session.scalars(db.select(Ticket).where(Ticket.agent_id.is_(None))).all()
                if t.cat_id in a.specs and auto_assign(t))
        flash(f"{a.name} approved." + (f" {n} waiting ticket{'s' if n != 1 else ''} assigned." if n else ""), "ok")
    elif action == "suspend":
        a.status = "suspended"
        for t in db.session.scalars(db.select(Ticket).where(Ticket.agent_id == a.id, Ticket.work != "resolved")):
            assign(t, None)
            db.session.flush()
            auto_assign(t)
        flash(f"{a.name} suspended. Their open tickets were reassigned where possible.", "ok")
    elif action == "delete":
        if request.form.get("confirm") != "yes":
            return redirect(url_for("admin.agents", confirm=a.id))
        for t in db.session.scalars(db.select(Ticket).where(Ticket.agent_id == a.id)):
            assign(t, None)
        db.session.delete(a)
        flash(f"{a.name} removed.", "ok")
    db.session.commit()
    return redirect(url_for("admin.agents"))


@admin_bp.get("/shops", endpoint="shops")
@require("admin")
def admin_shops():
    rows = db.session.scalars(db.select(Shop).order_by(Shop.status != "pending", Shop.name)).all()
    return render_template("admin/shops.html", app="tuku", tab="shops", shops=rows,
                           confirm=request.args.get("confirm", type=int))


@admin_bp.post("/shops/<int:sid>/<action>", endpoint="shop_action")
@require("admin")
def admin_shop_action(sid, action):
    s = db.session.get(Shop, sid) or abort(404)
    if action == "approve":
        s.status = "approved"
        flash(f"{s.name} approved and now visible to customers.", "ok")
    elif action == "suspend":
        s.status = "suspended"
        flash(f"{s.name} hidden from customers.", "ok")
    elif action == "delete":
        if request.form.get("confirm") != "yes":
            return redirect(url_for("admin.shops", confirm=s.id))
        db.session.delete(s)
        flash(f"{s.name} removed.", "ok")
    db.session.commit()
    return redirect(url_for("admin.shops"))


@admin_bp.get("/chats", endpoint="chats")
@require("admin")
def admin_chats():
    threads = _threads()
    cur = request.args.get("b", type=int) or (threads[0][0].id if threads else None)
    biz = db.session.get(Business, cur) if cur else None
    return render_template("admin/chats.html", app="tuku", tab="chats", threads=threads, cur=biz,
                           msgs=_messages(biz.id) if biz else [])


@admin_bp.get("/businesses", endpoint="businesses")
@require("admin")
def admin_businesses():
    rows = db.session.scalars(db.select(Business).order_by(Business.id.desc())).all()
    cur = db.session.get(Business, request.args.get("b", type=int)) if request.args.get("b") else None
    r = summary(cur.assessment.size, cur.assessment.answers) if cur and cur.assessment else None
    return render_template("admin/businesses.html", app="tuku", tab="businesses", rows=rows, cur=cur, r=r,
                           summaries={b.id: summary(b.assessment.size, b.assessment.answers)
                                      for b in rows if b.assessment and b.assessment.answers})


@admin_bp.post("/businesses/<int:bid>/password", endpoint="reset_password")
@require("admin")
def admin_reset_password(bid):
    b = db.session.get(Business, bid) or abort(404)
    pw = request.form.get("password", "")
    if len(pw) < 8:
        flash("Choose a password of at least 8 characters.", "error")
    else:
        b.set_password(pw)
        db.session.commit()
        flash(f"New password set for {b.name}. Share it with them privately.", "ok")
    return redirect(url_for("admin.businesses", b=bid))


@admin_bp.post("/businesses/<int:bid>/clear-answers", endpoint="clear_answers")
@require("admin")
def admin_clear_answers(bid):
    a = db.session.scalar(db.select(Assessment).where(Assessment.business_id == bid))
    if a:
        a.answers = {}
        db.session.commit()
        flash("Cyber-check answers cleared.", "ok")
    return redirect(url_for("admin.businesses", b=bid))
