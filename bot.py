#!/usr/bin/env python3
"""
Complete Telegram Bot for AI Image Generation with Plisio Crypto Payments
"""

import os
import asyncio
import sqlite3
import datetime
import time
import json
import base64
import secrets
import hmac
import hashlib
import httpx
from pathlib import Path
from fastapi import FastAPI, Request
from io import BytesIO
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ========== FEATURE FLAGS ==========
ENABLE_QUOTA_SYSTEM = True  # Set to True for production with paid credits
FREE_GENERATIONS_PER_DAY = 2  # Only applies if ENABLE_QUOTA_SYSTEM is True

# ========== CONFIGURATION ==========
TOKEN = os.getenv("TELEGRAM_KEY")
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TOKEN}"

# RunPod Configuration
ENDPOINT_ID = os.getenv("RUNPOD_ENDPOINT_ID")
API_KEY = os.getenv("RUNPOD_API_KEY")
WORKFLOW_PATH = os.getenv("WORKFLOW_PATH")

# Plisio Configuration
PLISIO_API_KEY = os.getenv("PLISIO_API_KEY")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://svthbzs7s6ioem-8000.proxy.runpod.net")

# Payment Packages
PAYMENT_PACKAGES = [
    {"id": 1, "credits": 5, "price": 3, "label": "5 credits - $3"},
    {"id": 2, "credits": 10, "price": 6, "label": "10 credits - $6 ⭐ POPULAR"},
    {"id": 3, "credits": 15, "price": 8, "label": "15 credits - $8 💎 BEST VALUE"},
    {"id": 4, "credits": 20, "price": 10, "label": "20 credits - $10"},
    {"id": 5, "credits": 100, "price": 35, "label": "100 credits - $35"},
]

# Generation settings
POLL_INTERVAL = 3  # seconds
MAX_POLL_TIME = 300  # 5 minutes max wait
MAX_CONCURRENT_GENERATIONS = 1  # Limit per-user concurrent generations

# Database
DB = "users.db"

# FastAPI app
app = FastAPI()

# Track active generations
active_generations = set()
db_write_lock = asyncio.Lock()

# ========== HELPER FUNCTIONS ==========

def print_status(emoji, message):
    """Print formatted status message."""
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {emoji} {message}")

# ========== TELEGRAM FUNCTIONS ==========

async def send_message(chat_id, text, parse_mode=None, reply_markup=None):
    """Send message to Telegram user using httpx."""
    try:
        data = {
            "chat_id": chat_id,
            "text": text
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{TELEGRAM_API_BASE}/sendMessage",
                json=data
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        print_status("❌", f"Failed to send message: {e}")
        return None

async def send_photo(chat_id, photo_bytes, caption=None):
    """Send photo to Telegram user using httpx."""
    try:
        files = {
            "photo": ("image.png", BytesIO(photo_bytes), "image/png")
        }
        data = {
            "chat_id": str(chat_id)
        }
        if caption:
            data["caption"] = caption
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{TELEGRAM_API_BASE}/sendPhoto",
                data=data,
                files=files
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        print_status("❌", f"Failed to send photo: {e}")
        return None

async def answer_callback_query(callback_query_id, text=None, show_alert=False):
    """Answer callback query from inline buttons."""
    try:
        data = {
            "callback_query_id": callback_query_id
        }
        if text:
            data["text"] = text
        if show_alert:
            data["show_alert"] = show_alert
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{TELEGRAM_API_BASE}/answerCallbackQuery",
                json=data
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        print_status("❌", f"Failed to answer callback: {e}")
        return None

# ========== DATABASE FUNCTIONS ==========

def db_connect():
    """Create a SQLite connection with basic concurrency settings."""
    conn = sqlite3.connect(DB, timeout=30)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    return conn

def init_db():
    """Initialize database tables."""
    conn = db_connect()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            credits INTEGER DEFAULT 0,
            free_used INTEGER DEFAULT 0,
            last_reset TEXT,
            total_generated INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS generations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            prompt TEXT,
            status TEXT,
            job_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            error_message TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            order_id TEXT UNIQUE NOT NULL,
            txn_id TEXT,
            amount_usd REAL,
            credits INTEGER,
            status TEXT,
            payment_status TEXT,
            payment_currency TEXT,
            payment_amount TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    
    conn.commit()
    conn.close()
    print_status("✅", "Database initialized")

async def get_user(user_id):
    """Get user credits and free usage."""
    async with db_write_lock:
        conn = db_connect()
        cur = conn.cursor()
        
        cur.execute(
            "SELECT credits, free_used, last_reset FROM users WHERE user_id=?", 
            (user_id,)
        )
        row = cur.fetchone()
        
        today = datetime.date.today().isoformat()
        
        if not row:
            # New user
            cur.execute(
                "INSERT INTO users (user_id, credits, free_used, last_reset) VALUES (?, 0, 0, ?)", 
                (user_id, today)
            )
            conn.commit()
            conn.close()
            return 0, 0
        
        credits, free_used, last_reset = row
        
        # Reset daily free usage if it's a new day
        if last_reset != today:
            cur.execute(
                "UPDATE users SET free_used=0, last_reset=? WHERE user_id=?", 
                (today, user_id)
            )
            conn.commit()
            free_used = 0
        
        conn.close()
        return credits, free_used

async def update_user(user_id, credits=None, free_used=None, increment_generated=False):
    """Update user data."""
    async with db_write_lock:
        conn = db_connect()
        cur = conn.cursor()
        
        if credits is not None:
            cur.execute("UPDATE users SET credits=? WHERE user_id=?", (credits, user_id))
        
        if free_used is not None:
            cur.execute("UPDATE users SET free_used=? WHERE user_id=?", (free_used, user_id))
        
        if increment_generated:
            cur.execute(
                "UPDATE users SET total_generated = total_generated + 1 WHERE user_id=?", 
                (user_id,)
            )
        
        conn.commit()
        conn.close()

async def log_generation(user_id, prompt, job_id=None, status="pending"):
    """Log generation request to database."""
    async with db_write_lock:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO generations (user_id, prompt, job_id, status) VALUES (?, ?, ?, ?)",
            (user_id, prompt, job_id, status)
        )
        generation_id = cur.lastrowid
        conn.commit()
        conn.close()
        return generation_id

