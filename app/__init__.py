import hashlib
import os
from datetime import timedelta
from pathlib import Path

import click
from flask import Flask, g, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth, content
from .models import Admin, db
from .services import ksh, map_link

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    """Read KEY=VALUE lines from .env (local development only; Render sets real env vars)."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _db_url():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        (ROOT / "instance").mkdir(exist_ok=True)
        return f"sqlite:///{ROOT / 'instance' / 'tukusaidie.db'}"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def create_app(test_config=None):
    _load_dotenv()
    env = os.environ.get("APP_ENV", "development")
    secret = os.environ.get("SECRET_KEY") or ("dev-only-secret" if env != "production" else None)
    if not secret:
        raise RuntimeError("Set SECRET_KEY before running in production.")
    mpesa_env = os.environ.get("MPESA_ENV", "simulate" if env != "production" else "sandbox")
    if env == "production" and mpesa_env == "simulate":
        raise RuntimeError("MPESA_ENV=simulate is not allowed when APP_ENV=production.")

    app = Flask(__name__)
    app.config.update(
        APP_ENV=env,
        SECRET_KEY=secret,
        SQLALCHEMY_DATABASE_URI=_db_url(),
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=env == "production",
        PUBLIC_URL=os.environ.get("PUBLIC_URL", os.environ.get("RENDER_EXTERNAL_URL", "")),
        ADMIN_EMAIL=os.environ.get("ADMIN_EMAIL", ""),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", ""),
        MPESA_ENV=mpesa_env,
        MPESA_CONSUMER_KEY=os.environ.get("MPESA_CONSUMER_KEY", ""),
        MPESA_CONSUMER_SECRET=os.environ.get("MPESA_CONSUMER_SECRET", ""),
        MPESA_SHORTCODE=os.environ.get("MPESA_SHORTCODE", ""),
        MPESA_PASSKEY=os.environ.get("MPESA_PASSKEY", ""),
        MPESA_TILL=os.environ.get("MPESA_TILL", ""),
        MPESA_TRANSACTION_TYPE=os.environ.get("MPESA_TRANSACTION_TYPE", "CustomerPayBillOnline"),
        MPESA_CALLBACK_URL=os.environ.get("MPESA_CALLBACK_URL", ""),
        MPESA_CALLBACK_TOKEN=os.environ.get("MPESA_CALLBACK_TOKEN")
        or hashlib.sha256(f"mpesa-callback:{secret}".encode()).hexdigest()[:32],
    )
    if test_config:
        app.config.update(test_config)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)

    from .views_customer import bp as customer_bp, tuku, salama
    from .views_staff import agent_bp, shop_bp, admin_bp
    from .views_api import bp as api_bp
    for bp in (customer_bp, tuku, salama, agent_bp, shop_bp, admin_bp, api_bp):
        app.register_blueprint(bp)

    app.before_request(auth.load_user)
    app.before_request(auth.check_csrf)

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")
        return resp

    app.jinja_env.globals.update(csrf_token=auth.csrf_token, C=content, ksh=ksh, map_link=map_link, app_env=env)

    @app.context_processor
    def inject():
        return {"me": g.get("user"), "role": g.get("role")}

    @app.template_filter("local_phone")
    def local_phone(p):
        return "0" + p[3:] if p and p.startswith("254") else p

    @app.template_filter("when")
    def when(dt):
        if not dt:
            return ""
        eat = dt + timedelta(hours=3)  # stored as UTC, shown in Kenya time
        return eat.strftime("%d %b %Y, %H:%M")

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(500)
    def error(e):
        code = getattr(e, "code", 500)
        if request.path.startswith("/api/"):
            return {"error": getattr(e, "description", "Error")}, code
        return render_template("error.html", app="portal", code=code, message=getattr(e, "description", "")), code

    with app.app_context():
        db.create_all()
        _bootstrap_admin(app)

    _register_cli(app)
    return app


def _bootstrap_admin(app):
    email, pw = app.config["ADMIN_EMAIL"].strip().lower(), app.config["ADMIN_PASSWORD"]
    if not (email and pw):
        return
    admin = db.session.scalar(db.select(Admin).where(Admin.email == email))
    if not admin:
        admin = Admin(email=email)
        admin.set_password(pw)
        db.session.add(admin)
        db.session.commit()
    elif not admin.check_password(pw):
        admin.set_password(pw)  # ADMIN_PASSWORD is the source of truth; change it there
        db.session.commit()


def _register_cli(app):
    @app.cli.command("create-admin")
    @click.argument("email")
    @click.password_option()
    def create_admin(email, password):
        """Create or reset an admin sign-in."""
        email = email.strip().lower()
        admin = db.session.scalar(db.select(Admin).where(Admin.email == email)) or Admin(email=email)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        click.echo(f"Admin {email} is ready.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Add sample agents, shops, businesses and tickets for trying the app."""
        from .demo import seed
        click.echo(seed())
