#!/usr/bin/env python3
"""
Minimal Telegram bot for image generation - TESTING ONLY
Only handles /generate command. No database, no queue.
"""

import os
import json
import time
import random
import base64
import requests
import asyncio
from fastapi import FastAPI, Request
from telegram import Bot
from io import BytesIO
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
TOKEN = os.getenv("TELEGRAM_KEY")
ENDPOINT_ID = os.getenv("RUNPOD_ENDPOINT_ID")
API_KEY = os.getenv("RUNPOD_API_KEY")
WORKFLOW_PATH = os.getenv("WORKFLOW_PATH")

# Initialize bot
bot = Bot(token=TOKEN)
app = FastAPI()

# ========== GENERATION FUNCTIONS ==========

def load_workflow():
    """Load ComfyUI workflow from file."""
    with open(WORKFLOW_PATH) as f:
        return json.load(f)

def randomize_seeds(workflow):
    """Randomize all known seed inputs in workflow (independently)."""
    for node in workflow.values():
        inputs = node.get("inputs", {})
        if "seed" in inputs:
            inputs["seed"] = random.randint(0, 2**32 - 1)
        if "noise_seed" in inputs:
            inputs["noise_seed"] = random.randint(0, 2**32 - 1)
    return workflow

def update_prompt(workflow, prompt):
    """Update prompt in node 45."""
    if "45" in workflow:
        workflow["45"]["inputs"]["string_a"] = prompt
    return workflow

def submit_job(workflow):
    """Submit job to RunPod."""
    response = requests.post(
        f"https://api.runpod.ai/v2/{ENDPOINT_ID}/run",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={"input": workflow},
        timeout=30
    )
    response.raise_for_status()
    return response.json()

def poll_job(job_id, max_wait=300):
    """Poll job until complete."""
    start_time = time.time()

    while time.time() - start_time < max_wait:
        response = requests.get(
            f"https://api.runpod.ai/v2/{ENDPOINT_ID}/status/{job_id}",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=10
        )
        status = response.json()

        print(f"[STATUS] {status['status']}")

        if status["status"] == "COMPLETED":
            return status

        if status["status"] in ("FAILED", "ERROR", "CANCELLED"):
            raise RuntimeError(f"Job failed: {status.get('error', 'Unknown')}")

        time.sleep(3)

    raise TimeoutError("Job timed out")

def extract_image(output):
    """Extract image bytes from output."""
    if not output or "images" not in output or not output["images"]:
        return None

    img_data = output["images"][0]
    return base64.b64decode(img_data["data"])

async def generate_and_send(chat_id, prompt):
    """Main generation function."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text="🎨 Generating your image...\n⏱️ This takes ~30–60 seconds"
        )

        print(f"[LOAD] Loading workflow from {WORKFLOW_PATH}")
        workflow = load_workflow()

        print(f"[PROMPT] Setting prompt: {prompt[:50]}...")
        workflow = update_prompt(workflow, prompt)

        print("[SEEDS] Randomizing seeds")
        workflow = randomize_seeds(workflow)

        print("[SUBMIT] Submitting to RunPod")
        job = submit_job(workflow)
        job_id = job["id"]
        print(f"[JOB] Job ID: {job_id}")

        await bot.send_message(
            chat_id=chat_id,
            text=f"⏳ Job submitted: `{job_id}`\nPolling for completion...",
            parse_mode="Markdown"
        )

        print("[POLL] Waiting for completion")
        result = poll_job(job_id)

        print("[IMAGE] Extracting image")
        image_bytes = extract_image(result.get("output"))

        if not image_bytes:
            raise RuntimeError("No image in output")

        print("[SEND] Sending image to user")
        await bot.send_photo(
            chat_id=chat_id,
            photo=BytesIO(image_bytes),
            caption=f"✅ Complete!\n\n{prompt[:100]}"
        )

        print("[SUCCESS] Image sent successfully")

    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Error: {str(e)[:300]}"
        )

# ========== FASTAPI ROUTES ==========

@app.get("/")
def health():
    return {
        "status": "ok",
        "endpoint_id": ENDPOINT_ID,
        "workflow_path": WORKFLOW_PATH
    }

@app.post("/webhook")
async def webhook(req: Request):
    try:
        update = await req.json()
        print(f"[UPDATE] {json.dumps(update, indent=2)}")

        if "message" not in update:
            return {"ok": True}

        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()

        print(f"[MSG] From {chat_id}: {text}")

        if text == "/start":
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "🎨 **Minimal Image Generator Bot**\n\n"
                    "Commands:\n"
                    "• /generate <prompt>\n"
                    "• /test"
                ),
                parse_mode="Markdown"
            )

        elif text == "/test":
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "✅ Bot is working!\n\n"
                    f"Endpoint: `{ENDPOINT_ID}`\n"
                    f"API Key: `{'SET' if API_KEY else 'MISSING'}`\n"
                    f"Workflow: `{'FOUND' if WORKFLOW_PATH and os.path.exists(WORKFLOW_PATH) else 'MISSING'}`"
                ),
                parse_mode="Markdown"
            )

        elif text.startswith("/generate"):
            prompt = text.replace("/generate", "").strip()
            if not prompt:
                await bot.send_message(
                    chat_id=chat_id,
                    text="❗ Usage: /generate <prompt>"
                )
            else:
                asyncio.create_task(generate_and_send(chat_id, prompt))

        elif text.startswith("/"):
            await bot.send_message(chat_id=chat_id, text="❓ Unknown command")

        return {"ok": True}

    except Exception as e:
        print(f"[WEBHOOK ERROR] {e}")
        return {"ok": False, "error": str(e)}

# ========== STARTUP ==========

@app.on_event("startup")
async def startup():
    print("\n" + "=" * 60)
    print("🚀 MINIMAL BOT STARTING")
    print("=" * 60)
    print(f"Telegram Token: {'SET' if TOKEN else 'MISSING'}")
    print(f"RunPod API Key: {'SET' if API_KEY else 'MISSING'}")
    print(f"Endpoint ID: {ENDPOINT_ID}")
    print(f"Workflow Path: {WORKFLOW_PATH}")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    import uvicorn

    if not all([TOKEN, API_KEY, ENDPOINT_ID, WORKFLOW_PATH]):
        print("❌ Missing required environment variables")
        exit(1)

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
