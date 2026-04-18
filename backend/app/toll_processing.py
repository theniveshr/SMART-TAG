"""
toll_processing.py - Core toll transaction processor.
Orchestrates: OCR → FASTag lookup → Fraud check → Deduction → Logging.
"""

import uuid
import os
import base64
from datetime import datetime
from typing import Optional

from .database    import get_connection
from .fraud_detection import get_engine
from .plate_recognition import get_recognizer


EVIDENCE_DIR = os.path.join(os.path.dirname(__file__), '../../logs/evidence')


def _ensure_evidence_dir():
    os.makedirs(EVIDENCE_DIR, exist_ok=True)


def _save_evidence(image_b64: str, plate: str, tid: str) -> Optional[str]:
    """Save fraud evidence image to disk."""
    try:
        _ensure_evidence_dir()
        if ',' in image_b64:
            image_b64 = image_b64.split(',', 1)[1]
        img_bytes = base64.b64decode(image_b64)
        fname = f"{tid}_{plate}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
        fpath = os.path.join(EVIDENCE_DIR, fname)
        with open(fpath, 'wb') as f:
            f.write(img_bytes)
        return fpath
    except Exception:
        return None


def _get_toll_amount(conn, gate_id: int, vehicle_type: str) -> float:
    cur = conn.execute("SELECT * FROM toll_gates WHERE id=?", (gate_id,))
    gate = cur.fetchone()
    if not gate:
        return 65.0
    col_map = {
        "car":   "toll_car",
        "truck": "toll_truck",
        "bus":   "toll_bus",
        "bike":  "toll_bike",
        "van":   "toll_van",
    }
    col = col_map.get(vehicle_type, "toll_car")
    return gate[col]


