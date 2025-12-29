#!/usr/bin/env python3
"""
Bulletproof Telegram Bot for AI Image Generation
Fixed connection pool issues and simplified architecture
"""

import os
import asyncio
import sqlite3
import datetime
import time
import json
import base64
import secrets
import httpx
from pathlib import Path
from fastapi import FastAPI, Request
from io import BytesIO
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ========== FEATURE FLAGS ==========
ENABLE_QUOTA_SYSTEM = False  # Set to False for unlimited testing
FREE_GENERATIONS_PER_DAY = 2  # Only applies if ENABLE_QUOTA_SYSTEM is True

# ========== CONFIGURATION ==========
TOKEN = os.getenv("TELEGRAM_KEY")
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TOKEN}"

# RunPod Configuration
ENDPOINT_ID = os.getenv("RUNPOD_ENDPOINT_ID")
API_KEY = os.getenv("RUNPOD_API_KEY")
WORKFLOW_PATH = os.getenv("WORKFLOW_PATH")

# Generation settings
POLL_INTERVAL = 3  # seconds
MAX_POLL_TIME = 300  # 5 minutes max wait
MAX_CONCURRENT_GENERATIONS = 1  # Limit concurrent generations

# Database
DB = "users.db"

# FastAPI app
app = FastAPI()

# Track active generations to prevent overwhelming the system
active_generations = set()
db_write_lock = asyncio.Lock()

# Create a single httpx client for all Telegram requests
telegram_client = None

# ========== HELPER FUNCTIONS ==========

def print_status(emoji, message):
    """Print formatted status message."""
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {emoji} {message}")

# ========== TELEGRAM FUNCTIONS ==========

async def send_message(chat_id, text, parse_mode=None):
    """Send message to Telegram user using httpx."""
    try:
        data = {
            "chat_id": chat_id,
            "text": text
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        
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
    print_status("📊", f"Max concurrent generations: {MAX_CONCURRENT_GENERATIONS}")

@app.get("/")
def health():
    """Health check endpoint."""
    return {
        "status": "up",
        "quota_enabled": ENABLE_QUOTA_SYSTEM,
        "active_generations": len(active_generations),
        "max_concurrent": MAX_CONCURRENT_GENERATIONS
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
    
    conn.close()
    
    return {
        "users": user_count,
        "generations": gen_stats,
        "active_generations": len(active_generations),
        "quota_enabled": ENABLE_QUOTA_SYSTEM
    }

@app.post("/webhook")
async def webhook(req: Request):
    """Handle Telegram webhook updates."""
    try:
        update = await req.json()
        
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
                    "/balance - Check credits & usage\n"
                    "/examples - Prompt ideas\n"
                    "/terms - Terms of service\n"
                    "/help - Show this message"
                ),
                parse_mode="Markdown"
            )
        
        elif text == "/balance":
            credits, free_used = get_user(chat_id)
            free_remaining = max(0, FREE_GENERATIONS_PER_DAY - free_used)
            
            if ENABLE_QUOTA_SYSTEM:
                balance_text = (
                    f"💳 **Your Balance:**\n\n"
                    f"Free generations today: {free_remaining}/{FREE_GENERATIONS_PER_DAY}\n"
                    f"Credits: {credits}\n"
                )
            else:
                balance_text = (
                    f"💳 **Testing Mode Active:**\n\n"
                    f"Unlimited generations available!\n"
                    f"Credits: {credits}\n"
                    f"Total generated: {free_used}"
                )
            
            await send_message(
                chat_id=chat_id,
                text=balance_text,
                parse_mode="Markdown"
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
            
            # Check concurrent limit
            if len(active_generations) >= MAX_CONCURRENT_GENERATIONS:
                await send_message(
                    chat_id=chat_id,
                    text=f"⏳ Server is busy ({len(active_generations)}/{MAX_CONCURRENT_GENERATIONS} active). Please try again in a moment."
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
                            f"You've used your {FREE_GENERATIONS_PER_DAY} free generations today.\n"
                            "Please buy credits to continue.\n\n"
                            "Contact @your_admin for credits."
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
    
    print("\n" + "="*60)
    print("🚀 STARTING BULLETPROOF BOT")
    print("="*60)
    print(f"Telegram Token: {'SET' if TOKEN else 'MISSING'}")
    print(f"RunPod API Key: {'SET' if API_KEY else 'MISSING'}")
    print(f"Endpoint ID: {ENDPOINT_ID}")
    print(f"Workflow Path: {WORKFLOW_PATH}")
    print(f"Quota System: {'ENABLED' if ENABLE_QUOTA_SYSTEM else 'DISABLED'}")
    
    if WORKFLOW_PATH and os.path.exists(WORKFLOW_PATH):
        print(f"✅ Workflow file found")
    else:
        print(f"❌ Workflow file NOT FOUND at {WORKFLOW_PATH}")
    
    print("="*60 + "\n")
    
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