async def update_generation(generation_id, status=None, job_id=None, error_message=None, completed=False):
    """Update generation status."""
    async with db_write_lock:
        conn = db_connect()
        cur = conn.cursor()
        
        updates = []
        params = []
        
        if status:
            updates.append("status = ?")
            params.append(status)
        
        if job_id:
            updates.append("job_id = ?")
            params.append(job_id)
        
        if error_message:
            updates.append("error_message = ?")
            params.append(error_message)
        
        if completed:
            updates.append("completed_at = CURRENT_TIMESTAMP")
        
        if updates:
            query = f"UPDATE generations SET {', '.join(updates)} WHERE id = ?"
            params.append(generation_id)
            cur.execute(query, params)
            conn.commit()
        
        conn.close()

async def log_transaction(user_id, order_id, amount_usd, credits, txn_id=None):
    """Log payment transaction."""
    async with db_write_lock:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO transactions 
            (user_id, order_id, txn_id, amount_usd, credits, status, payment_status) 
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, order_id, txn_id, amount_usd, credits, "pending", "new")
        )
        transaction_id = cur.lastrowid
        conn.commit()
        conn.close()
        return transaction_id

async def update_transaction(order_id, status=None, payment_status=None, 
                            payment_currency=None, payment_amount=None, completed=False):
    """Update transaction status."""
    async with db_write_lock:
        conn = db_connect()
        cur = conn.cursor()
        
        updates = []
        params = []
        
        if status:
            updates.append("status = ?")
            params.append(status)
        
        if payment_status:
            updates.append("payment_status = ?")
            params.append(payment_status)
        
        if payment_currency:
            updates.append("payment_currency = ?")
            params.append(payment_currency)
        
        if payment_amount:
            updates.append("payment_amount = ?")
            params.append(payment_amount)
        
        if completed:
            updates.append("completed_at = CURRENT_TIMESTAMP")
        
        if updates:
            query = f"UPDATE transactions SET {', '.join(updates)} WHERE order_id = ?"
            params.append(order_id)
            cur.execute(query, params)
            conn.commit()
        
        conn.close()

async def get_transaction(order_id):
    """Get transaction by order ID."""
    async with db_write_lock:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, credits, amount_usd, status FROM transactions WHERE order_id=?",
            (order_id,)
        )
        row = cur.fetchone()
        conn.close()
        
        if row:
            return {
                "user_id": row[0],
                "credits": row[1],
                "amount_usd": row[2],
                "status": row[3]
            }
        return None

# ========== PLISIO FUNCTIONS ==========

async def create_plisio_invoice(user_id, amount_usd, credits, order_number):
    """Create Plisio payment invoice."""
    try:
        callback_url = f"{WEBHOOK_BASE_URL}/webhook/plisio?json=true"
        
        params = {
            "source_currency": "USD",
            "source_amount": amount_usd,
            "order_name": f"{credits}_credits",
            "order_number": order_number,
            "description": f"Purchase {credits} AI image generation credits",
            "callback_url": callback_url,
            "allowed_psys_cids": "USDT_TRX,DOGE,BTC,LTC",
            "email": f"user{user_id}@bot.telegram",
            "plugin": "TelegramBot",
            "version": "1.0",
            "api_key": PLISIO_API_KEY,
            "expire_min": 60
        }
        
        print_status("📤", f"Creating Plisio invoice for ${amount_usd} ({credits} credits)")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://api.plisio.net/api/v1/invoices/new",
                params=params
            )
            response.raise_for_status()
            data = response.json()
        
        if data.get("status") == "success":
            print_status("✅", f"Invoice created: {data['data']['txn_id']}")
            return {
                "success": True,
                "txn_id": data["data"]["txn_id"],
                "invoice_url": data["data"]["invoice_url"],
                "invoice_total_sum": data["data"].get("invoice_total_sum")
            }
        else:
            error_msg = data.get("data", {}).get("message", "Unknown error")
            print_status("❌", f"Plisio error: {error_msg}")
            return {
                "success": False,
                "error": error_msg
            }
    
    except Exception as e:
        print_status("❌", f"Failed to create invoice: {e}")
        return {
            "success": False,
            "error": str(e)
        }

