"""
main.py - FastAPI Backend for AI Toll Gate Monitoring & Fraud Detection System
Run: uvicorn app.main:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import hashlib, base64, os, io
from datetime import datetime, timedelta

from .database      import get_connection, init_db
from .toll_processing import process_vehicle
from .fraud_detection import get_engine

app = FastAPI(
    title="AI Toll Gate Monitoring & Fraud Detection System",
    version="3.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "../../frontend")

@app.on_event("startup")
def on_startup():
    init_db()
    print("Toll Gate System v3 started.")


# ─── Pydantic Models ─────────────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    image_base64: Optional[str] = None
    gate_id: int = 1
    manual_plate: Optional[str] = None
    manual_type: Optional[str] = None

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role: str = "operator"
    gate_id: Optional[int] = None

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    gate_id: Optional[int] = None
    is_active: Optional[int] = None
    password: Optional[str] = None

class VehicleCreate(BaseModel):
    plate_number: str
    owner_name: str
    owner_phone: Optional[str] = None
    owner_email: Optional[str] = None
    vehicle_type: str = "car"
    fuel_type: Optional[str] = None
    state_code: Optional[str] = None

class VehicleUpdate(BaseModel):
    owner_name: Optional[str] = None
    owner_phone: Optional[str] = None
    owner_email: Optional[str] = None
    vehicle_type: Optional[str] = None
    fuel_type: Optional[str] = None
    state_code: Optional[str] = None

class FASTagCreate(BaseModel):
    fastag_id: str
    plate_number: str
    bank_name: str
    balance: float
    vehicle_type: str = "car"
    owner_name: Optional[str] = None
    owner_phone: Optional[str] = None

class FASTagUpdate(BaseModel):
    bank_name: Optional[str] = None
    balance: Optional[float] = None
    is_active: Optional[int] = None
    vehicle_type: Optional[str] = None

class TopUpRequest(BaseModel):
    fastag_id: str
    amount: float

class BlacklistRequest(BaseModel):
    plate_number: str
    reason: str

# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
def login(req: LoginRequest):
    conn = get_connection()
    pw_hash = hashlib.sha256(req.password.encode()).hexdigest()
    cur = conn.execute(
        "SELECT * FROM admin_users WHERE username=? AND password_hash=? AND is_active=1",
        (req.username, pw_hash)
    )
    user = cur.fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid credentials or account disabled")
    conn.execute("UPDATE admin_users SET last_login=datetime('now') WHERE id=?", (user["id"],))
    conn.commit()
    conn.close()
    return {
        "success": True,
        "id": user["id"],
        "username": user["username"],
        "full_name": user["full_name"],
        "email": user["email"],
        "phone": user["phone"],
        "role": user["role"],
        "gate_id": user["gate_id"],
        "avatar_initials": user["avatar_initials"] or (user["full_name"] or "U")[:2].upper(),
        "token": f"tok_{hashlib.md5(req.username.encode()).hexdigest()}",
    }

@app.post("/api/auth/register")
def register(req: RegisterRequest):
    """Register new user - only superadmin can create superadmin/admin roles"""
    conn = get_connection()
    pw_hash = hashlib.sha256(req.password.encode()).hexdigest()
    initials = ''.join(w[0] for w in (req.full_name or req.username).split()[:2]).upper()
    allowed_roles = ["superadmin","admin","analyst","operator","viewer"]
    if req.role not in allowed_roles:
        conn.close()
        raise HTTPException(400, f"Invalid role. Allowed: {allowed_roles}")
    try:
        conn.execute("""
            INSERT INTO admin_users (username,password_hash,full_name,email,phone,role,avatar_initials,gate_id)
            VALUES (?,?,?,?,?,?,?,?)
        """, (req.username, pw_hash, req.full_name, req.email, req.phone, req.role, initials, req.gate_id))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"Username already exists: {str(e)}")
    conn.close()
    return {"success": True, "username": req.username, "role": req.role}

# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat(), "version": "3.0.0"}

# ─── Core Processing ──────────────────────────────────────────────────────────

@app.post("/api/process")
def process_toll(req: ProcessRequest):
    try:
        result = process_vehicle(
            image_b64=req.image_base64 or "",
            gate_id=req.gate_id,
            manual_plate=req.manual_plate,
            manual_type=req.manual_type,
        )
        return result
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/process/image")
async def process_toll_image(
    file: UploadFile = File(...),
    gate_id: int = Form(1),
    manual_plate: str = Form(None),
    manual_type: str = Form(None),
):
    """Upload image file for OCR + fraud detection."""
    image_bytes = await file.read()
    image_b64   = base64.b64encode(image_bytes).decode()
    result = process_vehicle(
        image_b64=image_b64,
        gate_id=gate_id,
        manual_plate=manual_plate,
        manual_type=manual_type,
    )
    return result

@app.post("/api/process/video")
async def process_toll_video(
    file: UploadFile = File(...),
    gate_id: int = Form(1),
):
    """Extract frames from video and process the best frame for plate detection."""
    import tempfile, cv2 as _cv2
    video_bytes = await file.read()

    # Save to temp file
    suffix = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(video_bytes)
        tmp_path = tmp.name

    best_frame_b64 = None
    try:
        cap = _cv2.VideoCapture(tmp_path)
        frame_count = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(_cv2.CAP_PROP_FPS) or 25
        # Sample frames at 0.5s intervals
        sample_frames = list(range(0, frame_count, max(1, int(fps * 0.5))))[:20]
        best_var = -1
        for fi in sample_frames:
            cap.set(_cv2.CAP_PROP_POS_FRAMES, fi)
            ret, frame = cap.read()
            if not ret:
                continue
            gray = _cv2.cvtColor(frame, _cv2.COLOR_BGR2GRAY)
            var = gray.var()
            if var > best_var:
                best_var = var
                _, buf = _cv2.imencode(".jpg", frame)
                best_frame_b64 = base64.b64encode(buf.tobytes()).decode()
        cap.release()
    except Exception as e:
        os.unlink(tmp_path)
        raise HTTPException(500, f"Video processing error: {str(e)}")
    finally:
        try: os.unlink(tmp_path)
        except: pass

    if not best_frame_b64:
        raise HTTPException(400, "Could not extract frames from video")

    result = process_vehicle(
        image_b64=best_frame_b64,
        gate_id=gate_id,
    )
    result["video_info"] = {
        "frames_analyzed": len(sample_frames),
        "total_frames": frame_count,
        "best_frame_variance": round(best_var, 2),
    }
    return result

@app.post("/api/simulate")
def simulate_vehicle(plate: str = Query(None), gate_id: int = Query(1), vtype: str = Query(None)):
    result = process_vehicle(image_b64="", gate_id=gate_id, manual_plate=plate, manual_type=vtype)
    return result

# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/api/dashboard/stats")
def dashboard_stats(gate_id: Optional[int] = Query(None)):
    conn = get_connection()
    today = datetime.now().strftime("%Y-%m-%d")
    gw = "AND gate_id = ?" if gate_id else ""
    ga = [gate_id] if gate_id else []
    stats = {}
    r = conn.execute(f"SELECT COUNT(*) as total, SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as success, SUM(CASE WHEN status='fraud' THEN 1 ELSE 0 END) as fraud, SUM(CASE WHEN status='success' THEN toll_amount ELSE 0 END) as revenue FROM transactions WHERE date(processed_at)=? {gw}", [today]+ga).fetchone()
    stats["today"] = dict(r)
    r2 = conn.execute(f"SELECT COUNT(*) as total, SUM(CASE WHEN status='fraud' THEN 1 ELSE 0 END) as fraud, SUM(CASE WHEN status='success' THEN toll_amount ELSE 0 END) as revenue FROM transactions WHERE 1=1 {gw}", ga).fetchone()
    stats["all_time"] = dict(r2)
    stats["weekly"] = [dict(r) for r in conn.execute(f"SELECT date(processed_at) as day, COUNT(*) as count, SUM(CASE WHEN status='fraud' THEN 1 ELSE 0 END) as fraud_count, SUM(CASE WHEN status='success' THEN toll_amount ELSE 0 END) as revenue FROM transactions WHERE processed_at >= date('now','-7 days') {gw} GROUP BY day ORDER BY day", ga).fetchall()]
    stats["top_fraud_types"] = [dict(r) for r in conn.execute(f"SELECT fraud_type, COUNT(*) as count FROM transactions WHERE status='fraud' AND fraud_type IS NOT NULL {gw} GROUP BY fraud_type ORDER BY count DESC LIMIT 5", ga).fetchall()]
    stats["vehicle_types"] = [dict(r) for r in conn.execute(f"SELECT vehicle_type, COUNT(*) as count, SUM(CASE WHEN status='success' THEN toll_amount ELSE 0 END) as revenue FROM transactions WHERE 1=1 {gw} GROUP BY vehicle_type ORDER BY count DESC", ga).fetchall()]
    stats["open_alerts"] = conn.execute("SELECT COUNT(*) as cnt FROM fraud_alerts WHERE is_resolved=0").fetchone()["cnt"]
    conn.close()
    return stats

@app.get("/api/dashboard/live")
def live_feed(limit: int = Query(20, ge=1, le=100)):
    conn = get_connection()
    rows = [dict(r) for r in conn.execute("SELECT t.*, g.gate_name FROM transactions t LEFT JOIN toll_gates g ON t.gate_id=g.id ORDER BY t.processed_at DESC LIMIT ?", (limit,)).fetchall()]
    conn.close()
    return {"transactions": rows, "count": len(rows)}

# ─── Transactions ─────────────────────────────────────────────────────────────

@app.get("/api/transactions")
def list_transactions(page: int = Query(1,ge=1), limit: int = Query(25,ge=1,le=100),
    status: str = None, plate: str = None, gate_id: int = None,
    date_from: str = None, date_to: str = None):
    conn = get_connection()
    where, args = ["1=1"], []
    if status:    where.append("t.status=?");              args.append(status)
    if plate:     where.append("t.plate_number LIKE ?");   args.append(f"%{plate}%")
    if gate_id:   where.append("t.gate_id=?");             args.append(gate_id)
    if date_from: where.append("date(t.processed_at)>=?"); args.append(date_from)
    if date_to:   where.append("date(t.processed_at)<=?"); args.append(date_to)
    ws = " AND ".join(where)
    rows = [dict(r) for r in conn.execute(f"SELECT t.*, g.gate_name FROM transactions t LEFT JOIN toll_gates g ON t.gate_id=g.id WHERE {ws} ORDER BY t.processed_at DESC LIMIT ? OFFSET ?", args+[limit,(page-1)*limit]).fetchall()]
    total = conn.execute(f"SELECT COUNT(*) as cnt FROM transactions t WHERE {ws}", args).fetchone()["cnt"]
    conn.close()
    return {"transactions": rows, "total": total, "page": page, "pages": (total+limit-1)//limit}

@app.get("/api/transactions/export/pdf")
def export_transactions_pdf(status: str = None, date_from: str = None, date_to: str = None):
    """Export transactions as PDF."""
    conn = get_connection()
    where, args = ["1=1"], []
    if status:    where.append("t.status=?");              args.append(status)
    if date_from: where.append("date(t.processed_at)>=?"); args.append(date_from)
    if date_to:   where.append("date(t.processed_at)<=?"); args.append(date_to)
    ws = " AND ".join(where)
    rows = [dict(r) for r in conn.execute(f"SELECT t.*, g.gate_name FROM transactions t LEFT JOIN toll_gates g ON t.gate_id=g.id WHERE {ws} ORDER BY t.processed_at DESC LIMIT 500", args).fetchall()]
    conn.close()
    return _generate_pdf(
        title="Transaction Report",
        headers=["Transaction ID","Plate","Gate","Vehicle","Amount","Status","Fraud%","Date"],
        data=[[r["transaction_id"],r["plate_number"],r["gate_name"] or "—",r["vehicle_type"] or "—",
               f'Rs.{r["toll_amount"]}',r["status"],f'{int((r["fraud_score"] or 0)*100)}%',
               str(r["processed_at"])[:16]] for r in rows],
        filename="transactions.pdf"
    )

@app.get("/api/transactions/{tid}")
def get_transaction(tid: str):
    conn = get_connection()
    row = conn.execute("SELECT t.*, g.gate_name, g.location FROM transactions t LEFT JOIN toll_gates g ON t.gate_id=g.id WHERE t.transaction_id=?", (tid,)).fetchone()
    conn.close()
    if not row: raise HTTPException(404, "Transaction not found")
    return dict(row)

# ─── Fraud Alerts ─────────────────────────────────────────────────────────────

@app.get("/api/fraud/alerts")
def list_alerts(resolved: Optional[bool] = None, severity: str = None, limit: int = Query(50,ge=1,le=200)):
    conn = get_connection()
    where, args = ["1=1"], []
    if resolved is not None: where.append("is_resolved=?"); args.append(int(resolved))
    if severity:             where.append("severity=?");    args.append(severity)
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM fraud_alerts WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?", args+[limit]).fetchall()]
    conn.close()
    return {"alerts": rows, "count": len(rows)}

@app.patch("/api/fraud/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, resolved_by: str = "admin"):
    conn = get_connection()
    conn.execute("UPDATE fraud_alerts SET is_resolved=1, resolved_by=?, resolved_at=datetime('now') WHERE id=?", (resolved_by, alert_id))
    conn.commit(); conn.close()
    return {"success": True}

# ─── Vehicles ─────────────────────────────────────────────────────────────────

@app.get("/api/vehicles")
def list_vehicles(search: str = None, blacklisted: bool = None):
    conn = get_connection()
    where, args = ["1=1"], []
    if search:     where.append("(plate_number LIKE ? OR owner_name LIKE ?)"); args += [f"%{search}%"]*2
    if blacklisted is not None: where.append("is_blacklisted=?"); args.append(int(blacklisted))
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM vehicles WHERE {' AND '.join(where)} ORDER BY registered_at DESC", args).fetchall()]
    conn.close()
    return {"vehicles": rows, "count": len(rows)}

@app.get("/api/vehicles/export/pdf")
def export_vehicles_pdf():
    conn = get_connection()
    rows = [dict(r) for r in conn.execute("SELECT * FROM vehicles ORDER BY registered_at DESC LIMIT 500").fetchall()]
    conn.close()
    return _generate_pdf(
        title="Vehicle Registry Report",
        headers=["Plate","Owner","Phone","Type","Fuel","State","Status","Registered"],
        data=[[r["plate_number"],r["owner_name"],r["owner_phone"] or "—",r["vehicle_type"],
               r["fuel_type"] or "—",r["state_code"] or "—",
               "Blacklisted" if r["is_blacklisted"] else "Active",
               str(r["registered_at"])[:10]] for r in rows],
        filename="vehicles.pdf"
    )

@app.get("/api/vehicles/{plate}")
def get_vehicle(plate: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM vehicles WHERE plate_number=?", (plate.upper(),)).fetchone()
    if not row: conn.close(); raise HTTPException(404, "Vehicle not found")
    veh = dict(row)
    veh["fastags"] = [dict(r) for r in conn.execute("SELECT * FROM fastag WHERE plate_number=?", (plate.upper(),)).fetchall()]
    veh["recent_transactions"] = [dict(r) for r in conn.execute("SELECT * FROM transactions WHERE plate_number=? ORDER BY processed_at DESC LIMIT 10", (plate.upper(),)).fetchall()]
    conn.close()
    return veh

@app.post("/api/vehicles")
def create_vehicle(v: VehicleCreate):
    conn = get_connection()
    try:
        conn.execute("INSERT INTO vehicles (plate_number,owner_name,owner_phone,owner_email,vehicle_type,fuel_type,state_code) VALUES (?,?,?,?,?,?,?)",
                     (v.plate_number.upper(),v.owner_name,v.owner_phone,v.owner_email,v.vehicle_type,v.fuel_type,v.state_code))
        conn.commit()
    except Exception as e:
        conn.close(); raise HTTPException(400, str(e))
    conn.close()
    return {"success": True, "plate_number": v.plate_number.upper()}

@app.put("/api/vehicles/{plate}")
def update_vehicle(plate: str, v: VehicleUpdate):
    conn = get_connection()
    fields = {k: val for k, val in v.dict().items() if val is not None}
    if not fields: conn.close(); raise HTTPException(400, "No fields to update")
    sql = "UPDATE vehicles SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE plate_number=?"
    conn.execute(sql, list(fields.values()) + [plate.upper()])
    conn.commit(); conn.close()
    return {"success": True}

@app.delete("/api/vehicles/{plate}")
def delete_vehicle(plate: str):
    conn = get_connection()
    conn.execute("DELETE FROM vehicles WHERE plate_number=?", (plate.upper(),))
    conn.commit(); conn.close()
    return {"success": True}

@app.post("/api/vehicles/blacklist")
def blacklist_vehicle(req: BlacklistRequest):
    conn = get_connection()
    conn.execute("UPDATE vehicles SET is_blacklisted=1, blacklist_reason=? WHERE plate_number=?", (req.reason, req.plate_number.upper()))
    conn.commit(); conn.close()
    return {"success": True}

# ─── FASTag ───────────────────────────────────────────────────────────────────

@app.get("/api/fastag")
def list_fastag(search: str = None):
    conn = get_connection()
    if search:
        rows = [dict(r) for r in conn.execute("SELECT * FROM fastag WHERE (fastag_id LIKE ? OR plate_number LIKE ? OR owner_name LIKE ?) ORDER BY created_at DESC", [f"%{search}%"]*3).fetchall()]
    else:
        rows = [dict(r) for r in conn.execute("SELECT * FROM fastag ORDER BY created_at DESC").fetchall()]
    conn.close()
    return {"fastags": rows, "count": len(rows)}

@app.get("/api/fastag/export/pdf")
def export_fastag_pdf():
    conn = get_connection()
    rows = [dict(r) for r in conn.execute("SELECT * FROM fastag ORDER BY created_at DESC LIMIT 500").fetchall()]
    conn.close()
    return _generate_pdf(
        title="FASTag Accounts Report",
        headers=["FASTag ID","Plate","Owner","Bank","Balance","Type","Status"],
        data=[[r["fastag_id"],r["plate_number"],r["owner_name"] or "—",r["bank_name"] or "—",
               f'Rs.{r["balance"]:.2f}',r["vehicle_type"],
               "Active" if r["is_active"] else "Inactive"] for r in rows],
        filename="fastag.pdf"
    )

@app.get("/api/fastag/{fastag_id}")
def get_fastag(fastag_id: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM fastag WHERE fastag_id=?", (fastag_id,)).fetchone()
    conn.close()
    if not row: raise HTTPException(404, "FASTag not found")
    return dict(row)

@app.post("/api/fastag")
def create_fastag(f: FASTagCreate):
    conn = get_connection()
    try:
        conn.execute("INSERT INTO fastag (fastag_id,plate_number,bank_name,balance,vehicle_type,owner_name,owner_phone) VALUES (?,?,?,?,?,?,?)",
                     (f.fastag_id,f.plate_number.upper(),f.bank_name,f.balance,f.vehicle_type,f.owner_name,f.owner_phone))
        conn.commit()
    except Exception as e:
        conn.close(); raise HTTPException(400, str(e))
    conn.close()
    return {"success": True}

@app.put("/api/fastag/{fastag_id}")
def update_fastag(fastag_id: str, f: FASTagUpdate):
    conn = get_connection()
    fields = {k: val for k, val in f.dict().items() if val is not None}
    if not fields: conn.close(); raise HTTPException(400, "No fields")
    sql = "UPDATE fastag SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE fastag_id=?"
    conn.execute(sql, list(fields.values()) + [fastag_id])
    conn.commit(); conn.close()
    return {"success": True}

@app.delete("/api/fastag/{fastag_id}")
def delete_fastag(fastag_id: str):
    conn = get_connection()
    conn.execute("DELETE FROM fastag WHERE fastag_id=?", (fastag_id,))
    conn.commit(); conn.close()
    return {"success": True}

@app.post("/api/fastag/topup")
def topup_fastag(req: TopUpRequest):
    conn = get_connection()
    row = conn.execute("SELECT balance FROM fastag WHERE fastag_id=?", (req.fastag_id,)).fetchone()
    if not row: conn.close(); raise HTTPException(404, "FASTag not found")
    new_bal = round(row["balance"] + req.amount, 2)
    conn.execute("UPDATE fastag SET balance=? WHERE fastag_id=?", (new_bal, req.fastag_id))
    conn.commit(); conn.close()
    return {"success": True, "fastag_id": req.fastag_id, "new_balance": new_bal}

# ─── Toll Gates ───────────────────────────────────────────────────────────────

@app.get("/api/gates")
def list_gates():
    conn = get_connection()
    rows = [dict(r) for r in conn.execute("SELECT * FROM toll_gates ORDER BY id").fetchall()]
    conn.close()
    return {"gates": rows}

# ─── Users (Admin CRUD) ───────────────────────────────────────────────────────

@app.get("/api/users")
def list_users():
    conn = get_connection()
    rows = [dict(r) for r in conn.execute("SELECT id,username,full_name,email,phone,role,avatar_initials,is_active,gate_id,last_login,created_at FROM admin_users ORDER BY created_at DESC").fetchall()]
    conn.close()
    return {"users": rows, "count": len(rows)}

@app.get("/api/users/export/pdf")
def export_users_pdf():
    conn = get_connection()
    rows = [dict(r) for r in conn.execute("SELECT * FROM admin_users ORDER BY created_at DESC").fetchall()]
    conn.close()
    return _generate_pdf(
        title="System Users Report",
        headers=["Username","Full Name","Email","Role","Status","Last Login","Created"],
        data=[[r["username"],r["full_name"] or "—",r["email"] or "—",r["role"],
               "Active" if r["is_active"] else "Inactive",
               str(r["last_login"] or "Never")[:16], str(r["created_at"])[:10]] for r in rows],
        filename="users.pdf"
    )

@app.get("/api/users/{user_id}")
def get_user(user_id: int):
    conn = get_connection()
    row = conn.execute("SELECT id,username,full_name,email,phone,role,avatar_initials,is_active,gate_id,last_login,created_at FROM admin_users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not row: raise HTTPException(404, "User not found")
    return dict(row)

@app.put("/api/users/{user_id}")
def update_user(user_id: int, u: UserUpdate):
    conn = get_connection()
    fields = {}
    if u.full_name is not None: fields["full_name"] = u.full_name
    if u.email is not None:     fields["email"] = u.email
    if u.phone is not None:     fields["phone"] = u.phone
    if u.role is not None:      fields["role"] = u.role
    if u.gate_id is not None:   fields["gate_id"] = u.gate_id
    if u.is_active is not None: fields["is_active"] = u.is_active
    if u.password:
        fields["password_hash"] = hashlib.sha256(u.password.encode()).hexdigest()
    if not fields: conn.close(); raise HTTPException(400, "No fields to update")
    sql = "UPDATE admin_users SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE id=?"
    conn.execute(sql, list(fields.values()) + [user_id])
    conn.commit(); conn.close()
    return {"success": True}

@app.delete("/api/users/{user_id}")
def delete_user(user_id: int):
    conn = get_connection()
    row = conn.execute("SELECT role FROM admin_users WHERE id=?", (user_id,)).fetchone()
    if not row: conn.close(); raise HTTPException(404, "User not found")
    if row["role"] == "superadmin":
        count = conn.execute("SELECT COUNT(*) as c FROM admin_users WHERE role='superadmin'").fetchone()["c"]
        if count <= 1: conn.close(); raise HTTPException(400, "Cannot delete last superadmin")
    conn.execute("DELETE FROM admin_users WHERE id=?", (user_id,))
    conn.commit(); conn.close()
    return {"success": True}

# ─── Notifications ────────────────────────────────────────────────────────────

@app.get("/api/notifications")
def list_notifications(limit: int = Query(30, ge=1, le=100)):
    conn = get_connection()
    rows = [dict(r) for r in conn.execute("SELECT * FROM notifications ORDER BY sent_at DESC LIMIT ?", (limit,)).fetchall()]
    conn.close()
    return {"notifications": rows}

# ─── PDF Generation Helper ────────────────────────────────────────────────────

def _generate_pdf(title: str, headers: list, data: list, filename: str) -> StreamingResponse:
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.units import inch

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=0.5*inch,
                                rightMargin=0.5*inch, topMargin=0.5*inch, bottomMargin=0.5*inch)
        styles = getSampleStyleSheet()
        story = []
        story.append(Paragraph(f"SmartTag — {title}", styles["Title"]))
        story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Total Records: {len(data)}", styles["Normal"]))
        story.append(Spacer(1, 0.2*inch))

        table_data = [headers] + data
        col_count = len(headers)
        page_width = landscape(A4)[0] - inch
        col_width = page_width / col_count

        t = Table(table_data, colWidths=[col_width]*col_count, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1e3a5f")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,0), 8),
            ("FONTSIZE",   (0,1), (-1,-1), 7),
            ("GRID",       (0,0), (-1,-1), 0.4, colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f5f5f5")]),
            ("ALIGN",      (0,0), (-1,-1), "LEFT"),
            ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING", (0,0), (-1,-1), 4),
        ]))
        story.append(t)
        doc.build(story)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except ImportError:
        raise HTTPException(500, "ReportLab not installed. Run: pip install reportlab")

# ─── Static Mount ─────────────────────────────────────────────────────────────
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/")
    def root():
        return {"message": "API running. Visit /api/docs"}
