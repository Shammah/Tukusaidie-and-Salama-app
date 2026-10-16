# Tukusaidie and Salama

Two web apps for Kenyan businesses, served by one Python (Flask) server with a shared database and one customer account.

- **Tukusaidie** (`/tukusaidie`): an IT helpdesk. Free step-by-step fixes for 72 common problems in 18 service areas. Customers raise a ticket, see the price, accept it or make an offer, and pay with an M-Pesa prompt (Daraja STK push) or a till/paybill code. Certified agents get tickets in their specialities automatically and chat with the customer live. Computer shops list their services and appear to customers nearest first.
- **Salama** (`/salama`): a cybersecurity self-check with 44 questions for small businesses and 64 for large companies, covering M-Pesa safety and data protection. It gives a risk level per section and a ranked fix list, and the answers can be downloaded as CSV.
- **Admin** (`/admin`): the owner's side. Tickets, prices, counter-offers, till-code confirmation, agent and shop approval, every chat, and each business's cyber-check answers.

Everything is live. Chat, payment status and dashboards update by themselves every few seconds.

## Run it on your computer

```bash
py -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env
.venv\Scripts\flask --app wsgi seed-demo
.venv\Scripts\python wsgi.py
```

Then open http://127.0.0.1:5000. Locally the data lives in `instance/tukusaidie.db` (SQLite) and M-Pesa runs in `simulate` mode, where prompts complete by themselves and no money moves. Sample accounts and their password are described in `app/demo.py`. The admin sign-in comes from `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `.env`.

Run the tests with `.venv\Scripts\python -m pytest`.

## Put it online (Render)

1. Push this repo to GitHub.
2. In Render choose **New > Blueprint** and pick the repo. `render.yaml` creates the web service and a Postgres database.
3. When asked, fill in `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `MPESA_CONSUMER_KEY` and `MPESA_CONSUMER_SECRET`.
4. Open the `.onrender.com` address. Render sets `RENDER_EXTERNAL_URL`, which the app uses as its public address for M-Pesa callbacks.

The free web service sleeps when idle, so the first visit after a while takes up to a minute. Check Render's current limits on free databases before relying on one for real customers.

## M-Pesa (Daraja)

| `MPESA_ENV` | What happens |
|---|---|
| `simulate` | No API calls. For local testing only; refused when `APP_ENV=production`. |
| `sandbox` | Safaricom's test system. Needs a sandbox app's consumer key and secret from developer.safaricom.co.ke. Shortcode 174379 and the public test passkey are filled in automatically. |
| `production` | Real money. Needs your production app keys, `MPESA_SHORTCODE` and `MPESA_PASSKEY`. Set `MPESA_TRANSACTION_TYPE=CustomerBuyGoodsOnline` and `MPESA_TILL` for a till, or leave the paybill default. |

How a payment completes:
- **Prompt (STK push).** Safaricom posts the result to `/mpesa/callback/<secret>`, and the payment counts only if the amount covers the price. If no callback arrives (for example on a local machine), the app asks Daraja for the status after 20 seconds.
- **Till or paybill code.** The customer types the code, and the admin confirms it against the M-Pesa statement.

## Layout

```
app/
  __init__.py       app factory, config from environment, CLI (create-admin, seed-demo)
  models.py         businesses, agents, shops, tickets, payments, messages, assessments
  services.py       assignment, payments, distances, live-update stamps
  mpesa.py          Daraja client: token, STK push, status query, callback parsing
  views_customer.py accounts, Tukusaidie, Salama
  views_staff.py    agents, shops, admin
  views_api.py      JSON for live chat, ticket status and dashboards, M-Pesa callback
  content.json      helpdesk services, price bands and cyber-check questions
  templates/, static/
tests/test_app.py   end-to-end tests of every role
render.yaml         Render blueprint
```
