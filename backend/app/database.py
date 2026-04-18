"""
database.py - Database initialization and connection
"""
import sqlite3, os, hashlib, random
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), '../../logs/toll_system.db')

def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_number TEXT UNIQUE NOT NULL,
    owner_name TEXT NOT NULL,
    owner_phone TEXT,
    owner_email TEXT,
    vehicle_type TEXT CHECK(vehicle_type IN ('car','truck','bus','bike','van')) DEFAULT 'car',
    fuel_type TEXT,
    state_code TEXT,
    is_blacklisted INTEGER DEFAULT 0,
    blacklist_reason TEXT,
    registered_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS fastag (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fastag_id TEXT UNIQUE NOT NULL,
    plate_number TEXT NOT NULL,
    bank_name TEXT,
    balance REAL DEFAULT 0.0,
    is_active INTEGER DEFAULT 1,
    vehicle_type TEXT DEFAULT 'car',
    owner_name TEXT,
    owner_phone TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (plate_number) REFERENCES vehicles(plate_number)
);
CREATE TABLE IF NOT EXISTS toll_gates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_name TEXT NOT NULL,
    location TEXT,
    highway TEXT,
    toll_car REAL DEFAULT 65.0,
    toll_truck REAL DEFAULT 155.0,
    toll_bus REAL DEFAULT 125.0,
    toll_bike REAL DEFAULT 30.0,
    toll_van REAL DEFAULT 85.0,
    is_active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT UNIQUE NOT NULL,
    plate_number TEXT,
    fastag_id TEXT,
    gate_id INTEGER,
    toll_amount REAL,
    balance_before REAL,
    balance_after REAL,
    vehicle_type TEXT,
    status TEXT CHECK(status IN ('success','fraud','pending','failed')),
    fraud_type TEXT,
    fraud_score REAL DEFAULT 0.0,
    image_path TEXT,
    ocr_confidence REAL,
    processed_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (gate_id) REFERENCES toll_gates(id)
);
CREATE TABLE IF NOT EXISTS fraud_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT,
    plate_number TEXT,
    alert_type TEXT,
    severity TEXT CHECK(severity IN ('low','medium','high','critical')) DEFAULT 'medium',
    description TEXT,
    is_resolved INTEGER DEFAULT 0,
    resolved_by TEXT,
    resolved_at TEXT,
    evidence_image TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT,
    email TEXT,
    phone TEXT,
    role TEXT DEFAULT 'operator',
    avatar_initials TEXT,
    is_active INTEGER DEFAULT 1,
    gate_id INTEGER,
    last_login TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (gate_id) REFERENCES toll_gates(id)
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_phone TEXT,
    recipient_type TEXT CHECK(recipient_type IN ('admin','owner')),
    message TEXT,
    status TEXT DEFAULT 'sent',
    transaction_id TEXT,
    sent_at TEXT DEFAULT (datetime('now'))
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tx_plate     ON transactions(plate_number);
CREATE INDEX IF NOT EXISTS idx_tx_status    ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_tx_processed ON transactions(processed_at);
CREATE INDEX IF NOT EXISTS idx_fraud_tx     ON fraud_alerts(transaction_id);
CREATE INDEX IF NOT EXISTS idx_fastag_plate ON fastag(plate_number);
"""

def _hash(pw): return hashlib.sha256(pw.encode()).hexdigest()

VEHICLES_SEED = [
    ("TN09AB1234","Rajesh Kumar",   "+919876543210","rajesh@email.com","car",  "TN",0,None),
    ("TN10CD5678","Priya Sharma",   "+919765432109","priya@email.com", "truck","TN",0,None),
    ("MH12EF9012","Amit Patel",     "+918654321098","amit@email.com",  "bus",  "MH",0,None),
    ("KA03GH3456","Suresh Reddy",   "+917543210987","suresh@email.com","car",  "KA",0,None),
    ("DL01IJ7890","Neha Singh",     "+916432109876","neha@email.com",  "bike", "DL",0,None),
    ("TN22KL2345","Mohan Das",      "+919321098765","mohan@email.com", "van",  "TN",1,"Suspected toll evasion"),
    ("AP16MN6789","Kavita Rao",     "+918210987654","kavita@email.com","car",  "AP",0,None),
    ("GJ05OP0123","Vikram Shah",    "+917109876543","vikram@email.com","truck","GJ",0,None),
    ("TN33QR4567","Deepa Nair",     "+916098765432","deepa@email.com", "car",  "TN",1,"Fake number plate history"),
    ("UP80ST8901","Rajan Mishra",   "+915987654321","rajan@email.com", "bus",  "UP",0,None),
    ("TN07UV2345","Arjun Krishnan", "+914876543210","arjun@email.com", "car",  "TN",0,None),
    ("RJ14WX6789","Pooja Gupta",    "+913765432109","pooja@email.com", "bike", "RJ",0,None),
]
FASTAG_SEED = [
    ("FT-TN09AB1234","TN09AB1234","HDFC Bank",   850.0, 1,"car",  "Rajesh Kumar",   "+919876543210"),
    ("FT-TN10CD5678","TN10CD5678","SBI",         320.0, 1,"truck","Priya Sharma",   "+919765432109"),
    ("FT-MH12EF9012","MH12EF9012","ICICI Bank",  1200.0,1,"bus",  "Amit Patel",     "+918654321098"),
    ("FT-KA03GH3456","KA03GH3456","Axis Bank",   450.0, 1,"car",  "Suresh Reddy",   "+917543210987"),
    ("FT-DL01IJ7890","DL01IJ7890","Kotak Bank",  75.0,  1,"bike", "Neha Singh",     "+916432109876"),
    ("FT-TN22KL2345","TN22KL2345","PNB",         500.0, 0,"van",  "Mohan Das",      "+919321098765"),
    ("FT-AP16MN6789","AP16MN6789","HDFC Bank",   2000.0,1,"car",  "Kavita Rao",     "+918210987654"),
    ("FT-GJ05OP0123","GJ05OP0123","SBI",         180.0, 1,"truck","Vikram Shah",    "+917109876543"),
    ("FT-MISMATCH01","TN09AB1234","Yes Bank",    300.0, 1,"truck","Unknown",        "+910000000000"),
    ("FT-TN07UV2345","TN07UV2345","HDFC Bank",   600.0, 1,"car",  "Arjun Krishnan", "+914876543210"),
    ("FT-RJ14WX6789","RJ14WX6789","SBI",         220.0, 1,"bike", "Pooja Gupta",    "+913765432109"),
]
GATES_SEED = [
    ("NH-44 Chennai Toll Plaza","Chennai, Tamil Nadu","NH-44",65,155,125,30,85),
    ("NH-48 Bengaluru Entry",   "Bengaluru, Karnataka","NH-48",70,165,135,35,90),
    ("NH-8 Mumbai Expressway",  "Mumbai, Maharashtra","NH-8",80,185,150,40,100),
    ("NH-16 Vijayawada Gate",   "Vijayawada, AP","NH-16",60,145,120,28,80),
    ("NH-27 Jaipur North",      "Jaipur, Rajasthan","NH-27",55,135,110,25,75),
]
# Roles: superadmin > admin > analyst > operator > viewer
ADMIN_SEED = [
    ("admin",     _hash("admin@123"),    "System Administrator","admin@smarttag.in",   "+919000000001","superadmin","SA",1,None),
    ("operator1", _hash("op@pass123"),   "Gate Operator 1",     "op1@smarttag.in",     "+919000000002","operator",  "G1",1,1),
    ("analyst",   _hash("analysis@456"),"Fraud Analyst",        "analyst@smarttag.in", "+919000000003","analyst",   "FA",1,None),
    ("viewer",    _hash("view@789"),     "Report Viewer",        "viewer@smarttag.in",  "+919000000004","viewer",    "RV",1,None),
    ("operator2", _hash("op2@pass123"),  "Gate Operator 2",      "op2@smarttag.in",     "+919000000005","operator",  "G2",1,2),
]

def seed_transactions(conn, num=80):
    plates  = [r[0] for r in VEHICLES_SEED]
    gates   = list(range(1, len(GATES_SEED)+1))
    types   = ["car","truck","bus","bike","van"]
    toll_map= {"car":65,"truck":155,"bus":125,"bike":30,"van":85}
    statuses= ["success"]*7 + ["fraud"]*2 + ["failed"]*1
    fraud_types = ["FASTag mismatch","Insufficient balance","Blacklisted vehicle","Fake number plate","Vehicle type mismatch"]
    txns = []
    for i in range(num):
        plate   = random.choice(plates)
        gate    = random.choice(gates)
        vtype   = random.choice(types)
        toll    = toll_map[vtype]
        status  = random.choice(statuses)
        ftype   = random.choice(fraud_types) if status == "fraud" else None
        fscore  = round(random.uniform(0.6,0.98),2) if status=="fraud" else round(random.uniform(0,0.3),2)
        bal_b   = round(random.uniform(100,2000),2)
        bal_a   = max(0, bal_b - toll) if status=="success" else bal_b
        is_today = random.random() > 0.5
        days_ago = 0 if is_today else random.randint(1, 30)
        hours_ago = random.randint(0, min(23, datetime.now().hour) if is_today else 23)
        ts  = (datetime.now() - timedelta(days=days_ago, hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
        tid = f"TXN{i+1:06d}{random.randint(100,999)}"
        txns.append((tid,plate,f"FT-{plate}",gate,toll,bal_b,bal_a,vtype,status,ftype,fscore,None,round(random.uniform(0.75,0.99),2),ts))
    conn.executemany("""
        INSERT OR IGNORE INTO transactions
        (transaction_id,plate_number,fastag_id,gate_id,toll_amount,balance_before,balance_after,
         vehicle_type,status,fraud_type,fraud_score,image_path,ocr_confidence,processed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, txns)
    fraud_txns = [(t[0],t[1],t[9]) for t in txns if t[8]=="fraud"]
    alerts = []
    for tid,plate,ftype in fraud_txns:
        alerts.append((tid,plate,ftype or "Unknown",random.choice(["medium","high","critical"]),
                       f"Fraud detected: {ftype} for vehicle {plate}",random.randint(0,1),None,None))
    conn.executemany("""
        INSERT OR IGNORE INTO fraud_alerts
        (transaction_id,plate_number,alert_type,severity,description,is_resolved,resolved_by,resolved_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, alerts)

def init_db():
    conn = get_connection()
    # Create tables first
    conn.executescript(SCHEMA)
    conn.executescript(INDEXES)
    
    # Migrations for existing DBs
    cols = {r[1] for r in conn.execute("PRAGMA table_info(vehicles)").fetchall()}
    if 'fuel_type' not in cols:
        conn.execute("ALTER TABLE vehicles ADD COLUMN fuel_type TEXT"); conn.commit()
    cols2 = {r[1] for r in conn.execute("PRAGMA table_info(admin_users)").fetchall()}
    for col, typ in [("email","TEXT"),("phone","TEXT"),("avatar_initials","TEXT"),("is_active","INTEGER DEFAULT 1"),("gate_id","INTEGER")]:
        if col not in cols2:
            conn.execute(f"ALTER TABLE admin_users ADD COLUMN {col} {typ}"); conn.commit()
    conn.executemany("INSERT OR IGNORE INTO vehicles (plate_number,owner_name,owner_phone,owner_email,vehicle_type,state_code,is_blacklisted,blacklist_reason) VALUES (?,?,?,?,?,?,?,?)", VEHICLES_SEED)
    conn.executemany("INSERT OR IGNORE INTO fastag (fastag_id,plate_number,bank_name,balance,is_active,vehicle_type,owner_name,owner_phone) VALUES (?,?,?,?,?,?,?,?)", FASTAG_SEED)
    conn.executemany("INSERT OR IGNORE INTO toll_gates (gate_name,location,highway,toll_car,toll_truck,toll_bus,toll_bike,toll_van) VALUES (?,?,?,?,?,?,?,?)", GATES_SEED)
    conn.executemany("INSERT OR IGNORE INTO admin_users (username,password_hash,full_name,email,phone,role,avatar_initials,is_active,gate_id) VALUES (?,?,?,?,?,?,?,?,?)", ADMIN_SEED)
    seed_transactions(conn)
    conn.commit(); conn.close()
    print("Database initialized.")