def verify_plisio_callback(data):
    """Verify Plisio webhook signature.
    
    With ?json=true in callback URL, Plisio sends JSON and we must verify
    using the exact JSON serialization they used to generate the signature.
    """
    try:
        if "verify_hash" not in data:
            print_status("⚠️", "No verify_hash in callback")
            return False
        
        received_hash = data["verify_hash"]
        
        # Remove verify_hash from data for verification
        callback_data = {k: v for k, v in data.items() if k != "verify_hash"}
        
        # Plisio with json=true uses JSON.stringify on sorted object
        # We need to match their exact serialization
        # According to Plisio Node.js example:
        # const string = JSON.stringify(ordered);
        # where ordered is the data object without verify_hash
        
        # Sort keys and serialize without spaces (compact JSON)
        json_string = json.dumps(callback_data, separators=(',', ':'), sort_keys=True)
        
        # Calculate HMAC-SHA1
        calculated_hash = hmac.new(
            PLISIO_API_KEY.encode(),
            json_string.encode(),
            hashlib.sha1
        ).hexdigest()
        
        is_valid = calculated_hash == received_hash
        
        if not is_valid:
            print_status("⚠️", f"Signature verification failed")
            print_status("⚠️", f"Expected: {calculated_hash}")
            print_status("⚠️", f"Received: {received_hash}")
            print_status("⚠️", f"Data: {json_string[:200]}")
            
            # Try alternative: without sort_keys (natural order)
            json_string_alt = json.dumps(callback_data, separators=(',', ':'))
            calculated_hash_alt = hmac.new(
                PLISIO_API_KEY.encode(),
                json_string_alt.encode(),
                hashlib.sha1
            ).hexdigest()
            
            if calculated_hash_alt == received_hash:
                print_status("✅", "Signature valid with natural key order")
                return True
            
            print_status("⚠️", f"Alternative also failed: {calculated_hash_alt}")
        
        return is_valid
    
    except Exception as e:
        print_status("❌", f"Verification error: {e}")
        return False

# ========== COMFYUI FUNCTIONS ==========

def load_workflow():
    """Load ComfyUI workflow from file."""
    with open(WORKFLOW_PATH) as f:
        return json.load(f)

def randomize_seeds(workflow):
    """Randomize all seeds in workflow."""
    for node_data in workflow.values():
        inputs = node_data.get("inputs", {})
        if "seed" in inputs:
            inputs["seed"] = secrets.randbits(32)
        if "noise_seed" in inputs:
            inputs["noise_seed"] = secrets.randbits(32)
    
    return workflow

def update_prompt(workflow, prompt):
    """Update prompt in workflow (node 45)."""
    if "45" in workflow:
        workflow["45"]["inputs"]["string_a"] = prompt
    return workflow

