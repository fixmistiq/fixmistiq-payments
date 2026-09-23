import os
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


@app.post("/dodo-webhook")
async def dodo_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Dodo-Signature", "")
    expected = hmac.new(DODO_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")
    payload = await request.json()
    if payload.get("type") == "subscription.active":
        user_id = payload.get("data", {}).get("metadata", {}).get("user_id")
        if user_id:
            premium_users.add(user_id)
    return {"status": "ok"}


@app.get("/is-premium/{user_id}")
def is_premium(user_id: str):
    return {"user_id": user_id, "premium": user_id in premium_users}
