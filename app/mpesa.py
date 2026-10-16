"""M-Pesa Daraja client: STK push (Lipa na M-Pesa Online), status query and callback parsing.

Modes, set with MPESA_ENV:
  sandbox     Safaricom's test environment (needs a Daraja sandbox app's consumer key and secret)
  production  real money (needs your production app, shortcode and passkey)
  simulate    no API calls; payments succeed after a few seconds. Refused when APP_ENV=production.
"""
import base64
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from flask import current_app

BASE = {"sandbox": "https://sandbox.safaricom.co.ke", "production": "https://api.safaricom.co.ke"}
SANDBOX_SHORTCODE = "174379"
SANDBOX_PASSKEY = "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"
EAT = timezone(timedelta(hours=3))


class MpesaError(Exception):
    pass


_token = {"value": None, "expires": 0.0}
_lock = threading.Lock()


def mode():
    return current_app.config["MPESA_ENV"]


def _cfg(key):
    return current_app.config.get(key) or ""


def shortcode():
    return _cfg("MPESA_SHORTCODE") or (SANDBOX_SHORTCODE if mode() == "sandbox" else "")


def passkey():
    return _cfg("MPESA_PASSKEY") or (SANDBOX_PASSKEY if mode() == "sandbox" else "")


def till_number():
    """The number customers pay into by hand (Buy Goods till or Paybill)."""
    return _cfg("MPESA_TILL") or shortcode()


def configured():
    if mode() == "simulate":
        return True
    return bool(_cfg("MPESA_CONSUMER_KEY") and _cfg("MPESA_CONSUMER_SECRET") and shortcode() and passkey()
                and callback_url())


def callback_url():
    explicit = _cfg("MPESA_CALLBACK_URL")
    if explicit:
        return explicit
    public = _cfg("PUBLIC_URL").rstrip("/")
    if not public.startswith("https://"):
        return ""  # Safaricom only calls public https URLs
    return f"{public}/mpesa/callback/{_cfg('MPESA_CALLBACK_TOKEN')}"


def to_msisdn(phone):
    """0712345678 / +254712345678 / 254712345678 -> 254712345678"""
    p = phone.replace(" ", "").replace("-", "").lstrip("+")
    if p.startswith("0"):
        p = "254" + p[1:]
    if not (len(p) == 12 and p.startswith(("2547", "2541")) and p.isdigit()):
        raise MpesaError("Enter a Safaricom number, for example 0712 345 678.")
    return p


def _access_token():
    with _lock:
        if _token["value"] and time.time() < _token["expires"]:
            return _token["value"]
        try:
            r = requests.get(f"{BASE[mode()]}/oauth/v1/generate", params={"grant_type": "client_credentials"},
                             auth=(_cfg("MPESA_CONSUMER_KEY"), _cfg("MPESA_CONSUMER_SECRET")), timeout=15)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            current_app.logger.warning("Daraja token request failed: %s", e)
            raise MpesaError("Could not reach M-Pesa right now. Try again in a minute.") from e
        _token["value"] = data["access_token"]
        _token["expires"] = time.time() + int(data.get("expires_in", 3599)) - 60
        return _token["value"]


def _password():
    ts = datetime.now(EAT).strftime("%Y%m%d%H%M%S")
    return base64.b64encode(f"{shortcode()}{passkey()}{ts}".encode()).decode(), ts


def _post(path, body):
    try:
        r = requests.post(f"{BASE[mode()]}{path}", json=body, timeout=30,
                          headers={"Authorization": f"Bearer {_access_token()}"})
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        current_app.logger.warning("Daraja %s failed: %s", path, e)
        raise MpesaError("Could not reach M-Pesa right now. Try again in a minute.") from e
    return r.status_code, data


def stk_push(phone, amount, reference, description):
    """Send the PIN prompt. Returns (merchant_request_id, checkout_request_id)."""
    msisdn = to_msisdn(phone)
    if mode() == "simulate":
        stamp = f"{int(time.time() * 1000)}"
        return f"SIM-M-{stamp}", f"SIM-C-{stamp}"
    password, ts = _password()
    buy_goods = _cfg("MPESA_TRANSACTION_TYPE") == "CustomerBuyGoodsOnline"
    status, data = _post("/mpesa/stkpush/v1/processrequest", {
        "BusinessShortCode": shortcode(),
        "Password": password,
        "Timestamp": ts,
        "TransactionType": "CustomerBuyGoodsOnline" if buy_goods else "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": msisdn,
        "PartyB": _cfg("MPESA_TILL") if buy_goods and _cfg("MPESA_TILL") else shortcode(),
        "PhoneNumber": msisdn,
        "CallBackURL": callback_url(),
        "AccountReference": reference[:12],
        "TransactionDesc": description[:13],
    })
    if status != 200 or str(data.get("ResponseCode")) != "0":
        current_app.logger.warning("STK push refused: %s %s", status, data)
        raise MpesaError(data.get("errorMessage") or data.get("CustomerMessage") or "M-Pesa did not accept the request.")
    return data["MerchantRequestID"], data["CheckoutRequestID"]


def stk_query(checkout_request_id):
    """Ask Daraja for the result of a prompt. Returns (result_code or None while pending, description)."""
    password, ts = _password()
    status, data = _post("/mpesa/stkpushquery/v1/query", {
        "BusinessShortCode": shortcode(), "Password": password, "Timestamp": ts,
        "CheckoutRequestID": checkout_request_id,
    })
    if "ResultCode" in data:
        return int(data["ResultCode"]), data.get("ResultDesc", "")
    return None, data.get("errorMessage", "Still waiting")  # e.g. "The transaction is being processed"


def parse_callback(payload):
    """Pull the useful fields out of Safaricom's stkCallback body."""
    cb = payload["Body"]["stkCallback"]
    items = {i["Name"]: i.get("Value") for i in cb.get("CallbackMetadata", {}).get("Item", [])}
    return {
        "merchant_request_id": cb.get("MerchantRequestID"),
        "checkout_request_id": cb["CheckoutRequestID"],
        "result_code": int(cb["ResultCode"]),
        "result_desc": cb.get("ResultDesc", ""),
        "amount": items.get("Amount"),
        "receipt": items.get("MpesaReceiptNumber"),
        "phone": str(items.get("PhoneNumber") or ""),
    }
