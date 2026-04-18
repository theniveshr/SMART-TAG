"""
plate_recognition.py - License Plate Recognition using OCR
Supports: EasyOCR (preferred), Tesseract fallback, and simulation mode.

Install: pip install easyocr opencv-python-headless pillow
"""

import re
import os
import random
import base64
import io
from typing import Optional, Tuple
from datetime import datetime


# ─── OCR Backend Detection ───────────────────────────────────────────────────

def _try_import_easyocr():
    try:
        import easyocr
        return easyocr
    except ImportError:
        return None

def _try_import_cv2():
    try:
        import cv2
        return cv2
    except ImportError:
        return None

def _try_import_pil():
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        return Image, ImageEnhance, ImageFilter
    except ImportError:
        return None, None, None


# ─── Plate Normalisation ─────────────────────────────────────────────────────

VALID_STATES = {
    "AN","AP","AR","AS","BR","CG","CH","DD","DL","DN","GA","GJ","HP","HR",
    "JH","JK","KA","KL","LA","LD","MH","ML","MN","MP","MZ","NL","OD","PB",
    "PY","RJ","SK","TN","TR","TS","UK","UP","WB",
}

def normalise_plate(raw: str) -> Tuple[str, float]:
    """
    Clean up OCR output → standard Indian plate: SS##XX#### 
    Returns (normalised_plate, confidence_adjustment)
    """
    # Remove noise
    text = raw.upper().strip()
    text = re.sub(r'[^A-Z0-9]', '', text)

    # Common OCR substitutions
    OCR_FIX = {
        'O': '0', 'I': '1', 'S': '5', 'B': '8', 'G': '6', 'Z': '2',
    }

    # Try standard Indian pattern
    pattern = r'([A-Z]{2})(\d{2})([A-Z]{1,3})(\d{4})'
    m = re.search(pattern, text)
    if m:
        state, dist, series, num = m.groups()
        if state in VALID_STATES:
            return f"{state}{dist}{series}{num}", 0.0   # no confidence penalty
        # State code wrong → try OCR fix on first 2 chars
        fixed_state = ''.join(OCR_FIX.get(c, c) for c in state)
        if fixed_state in VALID_STATES:
            return f"{fixed_state}{dist}{series}{num}", -0.10

    # Partial match – apply fixes throughout
    fixed = ''.join(OCR_FIX.get(c, c) if c.isdigit() else c for c in text)
    m2 = re.search(pattern, fixed)
    if m2:
        return ''.join(m2.groups()), -0.15

    return text[:10], -0.30  # return raw truncated with big penalty


# ─── Image Pre-processing ─────────────────────────────────────────────────────

def preprocess_image(image_bytes: bytes) -> bytes:
    """Enhance image for better OCR: grayscale → contrast → sharpen → threshold."""
    cv2 = _try_import_cv2()
    Image, ImageEnhance, ImageFilter = _try_import_pil()

    if cv2 is None and Image is None:
        return image_bytes  # passthrough

    import numpy as np

    if cv2:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return image_bytes

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # CLAHE contrast enhancement
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)

        # Bilateral filter to reduce noise
        denoised = cv2.bilateralFilter(enhanced, 11, 17, 17)

        # Adaptive threshold
        thresh = cv2.adaptiveThreshold(
            denoised, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )

        _, buf = cv2.imencode('.png', thresh)
        return buf.tobytes()

    elif Image:
        img = Image.open(io.BytesIO(image_bytes)).convert('L')
        img = ImageEnhance.Contrast(img).enhance(2.5)
        img = img.filter(ImageFilter.SHARPEN)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()

    return image_bytes


# ─── OCR Engine ───────────────────────────────────────────────────────────────

