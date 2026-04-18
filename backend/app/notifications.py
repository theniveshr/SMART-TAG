"""
notifications.py - SMS Alert System using Twilio
Configure TWILIO_* environment variables to enable real SMS.
Without credentials, messages are logged only.
"""

import os
from datetime import datetime


# ── Twilio Setup ───────────────────────────────────────────────────────────

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "+1234567890")
ADMIN_PHONE        = os.getenv("ADMIN_PHONE",        "+919999999999")

def _get_twilio_client():
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return None
    try:
        from twilio.rest import Client
        return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    except ImportError:
        return None


# ── SMS Templates ─────────────────────────────────────────────────────────

def sms_toll_success(plate, amount, balance_after, txn_id, gate_name):
    return (
        f"[TollAI] ✅ Toll Paid!\n"
        f"Vehicle : {plate}\n"
        f"Gate    : {gate_name}\n"
        f"Amount  : ₹{amount}\n"
        f"Balance : ₹{balance_after:.2f}\n"
        f"TxnID   : {txn_id}\n"
        f"Time    : {datetime.now().strftime('%d %b %H:%M')}"
    )

def sms_fraud_owner(plate, fraud_type, txn_id):
    return (
        f"[TollAI] ⚠ FRAUD ALERT!\n"
        f"Your vehicle {plate} was flagged:\n"
        f"Reason  : {fraud_type}\n"
        f"TxnID   : {txn_id}\n"
        f"If this is wrong, contact: support@tollai.in"
    )

def sms_fraud_admin(plate, severity, fraud_types, txn_id, gate_name):
    return (
        f"[TollAI] 🚨 FRAUD DETECTED!\n"
        f"Vehicle  : {plate}\n"
        f"Gate     : {gate_name}\n"
        f"Severity : {severity.upper()}\n"
        f"Type     : {fraud_types}\n"
        f"TxnID    : {txn_id}\n"
        f"Time     : {datetime.now().strftime('%d %b %H:%M')}\n"
        f"Action   : Barrier CLOSED. Review needed."
    )


# ── Send Function ─────────────────────────────────────────────────────────

def send_sms(to_number: str, message: str) -> dict:
    """
    Send SMS via Twilio.
    Returns {"status": "sent"|"logged"|"error", "message": ...}
    """
    client = _get_twilio_client()

    if client:
        try:
            msg = client.messages.create(
                body = message,
                from_= TWILIO_FROM_NUMBER,
                to   = to_number,
            )
            print(f"📱 SMS sent to {to_number}: {msg.sid}")
            return {"status": "sent", "sid": msg.sid}
        except Exception as e:
            print(f"❌ SMS failed: {e}")
            return {"status": "error", "error": str(e)}
    else:
        # Log to console (no Twilio credentials)
        print(f"\n{'─'*60}")
        print(f"📱 [SMS LOG] To: {to_number}")
        print(message)
        print(f"{'─'*60}\n")
        return {"status": "logged"}


# ── High-level Notifiers ───────────────────────────────────────────────────

def notify_toll_success(plate, amount, balance_after, txn_id, gate_name, owner_phone):
    msg = sms_toll_success(plate, amount, balance_after, txn_id, gate_name)
    if owner_phone:
        return send_sms(owner_phone, msg)
    return {"status": "skipped", "reason": "No owner phone"}

def notify_fraud(plate, severity, fraud_types, txn_id, gate_name, owner_phone=None):
    # Notify admin
    admin_msg = sms_fraud_admin(plate, severity, fraud_types, txn_id, gate_name)
    send_sms(ADMIN_PHONE, admin_msg)

    # Notify owner if phone available
    if owner_phone:
        owner_msg = sms_fraud_owner(plate, fraud_types, txn_id)
        send_sms(owner_phone, owner_msg)

    return {"status": "notified"}
