"""
scripts/create_test_order.py

Creates a Razorpay test-mode Order via the API, then prints a minimal
HTML checkout page you can open in a browser to complete a test
payment against it — which will fire payment.authorized and
payment.captured webhooks to your listener.
"""

import os
import webbrowser
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

KEY_ID = os.environ["RAZORPAY_KEY_ID"]
KEY_SECRET = os.environ["RAZORPAY_KEY_SECRET"]

order_resp = requests.post(
    "https://api.razorpay.com/v1/orders",
    auth=(KEY_ID, KEY_SECRET),
    json={
        "amount": 50000,          # in paise -> ₹500.00
        "currency": "INR",
        "receipt": "shield_assist_phase0_test",
    },
    timeout=15,
)
order_resp.raise_for_status()
order = order_resp.json()
print("Order created:", order["id"])

checkout_html = f"""
<!DOCTYPE html>
<html>
<head><title>Shield Assist — Test Checkout</title></head>
<body>
  <h2>Phase 0 Webhook Test Payment</h2>
  <button id="pay-btn">Pay ₹500 (Test Mode)</button>
  <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
  <script>
    document.getElementById('pay-btn').onclick = function () {{
      var rzp = new Razorpay({{
        key: "{KEY_ID}",
        amount: "{order['amount']}",
        currency: "{order['currency']}",
        order_id: "{order['id']}",
        name: "Shield Assist",
        description: "Phase 0 webhook plumbing test",
        handler: function (response) {{
          document.body.innerHTML =
            "<h3>Payment done: " + response.razorpay_payment_id + "</h3>" +
            "<p>Check your uvicorn terminal for the webhook.</p>";
        }},
      }});
      rzp.open();
    }};
  </script>
</body>
</html>
"""

out_path = Path(__file__).resolve().parent / "test_checkout.html"
out_path.write_text(checkout_html, encoding="utf-8")
print(f"Opening {out_path} in your browser...")
webbrowser.open(out_path.as_uri())