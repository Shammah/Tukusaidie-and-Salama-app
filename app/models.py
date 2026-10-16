from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.security import check_password_hash, generate_password_hash


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PasswordMixin:
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Business(PasswordMixin, db.Model):
    """A customer account, shared by Tukusaidie (helpdesk) and Salama (cyber check)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(10), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    tickets = db.relationship("Ticket", back_populates="business", cascade="all, delete-orphan")
    assessment = db.relationship("Assessment", back_populates="business", uselist=False, cascade="all, delete-orphan")


class Admin(PasswordMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)


class Agent(PasswordMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False, index=True)
    specs = db.Column(db.JSON, default=list, nullable=False)
    certs = db.Column(db.JSON, default=list, nullable=False)
    cert_ref = db.Column(db.String(200), default="")
    status = db.Column(db.String(12), default="pending", nullable=False)  # pending, approved, suspended
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @property
    def code(self):
        return f"AG-{self.id}"


class Shop(PasswordMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(10), unique=True, nullable=False, index=True)
    town = db.Column(db.String(80), nullable=False)
    address = db.Column(db.String(160), default="")
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    cats = db.Column(db.JSON, default=list, nullable=False)
    status = db.Column(db.String(12), default="pending", nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    services = db.relationship("ShopService", back_populates="shop", cascade="all, delete-orphan", order_by="ShopService.id")
    records = db.relationship("ShopRecord", back_populates="shop", cascade="all, delete-orphan", order_by="ShopRecord.id.desc()")


class ShopService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shop.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    cat = db.Column(db.String(12), nullable=False)
    price = db.Column(db.Integer)
    shop = db.relationship("Shop", back_populates="services")


class ShopRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shop.id"), nullable=False)
    customer = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), default="")
    service = db.Column(db.String(120), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    shop = db.relationship("Shop", back_populates="records")


class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("business.id"), nullable=False, index=True)
    cat_id = db.Column(db.String(12), nullable=False)
    issue_idx = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    priority = db.Column(db.String(10), default="Normal", nullable=False)
    band = db.Column(db.Integer, nullable=False)
    fee = db.Column(db.Integer, nullable=False)
    note = db.Column(db.Text, default="")
    place = db.Column(db.String(160), default="")
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    accuracy = db.Column(db.Integer)
    status = db.Column(db.String(12), default="quote", nullable=False)  # quote, negotiating, accepted, paid
    offer = db.Column(db.Integer)
    offer_note = db.Column(db.String(300), default="")
    agent_id = db.Column(db.Integer, db.ForeignKey("agent.id"))
    work = db.Column(db.String(12))  # assigned, in progress, resolved
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    business = db.relationship("Business", back_populates="tickets")
    agent = db.relationship("Agent")
    payments = db.relationship("Payment", back_populates="ticket", cascade="all, delete-orphan", order_by="Payment.id.desc()")

    @property
    def code(self):
        return f"IT-{1000 + self.id}"

    @property
    def paid_payment(self):
        return next((p for p in self.payments if p.status == "success"), None)

    @property
    def open_payment(self):
        return next((p for p in self.payments if p.status in ("pending", "review")), None)


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("ticket.id"), nullable=False, index=True)
    method = db.Column(db.String(6), nullable=False)  # stk or till
    phone = db.Column(db.String(12), default="")
    amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(10), default="pending", nullable=False)  # pending, review, success, failed
    merchant_request_id = db.Column(db.String(64))
    checkout_request_id = db.Column(db.String(64), unique=True, index=True)
    receipt = db.Column(db.String(32))
    result_desc = db.Column(db.String(255), default="")
    simulated = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    ticket = db.relationship("Ticket", back_populates="payments")


class Message(db.Model):
    """Chat between a business and the helpdesk (its agent or the admin)."""
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("business.id"), nullable=False, index=True)
    sender = db.Column(db.String(8), nullable=False)  # customer, agent, admin
    sender_name = db.Column(db.String(120), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    business = db.relationship("Business")


class Assessment(db.Model):
    """Salama cyber-check answers for one business."""
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("business.id"), unique=True, nullable=False)
    size = db.Column(db.String(1), default="S", nullable=False)  # S small business, L large company
    answers = db.Column(db.JSON, default=dict, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    business = db.relationship("Business", back_populates="assessment")
