import os
import base64
import hmac
import hashlib
import secrets
import string
import urllib.parse
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from dodopayments import DodoPayments
import requests as http_requests
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DODO_API_KEY = os.getenv("DODO_API_KEY", "")
DODO_WEBHOOK_SECRET = os.getenv("DODO_WEBHOOK_SECRET", "")
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
BACKEND_PUBLIC = "https://fixmistiq-payments.onrender.com"

client = DodoPayments(bearer_token=DODO_API_KEY, environment="live_mode")

PRODUCTS = {
    "monthly": "pdt_0NoMqLzu3vxH2v2LIPFCr",
    "yearly":  "pdt_0NoMr7xnnZPD9VTakR93n",
}

_memory_store = {}

def _redis_headers():
    return {"Authorization": f"Bearer {UPSTASH_TOKEN}"}


def redis_set(key: str, value: str, ttl: int = None):
    if not (UPSTASH_URL and UPSTASH_TOKEN):
        _memory_store[key] = value
        return
    try:
        safe_key = urllib.parse.quote(key, safe="")
        safe_value = urllib.parse.quote(value, safe="")
        url = f"{UPSTASH_URL}/set/{safe_key}/{safe_value}"
        if ttl:
            url += f"/ex/{ttl}"
        r = http_requests.get(url, headers=_redis_headers(), timeout=10)
        print(f"REDIS SET {key} -> {r.status_code} {r.text[:100]}")
    except Exception as e:
        print("redis set error:", e)


def redis_get(key: str):
    if not (UPSTASH_URL and UPSTASH_TOKEN):
        return _memory_store.get(key)
    try:
        safe_key = urllib.parse.quote(key, safe="")
        r = http_requests.get(f"{UPSTASH_URL}/get/{safe_key}", headers=_redis_headers(), timeout=10)
        data = r.json()
        print(f"REDIS GET {key} -> {data}")
        return data.get("result")
    except Exception as e:
        print("redis get error:", e)
        return None


def redis_del(key: str):
    if not (UPSTASH_URL and UPSTASH_TOKEN):
        _memory_store.pop(key, None)
        return
    try:
        safe_key = urllib.parse.quote(key, safe="")
        http_requests.get(f"{UPSTASH_URL}/del/{safe_key}", headers=_redis_headers(), timeout=10)
    except Exception as e:
        print("redis del error:", e)


class CreateOrderRequest(BaseModel):
    plan: str
    user_id: str


class VerifyCodeRequest(BaseModel):
    code: str


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "Fixmistiq Payments",
        "redis": bool(UPSTASH_URL),
        "google": bool(GOOGLE_CLIENT_ID),
    }


@app.get("/auth/google/start")
def google_start():
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{BACKEND_PUBLIC}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@app.get("/auth/google/callback")
def google_callback(code: str = None, error: str = None):
    if error or not code:
        return HTMLResponse(
            f"<html><body style='background:#0A0A1F;color:#FF8FA3;font-family:sans-serif;text-align:center;padding:60px;'>"
            f"<h1>Sign-in failed</h1><p>{error or 'No code received'}</p></body></html>",
            status_code=400,
        )
    try:
        token_resp = http_requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": f"{BACKEND_PUBLIC}/auth/google/callback",
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        tokens = token_resp.json()
        id_token_str = tokens.get("id_token")
        if not id_token_str:
            raise Exception(f"No id_token. Response: {tokens}")

        idinfo = id_token.verify_oauth2_token(
            id_token_str, google_requests.Request(), GOOGLE_CLIENT_ID
        )
        google_sub = idinfo.get("sub")
        email = idinfo.get("email", "")
        if not google_sub:
            raise Exception("No sub in id token")

        short_code = "".join(
            secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6)
        )
        redis_set(f"signin_code:{short_code}", f"{google_sub}|{email}", ttl=600)

        html = f"""
        <html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
        <body style="font-family:-apple-system,sans-serif;background:#0A0A1F;color:#F2EFFF;
                     text-align:center;padding:60px 20px;margin:0;">
            <h1 style="color:#8FE3B0;font-size:28px;margin:0 0 20px;">✓ Signed in!</h1>
            <p style="font-size:16px;color:#B9B4D8;margin:0 0 30px;">
                Your Fixmistiq sign-in code is:
            </p>
            <div style="font-size:42px;font-weight:800;letter-spacing:8px;
                        color:#FFD79A;background:#17182E;padding:32px 24px;
                        border-radius:16px;margin:0 auto 30px;max-width:360px;
                        font-family:monospace;">{short_code}</div>
            <p style="font-size:15px;color:#B9B4D8;margin:0 0 12px;">
                Return to the Fixmistiq app and paste this code.
            </p>
            <p style="font-size:13px;color:#7A7699;margin:0;">
                Code expires in 10 minutes.
            </p>
        </body></html>
        """
        return HTMLResponse(html)

    except Exception as e:
        return HTMLResponse(
            f"<html><body style='background:#0A0A1F;color:#FF8FA3;font-family:sans-serif;text-align:center;padding:60px;'>"
            f"<h1>Sign-in error</h1><p>{str(e)[:300]}</p></body></html>",
            status_code=500,
        )


@app.post("/auth/verify-code")
async def verify_code(req: VerifyCodeRequest):
    code = (req.code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Empty code")
    stored = redis_get(f"signin_code:{code}")
    if not stored:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    parts = stored.split("|", 1)
    google_sub = parts[0]
    email = parts[1] if len(parts) > 1 else ""

    redis_del(f"signin_code:{code}")

    expiry_str = redis_get(f"premium:{google_sub}")
    premium = False
    expires_on = None
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str)
            if datetime.now() < expiry:
                premium = True
                expires_on = expiry.isoformat()
        except Exception:
            pass

    return {
        "google_sub": google_sub,
        "email": email,
        "premium": premium,
        "expires_on": expires_on,
    }


@app.get("/user/premium/{google_sub}")
def user_premium(google_sub: str):
    expiry_str = redis_get(f"premium:{google_sub}")
    if not expiry_str:
        return {"premium": False}
    try:
        expiry = datetime.fromisoformat(expiry_str)
        if datetime.now() >= expiry:
            redis_del(f"premium:{google_sub}")
            return {"premium": False, "reason": "expired"}
        return {"premium": True, "expires_on": expiry.isoformat()}
    except Exception:
        return {"premium": False}


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


def _activate_premium(key: str, plan: str = "monthly"):
    days = 365 if plan == "yearly" else 30
    expiry = datetime.now() + timedelta(days=days)
    redis_set(f"premium:{key}", expiry.isoformat())
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
    expiry_str = redis_get(f"premium:{user_id}")
    if not expiry_str:
        return {"user_id": user_id, "premium": False}
    try:
        expiry = datetime.fromisoformat(expiry_str)
        if datetime.now() >= expiry:
            redis_del(f"premium:{user_id}")
            return {"user_id": user_id, "premium": False, "reason": "expired"}
        return {"user_id": user_id, "premium": True, "expires_on": expiry.isoformat()}
    except Exception:
        return {"user_id": user_id, "premium": False}

