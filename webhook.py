#!/usr/bin/env python3
"""
Setup script for Telegram bot webhook configuration.
Run this once to set up your bot's webhook.
"""

import os
import sys
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def setup_webhook():
    """Configure the Telegram webhook."""
    
    token = os.getenv("TELEGRAM_KEY")
    webhook_url = os.getenv("WEBHOOK_URL")
    
    if not token:
        print("❌ Error: TELEGRAM_KEY not set in .env file")
        sys.exit(1)
    
    if not webhook_url:
        print("⚠️  WEBHOOK_URL not set. Bot will work in polling mode (development only)")
        print("For production, set WEBHOOK_URL in your .env file")
        return
    
    # Set the webhook
    url = f"https://api.telegram.org/bot{token}/setWebhook"
    data = {
        "url": webhook_url,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True  # Optional: drop pending updates
    }
    
    print(f"Setting webhook to: {webhook_url}")
    response = requests.post(url, json=data)
    
    if response.ok:
        result = response.json()
        if result.get("ok"):
            print("✅ Webhook configured successfully!")
            print(f"Response: {result}")
        else:
            print(f"❌ Failed to set webhook: {result}")
    else:
        print(f"❌ HTTP error: {response.status_code}")
        print(response.text)

def get_webhook_info():
    """Get current webhook configuration."""
    
    token = os.getenv("TELEGRAM_KEY")
    if not token:
        print("❌ Error: TELEGRAM_KEY not set")
        return
    
    url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    response = requests.get(url)
    
    if response.ok:
        info = response.json()
        if info.get("ok"):
            webhook_data = info.get("result", {})
            print("\n📍 Current Webhook Info:")
            print(f"URL: {webhook_data.get('url', 'Not set')}")
            print(f"Pending updates: {webhook_data.get('pending_update_count', 0)}")
            if webhook_data.get('last_error_date'):
                print(f"Last error: {webhook_data.get('last_error_message')}")
    else:
        print(f"❌ Failed to get webhook info: {response.status_code}")

def delete_webhook():
    """Remove webhook configuration (for testing with polling)."""
    
    token = os.getenv("TELEGRAM_KEY")
    if not token:
        print("❌ Error: TELEGRAM_KEY not set")
        return
    
    url = f"https://api.telegram.org/bot{token}/deleteWebhook"
    response = requests.post(url, json={"drop_pending_updates": True})
    
    if response.ok and response.json().get("ok"):
        print("✅ Webhook deleted successfully")
    else:
        print(f"❌ Failed to delete webhook: {response.text}")

if __name__ == "__main__":
    print("🤖 Telegram Bot Setup Script\n")
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "delete":
            delete_webhook()
        elif command == "info":
            get_webhook_info()
        else:
            print(f"Unknown command: {command}")
            print("Usage: python setup_webhook.py [delete|info]")
    else:
        setup_webhook()
        get_webhook_info()
