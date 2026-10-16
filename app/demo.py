"""Sample data for trying the app (`flask --app wsgi seed-demo`). Every sample name starts with "Sample:"."""
import os

from .content import questions
from .models import Agent, Assessment, Business, Message, Shop, ShopRecord, ShopService, db
from .services import auto_assign, new_ticket

DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "demo-pass-2026")


def _pw(obj):
    obj.set_password(DEMO_PASSWORD)
    return obj


def seed():
    if db.session.scalar(db.select(Business).where(Business.phone == "0700000001")):
        return "Sample data is already there."
    for i, (name, specs, certs, status) in enumerate([
        ("Sample: Achieng Otieno", ["print", "pc", "hw", "power"], ["CompTIA A+"], "approved"),
        ("Sample: Brian Mwangi", ["sec", "net", "rtr", "ncfg", "fw", "mail", "files"], ["CompTIA Security+", "Cisco CCNA"], "approved"),
        ("Sample: Faith Chebet", ["apps", "mobile", "web"], ["Google IT Support Certificate"], "pending"),
    ], 1):
        db.session.add(_pw(Agent(name=name, email=f"agent{i}@example.com", specs=specs, certs=certs,
                                 cert_ref="Sample reference", status=status)))
    shops = [
        ("Sample: Kisii Tech Hub", "0711000001", "Kisii", "Hospital Road", (-0.6817, 34.7667), ["pc", "print", "hw", "power", "files", "apps"],
         [("Laptop servicing and dust cleaning", "pc", 1500), ("Laptop screen replacement", "hw", 4500), ("Printer repair", "print", 1200),
          ("UPS battery replacement", "power", 3000), ("Data recovery", "files", 2500)], "approved"),
        ("Sample: Ouru Computers", "0711000002", "Kisii", "Ouru Plaza", (-0.679, 34.771), ["pc", "apps", "mobile", "sec"],
         [("Virus removal", "sec", 800), ("Windows reinstall", "pc", 1000), ("Phone screen repair", "mobile", 2500)], "approved"),
        ("Sample: Nyamira PC Clinic", "0711000003", "Nyamira", "", (-0.5669, 34.9341), ["pc", "print", "hw"],
         [("Keyboard replacement", "hw", 2000), ("Printer servicing", "print", 800)], "pending"),
        ("Sample: Kisumu Network Solutions", "0711000004", "Kisumu", "Oginga Odinga Street", (-0.0917, 34.768),
         ["net", "rtr", "ncfg", "fw", "cctv", "srv"], [("Router setup", "rtr", 1500), ("Firewall installation", "fw", 8000)], "approved"),
    ]
    for name, phone, town, addr, (lat, lng), cats, items, status in shops:
        s = _pw(Shop(name=name, phone=phone, town=town, address=addr, lat=lat, lng=lng, cats=cats, status=status))
        s.services = [ShopService(name=n, cat=c, price=p) for n, c, p in items]
        db.session.add(s)
        if phone == "0711000001":
            s.records = [ShopRecord(customer="Sample customer", service="Printer repair", amount=1200)]
    db.session.flush()

    b1 = _pw(Business(name="Sample: Nyanza Agrovet", phone="0700000001"))
    b2 = _pw(Business(name="Sample: Lakeside Logistics Ltd", phone="0700000002"))
    db.session.add_all([b1, b2])
    db.session.flush()
    t1 = new_ticket(b1, "print", 1, "Normal", "Paper keeps jamming on page 2.", "Kisii town", -0.68, 34.77, 30)
    t2 = new_ticket(b2, "pc", 2, "Urgent", "Three machines show the same fault.", "Kisumu", None, None, None)
    t2.status, t2.offer, t2.offer_note = "negotiating", round(t2.fee * 0.7), "Three machines have the same fault"
    t3 = new_ticket(b1, "mail", 0, "Low", "", "", None, None, None)
    t3.status = "accepted"
    for t in (t1, t2, t3):
        if not t.agent_id:
            auto_assign(t)
    db.session.add(Message(business_id=b1.id, sender="customer", sender_name=b1.name,
                           text="Hello, the printer is still jamming after I tried the steps."))
    pattern = ["yes", "yes", "partly", "no", "yes", "unsure", "yes", "no", "yes", "partly"]
    for b, size, off in ((b1, "S", 0), (b2, "L", 3)):
        db.session.add(Assessment(business=b, size=size,
                                  answers={q["key"]: pattern[(i * 7 + off) % len(pattern)] for i, q in enumerate(questions(size))}))
    db.session.commit()
    return ("Sample data added. Customers sign in with 0700000001 or 0700000002, agents with agent1@example.com "
            "or agent2@example.com, shops with 0711000001. All use the DEMO_PASSWORD (see app/demo.py).")
