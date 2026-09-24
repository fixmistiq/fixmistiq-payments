import os
import base64
import hmac
import hashlib
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dodopayments import DodoPayments

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DODO_API_KEY = os.getenv("DODO_API_KEY", "")
DODO_WEBHOOK_SECRET = os.getenv("DODO_WEBHOOK_SECRET", "")

client = DodoPayments(bearer_token=DODO_API_KEY, environment="test_mode")

PRODUCTS = {
    "monthly": "pdt_0NoD4EBNQUYi66KottxTZ",
    "yearly":  "pdt_0NoD6xghw1bAcxmZEGqbm",
}

# {user_id: expiry_iso_string}
premium_users = {}


class CreateOrderRequest(BaseModel):
    plan: str
    user_id: str


@app.get("/")
def root():
    return {"status": "ok", "service": "Fixmistiq Payments"}


@app.post("/create-checkout")
async def create_checkout(req: CreateOrderRequest):
    if req.plan not in PRODUCTS:
        raise HTTPException(status_code=400, detail="Invalid plan")
    try:
        session = client.checkout_sessions.create(
            product_cart=[{"product_id": PRODUCTS[req.plan], "quantity": 1}],
            metadata={"user_id": req.user_id, "plan": req.plan},
            return_url="https://fixmistiq.netlify.app",
        )
        return {"checkout_url": session.checkout_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def verify_webhook(body: bytes, headers: dict, secret: str) -> bool:
    msg_id = headers.get("webhook-id", "")
    msg_timestamp = headers.get("webhook-timestamp", "")
    msg_signature = headers.get("webhook-signature", "")
    if not (msg_id and msg_timestamp and msg_signature):
        return False
    secret_clean = secret.replace("whsec_", "")
    try:
        key = base64.b64decode(secret_clean)
    except Exception:
        key = secret_clean.encode()
    signed_content = f"{msg_id}.{msg_timestamp}.".encode() + body
    expected = base64.b64encode(
        hmac.new(key, signed_content, hashlib.sha256).digest()
    ).decode()
    for sig_entry in msg_signature.split(" "):
        parts = sig_entry.split(",")
        if len(parts) == 2 and parts[0] == "v1":
            if hmac.compare_digest(parts[1], expected):
                return True
    return False


def _activate_premium(user_id: str, plan: str = "monthly"):
    days = 365 if plan == "yearly" else 30
    expiry = datetime.now() + timedelta(days=days)
    premium_users[user_id] = expiry.isoformat()
    return expiry


@app.post("/dodo-webhook")
async def dodo_webhook(request: Request):
    body = await request.body()
    headers = dict(request.headers)
    if not verify_webhook(body, headers, DODO_WEBHOOK_SECRET):
        raise HTTPException(status_code=400, detail="Invalid signature")
    payload = await request.json()
    event = payload.get("type", "")
    data = payload.get("data", {})
    metadata = data.get("metadata", {}) or {}
    user_id = metadata.get("user_id")
    plan = metadata.get("plan", "monthly")
    if event in ("subscription.active", "subscription.created", "payment.succeeded"):
        if user_id:
            _activate_premium(user_id, plan)
            print(f"PREMIUM ACTIVATED: {user_id} (plan={plan})")
    return {"status": "ok"}


@app.get("/is-premium/{user_id}")
def is_premium(user_id: str):
    expiry_str = premium_users.get(user_id)
    if not expiry_str:
        return {"user_id": user_id, "premium": False}
    try:
        expiry = datetime.fromisoformat(expiry_str)
        if datetime.now() >= expiry:
            premium_users.pop(user_id, None)
            return {"user_id": user_id, "premium": False, "reason": "expired"}
        return {"user_id": user_id, "premium": True, "expires_on": expiry.isoformat()}
    except Exception:
        return {"user_id": user_id, "premium": False}


# ----- TEST ENDPOINTS (remove before going live) -----
@app.post("/test-activate/{user_id}")
def test_activate(user_id: str, days: int = 1, plan: str = "monthly"):
    expiry = _activate_premium(user_id, plan)
    return {"user_id": user_id, "premium": True, "expires_on": expiry.isoformat()}


@app.post("/test-deactivate/{user_id}")
def test_deactivate(user_id: str):
    premium_users.pop(user_id, None)
    return {"user_id": user_id, "premium": False}
