import os
import re
import logging
from datetime import datetime
from flask import Flask, request, jsonify

# --- Logging setup ---
if not os.path.exists("logs"):
    os.makedirs("logs")

logging.basicConfig(
    filename="logs/app.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

app = Flask(__name__)

@app.route("/sms-webhook", methods=["POST"])
def sms_webhook():
    try:
        data = request.form.to_dict() or request.get_json(silent=True) or {}
        message = data.get("key") or data.get("msg") or ""
        raw_time = data.get("time") or datetime.now().isoformat()

        # --- Only handle UPI Credit messages ---
        if not message.lower().startswith("upi credit"):
            logging.info("Ignored non-credit message: %s", message)
            return jsonify({"status": "ignored", "reason": "not a credit message"}), 200

        # Parse amount
        amount_match = re.search(r'Rs\.?(\d+(?:\.\d{1,2})?)', message)
        amount = float(amount_match.group(1)) if amount_match else None

        # Parse transaction ID
        txn_match = re.search(r'Info:UPI/[A-Z]+/(\d+)/', message)
        txn_id = txn_match.group(1) if txn_match else None

        # Parse name
        name_match = re.search(r'/(\w[\w\s]*)\s+on\s+\d', message)
        name = name_match.group(1).strip() if name_match else None

        # Parse timestamp
        time_match = re.search(r'on\s+(\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', message)
        sms_time = time_match.group(1) if time_match else None

        parsed_data = {
            "name": name,
            "transaction_id": txn_id,
            "amount": amount,
            "timestamp": sms_time,
            "raw_sms": message,
            "received_at": raw_time,
        }

        logging.info("✅ Parsed payment: %s", parsed_data)

        # Respond to sender
        return jsonify({"status": "success", "parsed": parsed_data}), 200

    except Exception as e:
        logging.error("Error processing SMS: %s", str(e), exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "message": "SMS Webhook is running",
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
