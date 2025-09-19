# app.py
import os
import logging
from datetime import datetime
from flask import Flask, request, make_response, jsonify
from dotenv import load_dotenv


# network + fallback
import requests

# try to import africastalking (may raise when sdk is misbehaving in sandbox)
try:
    import africastalking
   
except Exception:
    africastalking = None

# Load .env
load_dotenv()

# Config
AT_USERNAME = os.getenv("AT_USERNAME", "sandbox").strip()
AT_API_KEY = os.getenv("AT_API_KEY", "").strip()
RANGERS = [n.strip() for n in os.getenv("RANGERS", "").split(",") if n.strip()]

if not AT_API_KEY:
    raise SystemExit("ERROR: AT_API_KEY missing in .env. Add it and restart.")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wildlife-guardian")

# Flask app (ensure app is defined before route decorators)
app = Flask(__name__)

# Try to initialize SDK safely.
sms_service = None
use_sdk = False

if africastalking is not None:
    try:
        africastalking.initialize(AT_USERNAME, AT_API_KEY)
        # grabbing the SMS service object (SDK exposes africastalking.SMS)
        sms_service = africastalking.SMS
        use_sdk = True
        logger.info("Africa's Talking SDK initialized. Using SDK for SMS.")
    except Exception as e:
        # If SDK initialization fails (WhatsApp sandbox or other), fallback to REST.
        logger.warning("Africa's Talking SDK initialize failed — falling back to REST SMS. Error: %s", e)
        sms_service = None
        use_sdk = False
else:
    logger.warning("Africa's Talking package not importable. Using REST fallback.")


# REST SMS endpoint selection (sandbox vs live)
if AT_USERNAME.lower() == "sandbox":
    SMS_ENDPOINT = "https://api.sandbox.africastalking.com/version1/messaging"
else:
    SMS_ENDPOINT = "https://api.africastalking.com/version1/messaging"


def send_sms(message: str, recipients: list):
    """
    Send SMS using SDK when available, otherwise fall back to REST endpoint.
    Returns: (success: bool, response_dict_or_text)
    """
    if not recipients:
        return False, "No recipients provided"

    # 1) Try SDK
    if use_sdk and sms_service is not None:
        try:
            # SDK expects a list of numbers (or CSV). We pass list.
            resp = sms_service.send(message, recipients)
            logger.info("SMS sent via SDK. response=%s", resp)
            return True, resp
        except Exception as e:
            logger.warning("SDK SMS send failed, will try REST fallback. Error: %s", e)

    # 2) Fallback via REST
    try:
        headers = {
            "Apikey": AT_API_KEY,
            "Accept": "application/json"
        }
        payload = {
            "username": AT_USERNAME,
            "to": ",".join(recipients),
            "message": message
        }
        r = requests.post(SMS_ENDPOINT, data=payload, headers=headers, timeout=15)
        r.raise_for_status()
        try:
            json_resp = r.json()
        except ValueError:
            json_resp = r.text
        logger.info("SMS sent via REST. status_code=%s response=%s", r.status_code, json_resp)
        return True, json_resp
    except Exception as e:
        logger.error("REST SMS send failed: %s", e)
        return False, str(e)


# In-memory reports store for demo
reports = []


@app.route("/ussd", methods=["POST"])
def ussd():
    """
    USSD endpoint expected to be called by Africa's Talking (POST).
    Basic flow:
      - text == ""   -> show menu
      - text == "1"  -> prompt for location for Poaching
      - text == "2"  -> prompt for location for Emergency
      - text like "1*<location>" or "2*<location>" -> store report and notify rangers via SMS
    """
    phone_number = request.values.get("phoneNumber", "")
    text = request.values.get("text", "").strip()

    # Top menu
    if text == "":
        return _ussd_response("CON Wildlife Guardian\n1. Report Poaching\n2. Emergency Wildlife Help")

    if text == "1":
        return _ussd_response("CON Enter location (village/landmark/coords):")

    if text == "2":
        return _ussd_response("CON Enter location for emergency (village/landmark/coords):")

    # Handle follow-ups "1*location" or "2*location"
    if text.startswith("1*") or text.startswith("2*"):
        parts = text.split("*", 1)
        option = parts[0]
        location = parts[1].strip() if len(parts) > 1 else "Unknown location"

        report_type = "Poaching" if option == "1" else "Emergency"
        report = {
            "reporter": phone_number,
            "type": report_type,
            "location": location,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        reports.append(report)

        # Notify rangers
        message = f"ALERT: {report['type']} reported at {report['location']} by {phone_number}"
        success, resp = send_sms(message, RANGERS)
        if not success:
            logger.error("Failed to notify rangers: %s", resp)

        return _ussd_response("END Thank you — rangers have been alerted. Stay safe.")

    return _ussd_response("END Invalid input. Try again.")


def _ussd_response(text: str):
    """Return plain-text USSD response"""
    resp = make_response(text)
    resp.headers["Content-Type"] = "text/plain"
    return resp


@app.route("/reports", methods=["GET"])
def get_reports():
    """Debug endpoint to view in-memory reports (demo only)."""
    return jsonify(reports), 200


if __name__ == "__main__":
    # Run the Flask app
    # Use host=0.0.0.0 if you want ngrok or external access
    app.run(host="0.0.0.0", port=5000, debug=True)
