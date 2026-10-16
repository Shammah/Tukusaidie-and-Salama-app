"""Sessions, role checks, CSRF protection and a small login throttle."""
import secrets
import time
from collections import defaultdict, deque
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for

from .models import Admin, Agent, Business, Shop, db

ROLES = {"business": Business, "agent": Agent, "shop": Shop, "admin": Admin}
LOGIN_PAGE = {"business": "customer.login", "agent": "agent.login", "shop": "shop.login", "admin": "admin.login"}


def login(role, user):
    session.clear()
    session.permanent = True
    session["role"] = role
    session["uid"] = user.id
    session["csrf"] = secrets.token_urlsafe(32)


def logout():
    session.clear()


def load_user():
    g.role, g.user = None, None
    role, uid = session.get("role"), session.get("uid")
    if role in ROLES and uid:
        user = db.session.get(ROLES[role], uid)
        if user:
            g.role, g.user = role, user
        else:
            session.clear()


def require(role):
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if g.role != role:
                if request.path.startswith("/api/"):
                    abort(401)
                return redirect(url_for(LOGIN_PAGE[role], next=request.full_path if request.method == "GET" else None))
            return view(*a, **kw)
        return wrapped
    return deco


def safe_next(default):
    nxt = request.args.get("next") or request.form.get("next") or ""
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt.rstrip("?")
    return default


# ---------- CSRF ----------

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.endpoint in ("api.mpesa_callback", "static"):
        return
    sent = request.form.get("csrf") or request.headers.get("X-CSRF")
    if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
        abort(400, "Your session expired. Go back, refresh the page and try again.")


# ---------- login throttle ----------

_attempts = defaultdict(deque)
WINDOW, LIMIT = 300, 10


def throttled():
    """True when this IP has tried to sign in too often in the last five minutes."""
    key = (request.remote_addr, request.endpoint)
    q = _attempts[key]
    now = time.time()
    while q and q[0] < now - WINDOW:
        q.popleft()
    if len(q) >= LIMIT:
        return True
    q.append(now)
    return False


def throttle_message():
    flash("Too many attempts. Wait five minutes and try again.", "error")
