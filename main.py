import os
import hmac
import hashlib
import razorpay
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

PLANS = {
    "monthly": {"amount": 9900, "label": "Rs 99 / month"},
    "yearly":  {"amount": 99900, "label": "Rs 999 / year"},
}

client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

premium_users = set()


class CreateOrderRequest(BaseModel):
    plan: str
    user_id: str


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    user_id: str


@app.get("/")
def root():
    return {"status": "ok", "service": "Fixmistiq Payments"}


@app.post("/create-order")
async def create_order(req: CreateOrderRequest):
    if req.plan not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan")

    amount = PLANS[req.plan]["amount"]

    try:
        order = client.order.create({
            "amount": amount,
            "currency": "INR",
            "receipt": f"fixmistiq_{req.user_id}_{req.plan}",
            "notes": {"user_id": req.user_id, "plan": req.plan},
        })
        return {
            "order_id": order["id"],
            "amount": amount,
            "currency": "INR",
            "key_id": RAZORPAY_KEY_ID,
            "plan_label": PLANS[req.plan]["label"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/verify-payment")
async def verify_payment(req: VerifyPaymentRequest):
    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": req.razorpay_order_id,
            "razorpay_payment_id": req.razorpay_payment_id,
            "razorpay_signature": req.razorpay_signature,
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    premium_users.add(req.user_id)
    return {"status": "premium_activated", "user_id": req.user_id}


@app.get("/is-premium/{user_id}")
def is_premium(user_id: str):
    return {"user_id": user_id, "premium": user_id in premium_users}


@app.post("/razorpay-webhook")
async def razorpay_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    payload = await request.json()
    if payload.get("event") == "payment.captured":
        notes = payload["payload"]["payment"]["entity"].get("notes", {})
        user_id = notes.get("user_id")
        if user_id:
            premium_users.add(user_id)

    return {"status": "ok"}
    class PaymentLinkRequest(BaseModel):
    plan: str
    user_id: str


@app.post("/create-payment-link")
async def create_payment_link(req: PaymentLinkRequest):
    if req.plan not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan")
    try:
        link = client.payment_link.create({
            "amount": PLANS[req.plan]["amount"],
            "currency": "INR",
            "description": f"Fixmistiq Premium - {req.plan}",
            "notes": {"user_id": req.user_id, "plan": req.plan},
            "notify": {"email": False, "sms": False},
            "reminder_enable": False,
            "callback_url": "https://fixmistiq.netlify.app",
            "callback_method": "get",
        })
        return {"payment_link": link["short_url"], "link_id": link["id"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
