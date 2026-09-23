import os
import base64
import hmac
import hashlib
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

premium_users = set()


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
            metadata={"user_id": req.user_id},
            return_url="https://fixmistiq.netlify.app",
        )
        return {"checkout_url": session.checkout_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def verify_webhook(body: bytes, headers: dict, secret: str) -> bool:
    """Verify Dodo (Standard Webhooks) signature."""
    msg_id = headers.get("webhook-id", "")
    msg_timestamp = headers.get("webhook-timestamp", "")
    msg_signature = headers.get("webhook-signature", "")

    if not (msg_id and msg_timestamp and msg_signature):
        print("MISSING HEADERS")
        return False

    # Secret is base64-encoded and often prefixed with whsec_
    secret_clean = secret.replace("whsec_", "")
    try:
        key = base64.b64decode(secret_clean)
    except Exception:
        key = secret_clean.encode()

    signed_content = f"{msg_id}.{msg_timestamp}.".encode() + body
    expected = base64.b64encode(
        hmac.new(key, signed_content, hashlib.sha256).digest()
    ).decode()

    # Signature header contains space-separated entries like "v1,<base64>"
    for sig_entry in msg_signature.split(" "):
        parts = sig_entry.split(",")
        if len(parts) == 2 and parts[0] == "v1":
            if hmac.compare_digest(parts[1], expected):
                return True
    return False


@app.post("/dodo-webhook")
async def dodo_webhook(request: Request):
    body = await request.body()
    headers = dict(request.headers)

    if not verify_webhook(body, headers, DODO_WEBHOOK_SECRET):
        print("SIGNATURE VERIFICATION FAILED")
        print("HEADERS:", {k: v for k, v in headers.items() if "webhook" in k.lower()})
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = await request.json()
    event = payload.get("type", "")
    print(f"WEBHOOK OK: {event}")

    data = payload.get("data", {})
    user_id = (
        data.get("metadata", {}).get("user_id")
        or data.get("subscription", {}).get("metadata", {}).get("user_id")
        or data.get("payment", {}).get("metadata", {}).get("user_id")
    )

    if event in ("subscription.active", "subscription.created", "payment.succeeded"):
        if user_id:
            premium_users.add(user_id)
            print(f"PREMIUM ACTIVATED: {user_id}")

    return {"status": "ok"}


@app.get("/is-premium/{user_id}")
def is_premium(user_id: str):
    return {"user_id": user_id, "premium": user_id in premium_users}