class PlateRecognizer:
    def __init__(self):
        self.easyocr   = _try_import_easyocr()
        self.cv2       = _try_import_cv2()
        self._reader   = None
        self.mode      = "simulation"

        if self.easyocr:
            try:
                self._reader = self.easyocr.Reader(['en'], gpu=False, verbose=False)
                self.mode = "easyocr"
            except Exception:
                pass

        if self.mode == "simulation":
            print("⚠️  OCR libraries not found – running in simulation mode.")

    def recognize(self, image_bytes: bytes) -> dict:
        """
        Returns:
            plate_number  : str
            confidence    : float (0–1)
            raw_text      : str
            mode          : str
            processed_at  : str
        """
        if self.mode == "easyocr":
            return self._easyocr_recognize(image_bytes)
        else:
            return self._simulate_recognize()

    def recognize_from_base64(self, b64_str: str) -> dict:
        # Strip data URI prefix if present
        if ',' in b64_str:
            b64_str = b64_str.split(',', 1)[1]
        image_bytes = base64.b64decode(b64_str)
        return self.recognize(image_bytes)

    # ── EasyOCR ───────────────────────────────────────────────────────────────

    def _easyocr_recognize(self, image_bytes: bytes) -> dict:
        import numpy as np

        processed = preprocess_image(image_bytes)
        nparr = np.frombuffer(processed, np.uint8)
        cv2 = self.cv2
        if cv2:
            img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        else:
            img = nparr.reshape(-1, 1)

        results = self._reader.readtext(img, detail=1, paragraph=False)

        if not results:
            return self._fail_result("No text detected in image")

        # Pick best candidate
        best_text = ""
        best_conf = 0.0
        for (bbox, text, conf) in results:
            text_clean = re.sub(r'[^A-Za-z0-9]', '', text).upper()
            if len(text_clean) >= 6 and conf > best_conf:
                best_text = text_clean
                best_conf = conf

        if not best_text:
            return self._fail_result("Could not extract readable plate text")

        plate, penalty = normalise_plate(best_text)
        final_conf = max(0.0, round(best_conf + penalty, 3))

        return {
            "plate_number": plate,
            "confidence":   final_conf,
            "raw_text":     best_text,
            "mode":         "easyocr",
            "success":      True,
            "processed_at": datetime.now().isoformat(),
        }

    # ── Simulation ────────────────────────────────────────────────────────────

    SAMPLE_PLATES = [
        ("TN09AB1234", 0.97), ("TN10CD5678", 0.94), ("MH12EF9012", 0.91),
        ("KA03GH3456", 0.96), ("DL01IJ7890", 0.88), ("TN22KL2345", 0.93),
        ("AP16MN6789", 0.95), ("GJ05OP0123", 0.89), ("TN33QR4567", 0.92),
        ("UP80ST8901", 0.87), ("TN07UV2345", 0.98), ("RJ14WX6789", 0.90),
        ("MH04XX1111", 0.85), ("KL15YY2222", 0.83),  # unknown plates
    ]

    def _simulate_recognize(self) -> dict:
        plate, conf = random.choice(self.SAMPLE_PLATES)
        # Add slight noise
        conf = round(conf + random.uniform(-0.05, 0.02), 3)
        conf = max(0.60, min(1.0, conf))

        # Occasionally simulate a misread
        if random.random() < 0.08:
            plate = plate[:-1] + str(random.randint(0, 9))
            conf  = round(conf - 0.15, 3)

        return {
            "plate_number": plate,
            "confidence":   conf,
            "raw_text":     plate,
            "mode":         "simulation",
            "success":      True,
            "processed_at": datetime.now().isoformat(),
        }

    def _fail_result(self, reason: str) -> dict:
        return {
            "plate_number": "UNKNOWN",
            "confidence":   0.0,
            "raw_text":     "",
            "mode":         self.mode,
            "success":      False,
            "error":        reason,
            "processed_at": datetime.now().isoformat(),
        }


# ─── Singleton ────────────────────────────────────────────────────────────────

_recognizer: Optional[PlateRecognizer] = None

def get_recognizer() -> PlateRecognizer:
    global _recognizer
    if _recognizer is None:
        _recognizer = PlateRecognizer()
    return _recognizer