async def submit_job(workflow):
    """Submit job to RunPod."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"https://api.runpod.ai/v2/{ENDPOINT_ID}/run",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={"input": workflow}
        )
        response.raise_for_status()
        return response.json()

async def poll_job(job_id, max_wait=MAX_POLL_TIME):
    """Poll job until complete."""
    start_time = time.time()
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        while time.time() - start_time < max_wait:
            try:
                response = await client.get(
                    f"https://api.runpod.ai/v2/{ENDPOINT_ID}/status/{job_id}",
                    headers={"Authorization": f"Bearer {API_KEY}"}
                )
                status = response.json()
                
                print_status("🔄", f"Job {job_id[:8]}... status: {status['status']}")
                
                if status["status"] == "COMPLETED":
                    return status
                
                if status["status"] in ("FAILED", "ERROR", "CANCELLED"):
                    raise RuntimeError(f"Job failed: {status.get('error', 'Unknown')}")
                
                await asyncio.sleep(POLL_INTERVAL)
                
            except httpx.HTTPError as e:
                print_status("⚠️", f"Poll error: {e}")
                await asyncio.sleep(POLL_INTERVAL)
    
    raise TimeoutError("Job timed out")

def extract_image(output):
    """Extract image bytes from output."""
    if not output or "images" not in output or not output["images"]:
        return None
    
    img_data = output["images"][0]
    return base64.b64decode(img_data["data"])

# ========== GENERATION HANDLER ==========

async def generate_and_send(chat_id, prompt, generation_id):
    """Main generation function - runs in background."""
    try:
        # Mark as active
        active_generations.add(chat_id)
        
        print_status("🎨", f"Starting generation for user {chat_id}")
        
        # Notify start
        await send_message(
            chat_id=chat_id,
            text="🎨 Generating your image...\n⏱️ This takes ~30-60 seconds"
        )
        
        # Load and prepare workflow
        print_status("📋", "Loading workflow")
        workflow = load_workflow()
        
        print_status("✏️", f"Setting prompt: {prompt[:50]}...")
        workflow = update_prompt(workflow, prompt)
        
        print_status("🎲", "Randomizing seeds")
        workflow = randomize_seeds(workflow)
        
        # Submit job
        print_status("📤", "Submitting to RunPod")
        job = await submit_job(workflow)
        job_id = job["id"]
        
        print_status("✅", f"Job submitted: {job_id}")
        await update_generation(generation_id, status="processing", job_id=job_id)
        
        # Update user
        await send_message(
            chat_id=chat_id,
            text=f"⏳ Job ID: `{job_id}`\nPolling for completion...",
            parse_mode="Markdown"
        )
        
        # Poll for result
        print_status("⏳", "Waiting for completion")
        result = await poll_job(job_id)
        
        # Extract image
        print_status("🖼️", "Extracting image")
        image_bytes = extract_image(result.get("output"))
        
        if not image_bytes:
            raise RuntimeError("No image in output")
        
        # Send to user
        print_status("📸", "Sending image to user")
        await send_photo(
            chat_id=chat_id,
            photo_bytes=image_bytes,
            caption=f"✅ Complete!\n\n{prompt[:100]}"
        )
        
        # Update database
        await update_generation(generation_id, status="completed", completed=True)
        await update_user(chat_id, increment_generated=True)
        
        print_status("✅", f"Generation complete for user {chat_id}")
        
    except Exception as e:
        print_status("❌", f"Generation failed: {e}")
        
        # Update database
        await update_generation(generation_id, status="failed", error_message=str(e), completed=True)
        
        # Notify user
        await send_message(
            chat_id=chat_id,
            text=f"❌ Generation failed: {str(e)[:300]}\n\nPlease try again or contact support."
        )
    
    finally:
        # Remove from active set
        active_generations.discard(chat_id)

# ========== FASTAPI ROUTES ==========

@app.on_event("startup")
async def startup():
    """Initialize app on startup."""
    init_db()
    print_status("🚀", "Bot started successfully")
    print_status("⚙️", f"Quota system: {'ENABLED' if ENABLE_QUOTA_SYSTEM else 'DISABLED (unlimited testing)'}")
    print_status("💳", f"Plisio integration: {'ENABLED' if PLISIO_API_KEY else 'DISABLED'}")
    print_status("🔗", f"Webhook URL: {WEBHOOK_BASE_URL}/webhook/plisio")

@app.get("/")
def health():
    """Health check endpoint."""
    return {
        "status": "up",
        "quota_enabled": ENABLE_QUOTA_SYSTEM,
        "plisio_enabled": bool(PLISIO_API_KEY),
        "active_generations": len(active_generations),
        "webhook_url": f"{WEBHOOK_BASE_URL}/webhook/plisio"
    }

@app.get("/stats")
def stats():
    """Get bot statistics."""
    conn = db_connect()
    cur = conn.cursor()
    
    # Get user count
    cur.execute("SELECT COUNT(*) FROM users")
    user_count = cur.fetchone()[0]
    
    # Get generation stats
    cur.execute("SELECT COUNT(*), status FROM generations GROUP BY status")
    gen_stats = dict(cur.fetchall())
    
    # Get transaction stats
    cur.execute("SELECT COUNT(*), status FROM transactions GROUP BY status")
    trans_stats = dict(cur.fetchall())
    
    conn.close()
    
    return {
        "users": user_count,
        "generations": gen_stats,
        "transactions": trans_stats,
        "active_generations": len(active_generations),
        "quota_enabled": ENABLE_QUOTA_SYSTEM
    }

@app.post("/webhook/plisio")
async def plisio_webhook(req: Request):
    """Handle Plisio payment callbacks."""
    try:
        data = await req.json()
        
        print_status("💳", f"Plisio callback received")
        
        # Verify webhook signature
        if not verify_plisio_callback(data):
            print_status("⚠️", "Invalid webhook signature!")
            return {"status": "error", "message": "Invalid signature"}
        
        # Extract and validate required data
        order_number = data.get("order_number")
        status = data.get("status")
        
        if not order_number:
            print_status("⚠️", "Missing order_number in webhook")
            return {"status": "error", "message": "Missing order_number"}
        
        if not status:
            print_status("⚠️", "Missing status in webhook")
            return {"status": "error", "message": "Missing status"}
        
        amount = data.get("amount")
        currency = data.get("currency")
        source_amount = data.get("source_amount")
        
        print_status("📊", f"Order: {order_number}, Status: {status}")
        
        # Handle non-completed statuses
        if status == "pending":
            async with db_write_lock:
                conn = db_connect()
                try:
                    cur = conn.cursor()
                    # Only update if not already completed to avoid overwriting
                    cur.execute(
                        "UPDATE transactions SET payment_status='pending' WHERE order_id=? AND status NOT IN ('completed', 'processing')",
                        (order_number,)
                    )
                    updated = cur.rowcount > 0
                    
                    cur.execute("SELECT user_id FROM transactions WHERE order_id=?", (order_number,))
                    row = cur.fetchone()
                    conn.commit()
                    
                    if row and updated:
                        user_id = row[0]
                        await send_message(user_id, "⏳ Payment detected! Waiting for blockchain confirmations...")
                finally:
                    conn.close()
            
            return {"status": "success", "message": "Pending noted"}
        
        elif status == "expired":
            async with db_write_lock:
                conn = db_connect()
                try:
                    cur = conn.cursor()
                    # Only expire if not already completed
                    cur.execute(
                        "UPDATE transactions SET status='expired', completed_at=CURRENT_TIMESTAMP WHERE order_id=? AND status NOT IN ('completed', 'processing')",
                        (order_number,)
                    )
                    updated = cur.rowcount > 0
                    
                    cur.execute("SELECT user_id FROM transactions WHERE order_id=?", (order_number,))
                    row = cur.fetchone()
                    conn.commit()
                    
                    if row and updated:
                        user_id = row[0]
                        await send_message(user_id, "⏰ Payment invoice expired. Use /buy to create a new one.")
                finally:
                    conn.close()
            
            return {"status": "success", "message": "Expired noted"}
        
        elif status in ("cancelled", "error"):
            async with db_write_lock:
                conn = db_connect()
                try:
                    cur = conn.cursor()
                    # Only fail if not already completed
                    cur.execute(
                        "UPDATE transactions SET status='failed', completed_at=CURRENT_TIMESTAMP WHERE order_id=? AND status NOT IN ('completed', 'processing')",
                        (order_number,)
                    )
                    updated = cur.rowcount > 0
                    
                    cur.execute("SELECT user_id FROM transactions WHERE order_id=?", (order_number,))
                    row = cur.fetchone()
                    conn.commit()
                    
                    if row and updated:
                        user_id = row[0]
                        await send_message(user_id, "❌ Payment failed or cancelled. Use /buy to try again.")
                finally:
                    conn.close()
            
            return {"status": "success", "message": "Failed noted"}
        
        elif status == "mismatch":
            async with db_write_lock:
                conn = db_connect()
                try:
                    cur = conn.cursor()
                    # Only mark mismatch if not already completed
                    cur.execute(
                        "UPDATE transactions SET status='mismatch', completed_at=CURRENT_TIMESTAMP WHERE order_id=? AND status NOT IN ('completed', 'processing')",
                        (order_number,)
                    )
                    updated = cur.rowcount > 0
                    
                    cur.execute("SELECT user_id FROM transactions WHERE order_id=?", (order_number,))
                    row = cur.fetchone()
                    conn.commit()
                    
                    if row and updated:
                        user_id = row[0]
                        await send_message(user_id, f"⚠️ Payment mismatch detected. Please contact support with order {order_number}")
                finally:
                    conn.close()
            
            return {"status": "success", "message": "Mismatch noted"}
        
        # Only process completed payments from here
        if status != "completed":
            print_status("ℹ️", f"Unknown status {status} - ignoring")
            return {"status": "success", "message": f"Status {status} ignored"}
        
        # REQUIRE source_amount for completed payments
        if not source_amount:
            print_status("⚠️", f"Missing source_amount for completed payment: {order_number}")
            return {"status": "error", "message": "Missing source_amount"}
        
        # Atomically claim and process completed payment
        user_id = None
        new_credits = None
        credits = None  # Store credits for notification
        
        async with db_write_lock:
            conn = db_connect()
            try:
                cur = conn.cursor()
                
                # Atomically claim: set status='processing' only if 'pending' or 'new'
                cur.execute(
                    "UPDATE transactions SET status='processing' WHERE order_id=? AND status IN ('pending', 'new')",
                    (order_number,)
                )
                claimed = cur.rowcount > 0
                conn.commit()
                
                if not claimed:
                    print_status("⚠️", f"Transaction already processed: {order_number}")
                    return {"status": "success", "message": "Already processed"}
                
                # Get transaction details
                cur.execute(
                    "SELECT user_id, credits, amount_usd FROM transactions WHERE order_id=?",
                    (order_number,)
                )
                row = cur.fetchone()
                
                if not row:
                    print_status("⚠️", f"Transaction not found: {order_number}")
                    return {"status": "error", "message": "Transaction not found"}
                
                user_id, credits, expected_usd = row
                
                print_status("✅", f"Claimed transaction for user {user_id}: {credits} credits")
                
                # Validate amount
                try:
                    paid_usd = float(source_amount)
                except (ValueError, TypeError):
                    print_status("⚠️", f"Invalid source_amount: {source_amount}")
                    cur.execute("UPDATE transactions SET status='pending' WHERE order_id=?", (order_number,))
                    conn.commit()
                    return {"status": "error", "message": "Invalid source_amount"}
                
                tolerance = expected_usd * 0.02  # 2% tolerance
                
                if abs(paid_usd - expected_usd) > tolerance:
                    print_status("⚠️", f"Amount mismatch: expected ${expected_usd}, got ${paid_usd}")
                    cur.execute(
                        "UPDATE transactions SET status='amount_mismatch', completed_at=CURRENT_TIMESTAMP WHERE order_id=?",
                        (order_number,)
                    )
                    conn.commit()
                    
                    await send_message(
                        user_id,
                        f"⚠️ Payment amount mismatch. Expected ${expected_usd:.2f}, received ${paid_usd:.2f}. Contact support with order {order_number}"
                    )
                    return {"status": "error", "message": "Amount mismatch"}
                
                # Get or create user
                cur.execute("SELECT credits FROM users WHERE user_id=?", (user_id,))
                user_row = cur.fetchone()
                
                if not user_row:
                    cur.execute(
                        "INSERT INTO users (user_id, credits, free_used, last_reset) VALUES (?, 0, 0, ?)",
                        (user_id, datetime.date.today().isoformat())
                    )
                    current_credits = 0
                else:
                    current_credits = user_row[0]
                
                new_credits = current_credits + credits
                
                # Credit user
                cur.execute("UPDATE users SET credits=? WHERE user_id=?", (new_credits, user_id))
                
                # Mark transaction completed
                cur.execute(
                    "UPDATE transactions SET status='completed', payment_status=?, payment_currency=?, payment_amount=?, completed_at=CURRENT_TIMESTAMP WHERE order_id=?",
                    (status, currency, amount, order_number)
                )
                
                conn.commit()
                
                print_status("✅", f"Credited {credits} to user {user_id}, new balance: {new_credits}")
            
            except Exception as e:
                print_status("❌", f"Error processing payment: {e}")
                # Rollback to pending if we claimed it
                try:
                    if 'cur' in locals():
                        cur.execute("UPDATE transactions SET status='pending' WHERE order_id=?", (order_number,))
                        conn.commit()
                except:
                    pass
                raise
            
            finally:
                conn.close()
        
        # Notify user OUTSIDE lock
        if user_id and new_credits is not None:
            await send_message(
                chat_id=user_id,
                text=(
                    f"✅ **Payment Received!**\n\n"
                    f"Amount: {amount} {currency}\n"
                    f"Credits added: **{credits}**\n"
                    f"New balance: **{new_credits} credits**\n\n"
                    f"Thank you! Start generating with /generate"
                ),
                parse_mode="Markdown"
            )
        
        return {"status": "success"}
    
    except Exception as e:
        print_status("❌", f"Plisio webhook error: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.post("/webhook")
async def webhook(req: Request):
    """Handle Telegram webhook updates."""
    try:
        update = await req.json()
        
        # Handle callback queries (button presses)
        if "callback_query" in update:
            callback_query = update["callback_query"]
            callback_data = callback_query.get("data", "")
            chat_id = callback_query["message"]["chat"]["id"]
            callback_query_id = callback_query["id"]
            
            print_status("🔘", f"Callback from {chat_id}: {callback_data}")
            
            # Handle package selection
            if callback_data.startswith("buy_"):
                package_id = int(callback_data.split("_")[1])
                package = next((p for p in PAYMENT_PACKAGES if p["id"] == package_id), None)
                
                if not package:
                    await answer_callback_query(callback_query_id, "Invalid package", show_alert=True)
                    return {"ok": True}
                
                # Generate unique order number with microseconds to prevent collisions
                order_number = f"ORDER_{chat_id}_{int(time.time() * 1000)}"
                
                # Create Plisio invoice
                await answer_callback_query(callback_query_id, "Creating invoice...")
                
                invoice = await create_plisio_invoice(
                    user_id=chat_id,
                    amount_usd=package["price"],
                    credits=package["credits"],
                    order_number=order_number
                )
                
                if invoice["success"]:
                    # Log transaction
                    await log_transaction(
                        user_id=chat_id,
                        order_id=order_number,
                        amount_usd=package["price"],
                        credits=package["credits"],
                        txn_id=invoice["txn_id"]
                    )
                    
                    # Send payment link to user
                    await send_message(
                        chat_id=chat_id,
                        text=(
                            f"💳 **Payment Invoice Created**\n\n"
                            f"Package: **{package['credits']} credits**\n"
                            f"Price: **${package['price']} USD**\n\n"
                            f"Payment options:\n"
                            f"• USDT (TRC-20) - Low fees ~$1\n"
                            f"• Dogecoin - Very low fees\n"
                            f"• Bitcoin\n"
                            f"• Litecoin\n\n"
                            f"👉 [Click here to pay]({invoice['invoice_url']})\n\n"
                            f"⏰ Invoice expires in 60 minutes\n"
                            f"🔔 You'll be notified when payment is received"
                        ),
                        parse_mode="Markdown"
                    )
                else:
                    await send_message(
                        chat_id=chat_id,
                        text=f"❌ Failed to create invoice: {invoice.get('error', 'Unknown error')}"
                    )
            
            return {"ok": True}
        
        # Handle regular messages
        if "message" not in update:
            return {"ok": True}
        
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()
        
        print_status("📨", f"Message from {chat_id}: {text[:50]}...")
        
        # ========== COMMAND HANDLERS ==========
        
        if text == "/start":
            quota_msg = f"You get **{FREE_GENERATIONS_PER_DAY} free generations** per day.\nAfter that, you'll need credits.\n\n" if ENABLE_QUOTA_SYSTEM else "**Testing mode: Unlimited generations!**\n\n"
            
            await send_message(
                chat_id=chat_id,
                text=(
                    "🎨 Welcome to the AI Image Generator Bot!\n\n"
                    f"{quota_msg}"
                    "Commands:\n"
                    "• /generate <prompt> - Create an image\n"
                    "• /buy - Purchase credits\n"
                    "• /balance - Check your credits\n"
                    "• /examples - See prompt examples\n"
                    "• /help - Show all commands"
                ),
                parse_mode="Markdown"
            )
        
        elif text == "/help":
            await send_message(
                chat_id=chat_id,
                text=(
                    "📋 **Available Commands:**\n\n"
                    "/generate <prompt> - Generate an image\n"
                    "/buy - Purchase credits with crypto\n"
                    "/balance - Check credits & usage\n"
                    "/examples - Prompt ideas\n"
                    "/terms - Terms of service\n"
                    "/help - Show this message"
                ),
                parse_mode="Markdown"
            )
        
        elif text == "/balance":
            credits, free_used = await get_user(chat_id)
            free_remaining = max(0, FREE_GENERATIONS_PER_DAY - free_used)
            
            if ENABLE_QUOTA_SYSTEM:
                balance_text = (
                    f"💳 **Your Balance:**\n\n"
                    f"Free generations today: {free_remaining}/{FREE_GENERATIONS_PER_DAY}\n"
                    f"Credits: **{credits}**\n\n"
                    f"Purchase more: /buy"
                )
            else:
                balance_text = (
                    f"💳 **Testing Mode Active:**\n\n"
                    f"Unlimited generations available!\n"
                    f"Credits: {credits}\n"
                    f"Total generated: {free_used}\n\n"
                    f"You can still test /buy command"
                )
            
            await send_message(
                chat_id=chat_id,
                text=balance_text,
                parse_mode="Markdown"
            )
        
        elif text == "/buy":
            # Check if payments are enabled
            if not PLISIO_API_KEY:
                await send_message(
                    chat_id=chat_id,
                    text=(
                        "❌ **Payments Currently Disabled**\n\n"
                        "Crypto payments are not configured.\n"
                        "Please contact support for assistance."
                    ),
                    parse_mode="Markdown"
                )
                return {"ok": True}
            
            # Show payment packages
            keyboard = {
                "inline_keyboard": [
                    [{"text": pkg["label"], "callback_data": f"buy_{pkg['id']}"}]
                    for pkg in PAYMENT_PACKAGES
                ]
            }
            
            await send_message(
                chat_id=chat_id,
                text=(
                    "💳 **Purchase Credits**\n\n"
                    "Select a package below:\n\n"
                    "Payment accepted in:\n"
                    "• USDT (TRC-20) - Recommended\n"
                    "• Dogecoin - Best for small amounts\n"
                    "• Bitcoin\n"
                    "• Litecoin\n\n"
                    "Secure crypto payments via Plisio"
                ),
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        
        elif text == "/examples":
            await send_message(
                chat_id=chat_id,
                text=(
                    "💡 **Prompt Examples:**\n\n"
                    "• Beautiful woman, blonde hair, soft lighting, bedroom, candid photo\n"
                    "• Latina model, tattooed, cinematic lighting, professional photography\n"
                    "• Asian woman, elegant dress, studio lighting, fashion photography\n"
                    "• Redhead girl, freckles, natural light, outdoor portrait\n"
                    "• Athletic woman, gym setting, dynamic pose, fitness photography\n\n"
                    "Tips: Be descriptive about lighting, setting, and style!"
                ),
                parse_mode="Markdown"
            )
        
        elif text == "/terms":
            await send_message(
                chat_id=chat_id,
                text=(
                    "📜 **Terms of Service:**\n\n"
                    "• Must be 18+ to use this service\n"
                    "• No real people or celebrities\n"
                    "• No illegal or harmful content\n"
                    "• Abuse will result in permanent ban\n"
                    "• Generated images are for personal use\n"
                    "• We reserve the right to refuse service"
                ),
                parse_mode="Markdown"
            )
        
        elif text.startswith("/generate"):
            prompt = text.replace("/generate", "").strip()
            
            if not prompt:
                await send_message(
                    chat_id=chat_id,
                    text="❗ Usage: /generate <description>\n\nExample:\n/generate beautiful woman, soft lighting, professional photo"
                )
                return {"ok": True}
            
            # Check if user already has active generation
            if chat_id in active_generations:
                await send_message(
                    chat_id=chat_id,
                    text="⏳ You already have a generation in progress. Please wait for it to complete."
                )
                return {"ok": True}
            
            # Check quota (if enabled)
            can_generate = True
            status_msg = ""
            
            if ENABLE_QUOTA_SYSTEM:
                credits, free_used = await get_user(chat_id)
                
                if free_used < FREE_GENERATIONS_PER_DAY:
                    # Use free generation
                    await update_user(chat_id, free_used=free_used + 1)
                    remaining_free = FREE_GENERATIONS_PER_DAY - free_used - 1
                    status_msg = f"Using free generation ({remaining_free} left today)"
                elif credits > 0:
                    # Use credits
                    await update_user(chat_id, credits=credits - 1)
                    status_msg = f"Using 1 credit ({credits - 1} remaining)"
                else:
                    can_generate = False
                    await send_message(
                        chat_id=chat_id,
                        text=(
                            "❌ **No generations available**\n\n"
                            f"You've used your {FREE_GENERATIONS_PER_DAY} free generations today "
                            f"and have no credits.\n\n"
                            "Purchase credits: /buy"
                        ),
                        parse_mode="Markdown"
                    )
            else:
                # Testing mode - unlimited
                status_msg = "Testing mode - unlimited generations"
            
            if can_generate:
                # Log generation
                generation_id = await log_generation(chat_id, prompt, status="queued")
                
                # Send confirmation
                confirm_text = f"✅ Generation queued!\n\n{status_msg}\n\nYour image will be ready in ~30-60 seconds..."
                await send_message(chat_id=chat_id, text=confirm_text)
                
                # Start generation in background (non-blocking)
                asyncio.create_task(generate_and_send(chat_id, prompt, generation_id))
        
        else:
            # Unknown command
            if text.startswith("/"):
                await send_message(
                    chat_id=chat_id,
                    text="❓ Unknown command. Use /help to see available commands."
                )
        
        return {"ok": True}
        
    except Exception as e:
        print_status("❌", f"Webhook error: {e}")
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": str(e)}

# ========== STARTUP ==========

if __name__ == "__main__":
    import uvicorn
    
    # Validate required env vars
    if not TOKEN:
        print_status("❌", "TELEGRAM_KEY not set!")
        exit(1)
    
    if not API_KEY:
        print_status("❌", "RUNPOD_API_KEY not set!")
        exit(1)
    
    if not ENDPOINT_ID:
        print_status("❌", "RUNPOD_ENDPOINT_ID not set!")
        exit(1)
    
    if not WORKFLOW_PATH:
        print_status("❌", "WORKFLOW_PATH not set!")
        exit(1)
    
    if not PLISIO_API_KEY:
        print_status("⚠️", "PLISIO_API_KEY not set - payments disabled!")
    
    print("\n" + "="*60)
    print("🚀 STARTING BOT WITH PLISIO INTEGRATION")
    print("="*60)
    print(f"Telegram Token: {'SET' if TOKEN else 'MISSING'}")
    print(f"RunPod API Key: {'SET' if API_KEY else 'MISSING'}")
    print(f"Endpoint ID: {ENDPOINT_ID}")
    print(f"Plisio API Key: {'SET' if PLISIO_API_KEY else 'MISSING'}")
    print(f"Webhook URL: {WEBHOOK_BASE_URL}/webhook/plisio")
    print(f"Quota System: {'ENABLED' if ENABLE_QUOTA_SYSTEM else 'DISABLED'}")
    
    if WORKFLOW_PATH and os.path.exists(WORKFLOW_PATH):
        print(f"✅ Workflow file found")
    else:
        print(f"❌ Workflow file NOT FOUND at {WORKFLOW_PATH}")
    
    print("="*60 + "\n")
    
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)