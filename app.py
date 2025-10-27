import os
import re
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from collections import deque
from threading import Lock
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()
# --- Logging setup ---
if not os.path.exists("logs"):
    os.makedirs("logs")

logging.basicConfig(
    filename="logs/app.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

app = Flask(__name__)

# Thread-safe in-memory store for last 5 request/response pairs
RECENT_LOCK = Lock()
RECENT_ENTRIES = deque(maxlen=5)

# --- MongoDB setup (optional) ---
MONGODB_URI = os.environ.get("MONGODB_URI")
mongo_client = None
mongo_db = None
if MONGODB_URI:
    try:
        mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        # Trigger a server selection to validate the URI early
        mongo_client.server_info()
        # Use the explicit database 'wc2026' per user request (no fallback)
        mongo_db = mongo_client.get_database("wc2026")
        logging.info("Connected to MongoDB Atlas database: %s", mongo_db.name)
    except Exception:
        # If connection fails, leave mongo_db as None and log the error
        mongo_db = None
        logging.exception("Failed to connect to MongoDB Atlas using MONGODB_URI; won't persist logs")

@app.route("/sms-webhook", methods=["POST"])
def sms_webhook():
    try:
        # Read the raw body first and cache it. Some Flask helpers (form/json parsers)
        # may consume the input stream; reading it early ensures we can persist the
        # exact raw payload to MongoDB.
        raw_body = request.get_data(as_text=True)
        data = request.form.to_dict() or request.get_json(silent=True) or {}
        message = data.get("key") or data.get("msg") or ""
        raw_time = data.get("time") or datetime.now().isoformat()

        # --- Only handle UPI Credit messages ---
        if not message.lower().startswith("upi credit"):
            logging.info("Ignored non-credit message: %s", message)
            # Save the raw request (unstructured) into MongoDB 'wc2026.logs' if configured
            if mongo_db is not None:
                try:
                    mongo_db["logs"].insert_one({"raw_request": raw_body, "saved_at": datetime.now(timezone.utc)})
                except Exception:
                    logging.exception("Failed to save raw non-credit request to MongoDB 'logs' collection")
            # Return an error status code with no JSON body as requested
            return "", 400

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

        # Prepare response
        response_body = {"status": "success", "parsed": parsed_data}
        print("Response:", response_body)
        # Record the request and response in the recent entries store
        entry = {
            "received_at": parsed_data.get("received_at"),
            "request": {
                "headers": dict(request.headers),
                "form": request.form.to_dict(),
                "json": request.get_json(silent=True),
            },
            "parsed": parsed_data,
            "response": response_body,
        }
        with RECENT_LOCK:
            RECENT_ENTRIES.appendleft(entry)

        # Also persist to MongoDB collection `logs` if configured.
        # This will create the collection automatically on first insert.
        if mongo_db is not None:
            try:
                doc = {
                    # Use the cached raw body so we store exactly what arrived
                    "raw_request": raw_body,
                    "method": request.method,
                    "path": request.path,
                    "query_string": request.query_string.decode() if request.query_string else "",
                    "remote_addr": request.remote_addr,
                    "headers": dict(request.headers),
                    "form": request.form.to_dict(),
                    "json": request.get_json(silent=True),
                    "parsed": parsed_data,
                    "response": response_body,
                    "received_at": parsed_data.get("received_at"),
                    # Use timezone-aware UTC datetime to avoid deprecation warnings
                    "saved_at": datetime.now(timezone.utc),
                }
                mongo_db["logs"].insert_one(doc)
            except Exception:
                logging.exception("Failed to insert webhook document into MongoDB 'logs' collection")

        # Respond to sender
        return jsonify(response_body), 200

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


@app.route("/recent", methods=["GET"])
def recent():
    """Render a simple page showing the last 5 requests and responses."""
    with RECENT_LOCK:
        entries = list(RECENT_ENTRIES)
    return render_template("recent.html", entries=entries)


@app.route("/save-logs", methods=["POST"])
def save_logs():
    """Accept any POST and save the raw request and metadata into wc2026.logs.

    Returns 201 with inserted_id on success, 500 on failure, or 400 if DB not configured.
    """
    if mongo_db is None:
        logging.error("/save-logs called but mongo_db is not configured")
        return jsonify({"status": "error", "message": "database not configured"}), 400

    try:
        doc = {
            "raw_request": request.get_data(as_text=True),
            "method": request.method,
            "path": request.path,
            "query_string": request.query_string.decode() if request.query_string else "",
            "remote_addr": request.remote_addr,
            "headers": dict(request.headers),
            "form": request.form.to_dict(),
            "json": request.get_json(silent=True),
            "received_at": datetime.now(timezone.utc),
        }
        res = mongo_db["logs"].insert_one(doc)
        logging.info("Saved log with id %s", res.inserted_id)
        return jsonify({"status": "created", "inserted_id": str(res.inserted_id)}), 201
    except Exception as ex:
        logging.exception("Failed to save log in /save-logs")
        return jsonify({"status": "error", "message": str(ex)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000,debug=False)