def process_vehicle(
    image_b64:     str,
    gate_id:       int   = 1,
    manual_plate:  str   = None,
    manual_type:   str   = None,
) -> dict:
    """
    Full toll processing pipeline.

    Returns a rich result dict with transaction details + fraud analysis.
    """
    conn = get_connection()
    recognizer = get_recognizer()
    engine = get_engine(conn)

    # ── Step 1: Plate Recognition ─────────────────────────────────────────────
    if manual_plate:
        ocr_result = {
            "plate_number": manual_plate.upper().replace(" ", ""),
            "confidence":   1.0,
            "raw_text":     manual_plate,
            "mode":         "manual",
            "success":      True,
        }
    else:
        ocr_result = recognizer.recognize_from_base64(image_b64) if image_b64 else recognizer._simulate_recognize()

    plate        = ocr_result.get("plate_number", "UNKNOWN")
    ocr_conf     = ocr_result.get("confidence",   0.0)
    ocr_success  = ocr_result.get("success",       False)

    # ── Step 2: DB Lookups ────────────────────────────────────────────────────
    veh_cur = conn.execute("SELECT * FROM vehicles WHERE plate_number=?", (plate,))
    vehicle = veh_cur.fetchone()

    # Dynamic Enrichment: Fetch details if missing
    if not vehicle and plate != "UNKNOWN":
        from .vehicle_fetching import get_external_vehicle_details
        ext = get_external_vehicle_details(plate)
        if ext:
            try:
                conn.execute("""
                    INSERT INTO vehicles (plate_number, owner_name, vehicle_type, fuel_type, state_code)
                    VALUES (?,?,?,?,?)
                """, (plate, ext["owner_name"], ext["vehicle_type"], ext["fuel_type"], plate[:2]))
                conn.commit()
                # Refetch
                veh_cur = conn.execute("SELECT * FROM vehicles WHERE plate_number=?", (plate,))
                vehicle = veh_cur.fetchone()
            except Exception as e:
                print(f"Auto-registration error for {plate}: {str(e)}")

    # Find FASTag by plate (primary) or by manual fastag_id
    ft_cur  = conn.execute(
        "SELECT * FROM fastag WHERE plate_number=? AND is_active=1 LIMIT 1", (plate,)
    )
    fastag  = ft_cur.fetchone()

    # Also check ALL fastag entries for this plate (including inactive)
    ft_all_cur = conn.execute("SELECT * FROM fastag WHERE plate_number=?", (plate,))
    all_fastags = ft_all_cur.fetchall()

    # Determine vehicle type
    detected_type = manual_type or (vehicle["vehicle_type"] if vehicle else "car")

    # ── Step 3: Toll Amount ───────────────────────────────────────────────────
    toll_amount = _get_toll_amount(conn, gate_id, detected_type)

    # ── Step 4: Gate Info ─────────────────────────────────────────────────────
    gate_cur  = conn.execute("SELECT * FROM toll_gates WHERE id=?", (gate_id,))
    gate_info = gate_cur.fetchone()

    # ── Step 5: Fraud Analysis ────────────────────────────────────────────────
    fraud = engine.analyze(
        plate_number   = plate,
        fastag_id      = fastag["fastag_id"] if fastag else None,
        fastag_record  = dict(fastag)  if fastag else None,
        vehicle_record = dict(vehicle) if vehicle else None,
        toll_amount    = toll_amount,
        detected_type  = detected_type,
        ocr_confidence = ocr_conf,
        gate_id        = gate_id,
    )

    # ── Step 6: Historical Risk ───────────────────────────────────────────────
    risk_history = engine.get_risk_summary(plate)

    # ── Step 7: Transaction ID ────────────────────────────────────────────────
    tid = f"TXN{uuid.uuid4().hex[:10].upper()}"

    # ── Step 8: Deduct or Block ───────────────────────────────────────────────
    balance_before = fastag["balance"] if fastag else 0.0
    balance_after  = balance_before

    status        = "fraud" if fraud.is_fraud else "success"
    evidence_path = None

    if not fraud.is_fraud and fastag:
        balance_after = round(balance_before - toll_amount, 2)
        conn.execute(
            "UPDATE fastag SET balance=? WHERE fastag_id=?",
            (balance_after, fastag["fastag_id"])
        )
        status = "success"
    else:
        # Save evidence
        if image_b64:
            evidence_path = _save_evidence(image_b64, plate, tid)

    # ── Step 9: Log Transaction ───────────────────────────────────────────────
    conn.execute("""
        INSERT INTO transactions
        (transaction_id,plate_number,fastag_id,gate_id,toll_amount,balance_before,balance_after,
         vehicle_type,status,fraud_type,fraud_score,image_path,ocr_confidence)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        tid, plate,
        fastag["fastag_id"] if fastag else None,
        gate_id, toll_amount, balance_before, balance_after,
        detected_type, status,
        ", ".join(fraud.fraud_types) if fraud.fraud_types else None,
        fraud.fraud_score, evidence_path, ocr_conf,
    ))

    # ── Step 10: Fraud Alert ──────────────────────────────────────────────────
    if fraud.is_fraud:
        conn.execute("""
            INSERT INTO fraud_alerts
            (transaction_id,plate_number,alert_type,severity,description,evidence_image)
            VALUES (?,?,?,?,?,?)
        """, (
            tid, plate,
            ", ".join(fraud.fraud_types),
            fraud.severity,
            " | ".join(fraud.descriptions),
            evidence_path,
        ))

    conn.commit()

    # ── Step 11: Notifications (logged only) ──────────────────────────────────
    _log_notification(conn, plate, tid, status, fastag, vehicle, fraud)

    conn.close()

    # ── Result ────────────────────────────────────────────────────────────────
    return {
        "transaction_id":   tid,
        "plate_number":     plate,
        "ocr": {
            "raw_text":     ocr_result.get("raw_text"),
            "confidence":   ocr_conf,
            "mode":         ocr_result.get("mode"),
        },
        "vehicle": {
            "found":        vehicle is not None,
            "owner_name":   vehicle["owner_name"]   if vehicle else "Unknown",
            "owner_phone":  vehicle["owner_phone"]  if vehicle else None,
            "vehicle_type": detected_type,
            "fuel_type":    vehicle["fuel_type"]    if vehicle else "UNKNOWN",
            "state_code":   vehicle["state_code"]   if vehicle else None,
            "blacklisted":  bool(vehicle["is_blacklisted"]) if vehicle else False,
        },
        "fastag": {
            "found":        fastag is not None,
            "fastag_id":    fastag["fastag_id"]   if fastag else None,
            "bank":         fastag["bank_name"]   if fastag else None,
            "balance_before": balance_before,
            "balance_after":  balance_after,
            "active":       bool(fastag["is_active"]) if fastag else False,
        },
        "toll": {
            "amount":       toll_amount,
            "gate_id":      gate_id,
            "gate_name":    gate_info["gate_name"]  if gate_info else "Unknown Gate",
            "location":     gate_info["location"]   if gate_info else None,
        },
        "fraud": {
            "is_fraud":     fraud.is_fraud,
            "score":        fraud.fraud_score,
            "score_pct":    int(fraud.fraud_score * 100),
            "types":        fraud.fraud_types,
            "descriptions": fraud.descriptions,
            "severity":     fraud.severity,
            "recommendation": fraud.recommendation,
            "details":      fraud.details,
        },
        "transaction": {
            "status":       status,
            "barrier":      "OPEN" if status == "success" else "CLOSED",
            "processed_at": datetime.now().isoformat(),
            "evidence_path": evidence_path,
        },
        "history": risk_history,
    }


def _log_notification(conn, plate, tid, status, fastag, vehicle, fraud):
    """Log SMS notifications (actual sending via Twilio in notifications.py)."""
    try:
        owner_phone = vehicle["owner_phone"] if vehicle else None
        admin_phone = "+919999999999"  # admin number from config

        if status == "success":
            owner_msg = (
                f"TOLL PAID: ₹{fastag['balance'] if fastag else 0} deducted for vehicle {plate}. "
                f"TxnID: {tid}. New balance: ₹{round(fastag['balance'],2) if fastag else 0}."
            )
            conn.execute(
                "INSERT INTO notifications (recipient_phone,recipient_type,message,transaction_id) VALUES (?,?,?,?)",
                (owner_phone, "owner", owner_msg, tid)
            )
        else:
            admin_msg = (
                f"🚨 FRAUD ALERT: Vehicle {plate} | Severity: {fraud.severity.upper()} | "
                f"Types: {', '.join(fraud.fraud_types)} | TxnID: {tid}"
            )
            conn.execute(
                "INSERT INTO notifications (recipient_phone,recipient_type,message,transaction_id) VALUES (?,?,?,?)",
                (admin_phone, "admin", admin_msg, tid)
            )
        conn.commit()
    except Exception:
        pass
