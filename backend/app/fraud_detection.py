"""
fraud_detection.py - Multi-rule AI Fraud Detection Engine
Detects fraud using rule-based scoring + anomaly detection heuristics.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Tuple
import re
from datetime import datetime, timedelta


# ─── Fraud Types ─────────────────────────────────────────────────────────────

FRAUD_TYPES = {
    "FASTAG_MISMATCH":       ("FASTag ID does not match the registered vehicle",       0.95, "critical"),
    "INSUFFICIENT_BALANCE":  ("FASTag balance is below the required toll amount",      0.60, "medium"),
    "BLACKLISTED_VEHICLE":   ("Vehicle is blacklisted by traffic authority",           0.99, "critical"),
    "FAKE_PLATE":            ("Number plate pattern is invalid or forged",             0.90, "high"),
    "VEHICLE_TYPE_MISMATCH": ("Detected vehicle type doesn't match FASTag category",  0.80, "high"),
    "INACTIVE_FASTAG":       ("FASTag account is deactivated",                         0.85, "high"),
    "RAPID_REPEAT":          ("Vehicle appeared at multiple gates within short time",  0.75, "high"),
    "SUSPICIOUS_OCR":        ("Heavily obscured or unreadable plate detected",         0.45, "medium"),
    "PATTERN_ANOMALY":       ("Plate number contains suspicious character pattern",    0.65, "medium"),
    "ZERO_BALANCE":          ("FASTag balance is exactly zero",                        0.70, "medium"),
}

VALID_STATE_CODES = {
    "AN","AP","AR","AS","BR","CG","CH","DD","DL","DN","GA","GJ","HP","HR",
    "JH","JK","KA","KL","LA","LD","MH","ML","MN","MP","MZ","NL","OD","PB",
    "PY","RJ","SK","TN","TR","TS","UK","UP","WB",
}


@dataclass
class FraudResult:
    is_fraud:       bool
    fraud_score:    float               # 0.0 – 1.0
    fraud_types:    List[str]           = field(default_factory=list)
    descriptions:   List[str]          = field(default_factory=list)
    severity:       str                 = "low"   # low / medium / high / critical
    recommendation: str                 = "Allow"
    details:        dict               = field(default_factory=dict)


# ─── Core Engine ─────────────────────────────────────────────────────────────

class FraudDetectionEngine:
    """
    Rule-based fraud scoring engine.
    Each rule contributes a weight (0-1). Final score = weighted average.
    Score ≥ 0.5 → Fraud.
    """

    def __init__(self, db_conn=None):
        self.conn = db_conn

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(
        self,
        plate_number:     str,
        fastag_id:        Optional[str],
        fastag_record:    Optional[dict],
        vehicle_record:   Optional[dict],
        toll_amount:      float,
        detected_type:    str,
        ocr_confidence:   float = 1.0,
        gate_id:          int   = 1,
    ) -> FraudResult:

        flags:  List[Tuple[str, float]] = []   # (fraud_type_key, weight)
        details: dict = {}

        # 1. Plate format validation
        plate_valid, plate_msg = self._validate_plate(plate_number)
        if not plate_valid:
            flags.append(("FAKE_PLATE", FRAUD_TYPES["FAKE_PLATE"][1]))
            details["plate_issue"] = plate_msg

        # 2. OCR confidence
        if ocr_confidence < 0.60:
            flags.append(("SUSPICIOUS_OCR", FRAUD_TYPES["SUSPICIOUS_OCR"][1]))
            details["ocr_confidence"] = ocr_confidence

        # 3. Vehicle blacklisted
        if vehicle_record and vehicle_record.get("is_blacklisted"):
            flags.append(("BLACKLISTED_VEHICLE", FRAUD_TYPES["BLACKLISTED_VEHICLE"][1]))
            details["blacklist_reason"] = vehicle_record.get("blacklist_reason","Unknown reason")

        # 4. FASTag not found (No longer considered fraud, just requires manual payment)
        if fastag_record is None:
            details["fastag_issue"] = "No FASTag account found for this vehicle. Manual payment required."

        else:
            # 5. FASTag plate mismatch
            if fastag_record.get("plate_number","").upper() != plate_number.upper():
                flags.append(("FASTAG_MISMATCH", FRAUD_TYPES["FASTAG_MISMATCH"][1]))
                details["fastag_plate"] = fastag_record.get("plate_number")
                details["detected_plate"] = plate_number

            # 6. FASTag inactive
            if not fastag_record.get("is_active", 1):
                flags.append(("INACTIVE_FASTAG", FRAUD_TYPES["INACTIVE_FASTAG"][1]))

            # 7. Zero balance
            balance = fastag_record.get("balance", 0.0)
            if balance == 0.0:
                flags.append(("ZERO_BALANCE", FRAUD_TYPES["ZERO_BALANCE"][1]))
                details["balance"] = balance

            # 8. Insufficient balance
            elif balance < toll_amount:
                flags.append(("INSUFFICIENT_BALANCE", FRAUD_TYPES["INSUFFICIENT_BALANCE"][1]))
                details["balance"] = balance
                details["required"] = toll_amount

            # 9. Vehicle type mismatch (FASTag category vs detected)
            ft_vtype = fastag_record.get("vehicle_type","car").lower()
            if ft_vtype != detected_type.lower():
                flags.append(("VEHICLE_TYPE_MISMATCH", FRAUD_TYPES["VEHICLE_TYPE_MISMATCH"][1]))
                details["fastag_type"] = ft_vtype
                details["detected_type"] = detected_type

        # 10. Rapid repeat check (DB-based)
        if self.conn and self._is_rapid_repeat(plate_number, gate_id):
            flags.append(("RAPID_REPEAT", FRAUD_TYPES["RAPID_REPEAT"][1]))

        # 11. Pattern anomaly in plate
        if self._has_pattern_anomaly(plate_number):
            flags.append(("PATTERN_ANOMALY", FRAUD_TYPES["PATTERN_ANOMALY"][1]))

        # ── Score Calculation ─────────────────────────────────────────────────
        if not flags:
            score = 0.0
        else:
            total_weight = sum(w for _, w in flags)
            # Weighted score with diminishing returns for multiple flags
            score = min(total_weight / (len(flags) * 1.0), 1.0)
            # Boost score if critical flags present
            critical_keys = {"BLACKLISTED_VEHICLE", "FAKE_PLATE", "FASTAG_MISMATCH"}
            if any(k in critical_keys for k, _ in flags):
                score = max(score, 0.85)

        score = round(score, 3)
        is_fraud = score >= 0.50

        # ── Severity ──────────────────────────────────────────────────────────
        if score >= 0.90:   severity = "critical"
        elif score >= 0.75: severity = "high"
        elif score >= 0.50: severity = "medium"
        else:               severity = "low"

        # ── Recommendation ────────────────────────────────────────────────────
        if is_fraud:
            if severity in ("critical","high"):
                recommendation = "Block & Alert Authorities"
            else:
                recommendation = "Block & Notify Admin"
        else:
            recommendation = "Allow — Deduct Toll"

        fraud_type_keys  = [k for k, _ in flags]
        descriptions     = [FRAUD_TYPES[k][0] for k in fraud_type_keys if k in FRAUD_TYPES]

        return FraudResult(
            is_fraud       = is_fraud,
            fraud_score    = score,
            fraud_types    = fraud_type_keys,
            descriptions   = descriptions,
            severity       = severity,
            recommendation = recommendation,
            details        = details,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _validate_plate(self, plate: str) -> Tuple[bool, str]:
        """Indian number plate format: SS##XX####  e.g. TN09AB1234"""
        plate = plate.strip().upper().replace(" ", "").replace("-", "")
        # Regex: 2 letters, 2 digits, 1-3 letters, 4 digits
        pattern = r'^([A-Z]{2})(\d{2})([A-Z]{1,3})(\d{4})$'
        m = re.match(pattern, plate)
        if not m:
            return False, f"Plate '{plate}' does not match Indian format (e.g. TN09AB1234)"
        state = m.group(1)
        if state not in VALID_STATE_CODES:
            return False, f"State code '{state}' is not a valid Indian state code"
        return True, "OK"

    def _is_rapid_repeat(self, plate: str, gate_id: int, minutes: int = 10) -> bool:
        """Check if same plate appeared at a DIFFERENT gate in last N minutes."""
        try:
            cutoff = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
            cursor = self.conn.execute("""
                SELECT COUNT(*) as cnt FROM transactions
                WHERE plate_number=? AND gate_id != ? AND processed_at >= ? AND status='success'
            """, (plate, gate_id, cutoff))
            row = cursor.fetchone()
            return row and row["cnt"] > 0
        except Exception:
            return False

    def _has_pattern_anomaly(self, plate: str) -> bool:
        """Detect plates with all-zero digit sections or repeating chars."""
        plate = plate.strip().upper().replace(" ", "")
        # All zeros in digit section
        if "0000" in plate:
            return True
        # Repeating character runs (AAAA, 1111)
        for ch in set(plate):
            if plate.count(ch) >= 5:
                return True
        return False

    # ── Batch Analysis ────────────────────────────────────────────────────────

    def get_risk_summary(self, plate: str) -> dict:
        """Return historical fraud stats for a plate."""
        if not self.conn:
            return {}
        try:
            cur = self.conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status='fraud' THEN 1 ELSE 0 END) as fraud_count,
                    AVG(fraud_score) as avg_score,
                    MAX(processed_at) as last_seen
                FROM transactions WHERE plate_number=?
            """, (plate,))
            row = cur.fetchone()
            if row:
                return dict(row)
        except Exception:
            pass
        return {}


# ─── Singleton ───────────────────────────────────────────────────────────────

_engine: Optional[FraudDetectionEngine] = None

def get_engine(db_conn=None) -> FraudDetectionEngine:
    global _engine
    if _engine is None or db_conn is not None:
        _engine = FraudDetectionEngine(db_conn)
    return _engine
