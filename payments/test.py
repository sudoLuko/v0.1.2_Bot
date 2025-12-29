#!/usr/bin/env python3
"""
Standalone NOWPayments Test Script
Test your NOWPayments integration before adding to bot
"""

import os
import httpx
import asyncio
from dotenv import load_dotenv

load_dotenv()

# Configuration
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_API_BASE = "https://api.nowpayments.io/v1"

# ========== NOWPAYMENTS FUNCTIONS ==========

async def get_available_currencies():
    """Get list of available cryptocurrencies."""
    print("\n🔍 Testing: Get available currencies...")
    
    # DEBUG INFO
    print(f"\n🐛 DEBUG INFO:")
    print(f"API Key: {NOWPAYMENTS_API_KEY[:10] if NOWPAYMENTS_API_KEY else 'NOT SET'}...{NOWPAYMENTS_API_KEY[-4:] if NOWPAYMENTS_API_KEY else ''}")
    print(f"API Key Length: {len(NOWPAYMENTS_API_KEY) if NOWPAYMENTS_API_KEY else 0}")
    print(f"API Base URL: {NOWPAYMENTS_API_BASE}")
    print(f"Full URL: {NOWPAYMENTS_API_BASE}/currencies")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"x-api-key": NOWPAYMENTS_API_KEY}
        print(f"Headers: {headers}")
        
        response = await client.get(
            f"{NOWPAYMENTS_API_BASE}/currencies",
            headers=headers
        )
        
        print(f"\n📡 Response Status: {response.status_code}")
        print(f"📡 Response Headers: {response.headers}")
        print(f"📡 Response Body: {response.text[:500]}")
        
        if response.status_code == 200:
            currencies = response.json()
            print(f"✅ Success! Found {len(currencies['currencies'])} currencies")
            print(f"📝 Sample currencies: {currencies['currencies'][:10]}")
            return currencies
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"Response: {response.text}")
            return None

async def get_estimate(amount_usd, currency="usdttrc20"):
    """Get estimated amount in crypto for USD amount."""
    print(f"\n💱 Testing: Get estimate for ${amount_usd} USD in {currency}...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{NOWPAYMENTS_API_BASE}/estimate",
            params={
                "amount": amount_usd,
                "currency_from": "usd",
                "currency_to": currency
            },
            headers={"x-api-key": NOWPAYMENTS_API_KEY}
        )
        
        if response.status_code == 200:
            estimate = response.json()
            print(f"✅ Success!")
            print(f"📊 ${amount_usd} USD = {estimate['estimated_amount']} {currency.upper()}")
            return estimate
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"Response: {response.text}")
            return None

async def create_invoice(price_amount, price_currency="usd", order_id="test_001", order_description="Test Payment"):
    """Create a payment invoice."""
    print(f"\n💳 Testing: Create invoice for ${price_amount} {price_currency.upper()}...")
    
    payload = {
        "price_amount": price_amount,
        "price_currency": price_currency,
        "order_id": order_id,
        "order_description": order_description,
        "ipn_callback_url": "https://svthbzs7s6ioem-8000.proxy.runpod.net/webhook/payment",  # Will be your actual webhook
        "success_url": "https://example.com/success",
        "cancel_url": "https://example.com/cancel"
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{NOWPAYMENTS_API_BASE}/invoice",
            json=payload,
            headers={
                "x-api-key": NOWPAYMENTS_API_KEY,
                "Content-Type": "application/json"
            }
        )
        
        if response.status_code == 200:
            invoice = response.json()
            print(f"✅ Success! Invoice created")
            print(f"🔗 Invoice ID: {invoice['id']}")
            print(f"🌐 Payment URL: {invoice['invoice_url']}")
            print(f"\n💡 User would visit this URL to pay:")
            print(f"   {invoice['invoice_url']}")
            return invoice
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"Response: {response.text}")
            return None

async def get_payment_status(payment_id):
    """Check status of a payment."""
    print(f"\n🔍 Testing: Check payment status for ID {payment_id}...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{NOWPAYMENTS_API_BASE}/payment/{payment_id}",
            headers={"x-api-key": NOWPAYMENTS_API_KEY}
        )
        
        if response.status_code == 200:
            payment = response.json()
            print(f"✅ Success!")
            print(f"📊 Status: {payment['payment_status']}")
            print(f"💰 Amount: {payment['price_amount']} {payment['price_currency'].upper()}")
            return payment
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"Response: {response.text}")
            return None

# ========== TEST RUNNER ==========

async def run_all_tests():
    """Run all NOWPayments API tests."""
    
    print("=" * 60)
    print("🚀 NOWPayments API Test Suite")
    print("=" * 60)
    
    if not NOWPAYMENTS_API_KEY:
        print("❌ ERROR: NOWPAYMENTS_API_KEY not set in .env file!")
        return
    
    print(f"\n🔑 API Key: {NOWPAYMENTS_API_KEY[:10]}...{NOWPAYMENTS_API_KEY[-4:]}")
    
    # Test 1: Get available currencies
    currencies = await get_available_currencies()
    if not currencies:
        print("\n⚠️ Failed to get currencies. Check your API key!")
        return
    
    # Test 2: Get price estimate
    await get_estimate(2, "usdttrc20")
    
    # Test 3: Create test invoice
    invoice = await create_invoice(
        price_amount=2,
        price_currency="usd",
        order_id="test_12345",
        order_description="Test: 25 credits"
    )
    
    if invoice:
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print("\n📋 Next Steps:")
        print("1. Open the invoice URL in a browser to see the payment page")
        print("2. Try making a test payment (use testnet if available)")
        print("3. Once confirmed working, integrate into your bot!")
        print("\n💡 The invoice URL would be sent to users in Telegram")
    else:
        print("\n❌ Invoice creation failed. Check your API key and settings.")

# ========== INDIVIDUAL TEST FUNCTIONS ==========

async def test_create_payment():
    """Standalone test: Create a single payment."""
    print("\n🧪 Creating test payment for $2...")
    
    invoice = await create_invoice(
        price_amount=2,
        price_currency="usd",
        order_id=f"test_{int(asyncio.get_event_loop().time())}",
        order_description="Test Payment - 25 Credits"
    )
    
    if invoice:
        print("\n✅ Payment created successfully!")
        print(f"\n🔗 Send this URL to test the payment flow:")
        print(f"{invoice['invoice_url']}")
        print(f"\n💡 Open it in a browser to see what users will see")
        return invoice
    else:
        print("\n❌ Failed to create payment")
        return None

# ========== MAIN ==========

async def main():
    """Main entry point."""
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "test":
            # Run full test suite
            await run_all_tests()
        elif command == "create":
            # Just create a payment
            await test_create_payment()
        elif command == "status":
            # Check payment status
            if len(sys.argv) < 3:
                print("Usage: python test_nowpayments.py status <payment_id>")
                return
            payment_id = sys.argv[2]
            await get_payment_status(payment_id)
        else:
            print(f"Unknown command: {command}")
            print("\nAvailable commands:")
            print("  test     - Run full test suite")
            print("  create   - Create a test payment")
            print("  status   - Check payment status")
    else:
        # Default: run full test
        await run_all_tests()

if __name__ == "__main__":
    asyncio.run(main())
