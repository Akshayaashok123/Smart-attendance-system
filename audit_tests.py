"""
Audit test script - Steps 3a-3f (ASCII-only output, no Unicode arrows).
Uses audit_test.db -- never touches attendance.db.
"""
import os, json, sqlite3, time
import numpy as np
from unittest.mock import patch, MagicMock
import sys

# Force UTF-8 stdout
sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = "audit_test.db"
for f in [DB_PATH, DB_PATH+"-wal", DB_PATH+"-shm"]:
    if os.path.exists(f):
        try: os.remove(f)
        except: pass

from data_layer import DataLayer
from security import SecurityManager
from cloud_sync import CloudSyncManager

db = DataLayer(db_path=DB_PATH)
sec = SecurityManager()

STUDENT_ID = "STU_AUDIT_01"
EMBEDDING = np.random.rand(1, 128).astype(np.float32)
EMBEDDING /= np.linalg.norm(EMBEDDING)
emb_json = json.dumps(EMBEDDING.tolist())
enc_emb = sec.encrypt_data(emb_json)
db.register_student(STUDENT_ID, "AuditStudent", enc_emb)

def check(label, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {label}  {detail}")

print("=" * 60)
print("AUDIT TESTS (scratch DB: audit_test.db)")
print("=" * 60)

# --- (a) ---
print("\n[a] log_attendance writes row, is_synced=0, status='Late'")
ok = db.log_attendance(STUDENT_ID, enc_emb, status="Late")
conn = sqlite3.connect(DB_PATH)
row = conn.execute("SELECT student_id, status, is_synced FROM attendance WHERE student_id=?", (STUDENT_ID,)).fetchone()
conn.close()
check("Row written", ok and row is not None, str(row))
check("student_id correct", row and row[0]==STUDENT_ID)
check("status='Late'", row and row[1]=="Late")
check("is_synced=0", row and row[2]==0)

# --- (b) ---
print("\n[b] Different face embedding -> score < 0.42 -> rejected")
import cv2
DIFF = np.random.rand(1, 128).astype(np.float32)
DIFF /= np.linalg.norm(DIFF)
reg_emb = np.array(json.loads(sec.decrypt_data(enc_emb)), dtype=np.float32)
try:
    from hardware_ui import AttendanceScanner
    scanner = AttendanceScanner()
    score = scanner.face_recognizer.match(DIFF, reg_emb, cv2.FaceRecognizerSF_FR_COSINE)
    method = "SFace cosine"
except Exception as e:
    score = float(np.dot(DIFF.flatten(), reg_emb.flatten()))
    method = "numpy cosine fallback"
check(f"score<0.42 so rejected ({method})", score < 0.42, f"score={score:.4f}")

# --- (c) ---
print("\n[c] Network disabled -> local still saved, sync returns quickly")
sync = CloudSyncManager(db, "http://fake-url", sync_interval=9999)
t0 = time.time()
with patch.object(sync, 'is_connected', return_value=False):
    sync.sync_records()
elapsed = time.time() - t0
row_c = sqlite3.connect(DB_PATH).execute("SELECT is_synced FROM attendance WHERE student_id=?", (STUDENT_ID,)).fetchone()
check("Returned in <5s (no freeze)", elapsed < 5, f"{elapsed:.3f}s")
check("is_synced still 0 (record preserved)", row_c and row_c[0]==0)

# --- (d) ---
print("\n[d] Network re-enabled -> batch POST, is_synced=1, no embedding in payload")
mock_resp = MagicMock()
mock_resp.status_code = 200
mock_resp.text = "Success"
with patch('requests.post', return_value=mock_resp) as mp:
    with patch.object(sync, 'is_connected', return_value=True):
        sync.sync_records()
row_d = sqlite3.connect(DB_PATH).execute("SELECT is_synced FROM attendance WHERE student_id=?", (STUDENT_ID,)).fetchone()
payload = mp.call_args[1].get('json', []) if mp.called else []
keys = set(payload[0].keys()) if payload else set()
check("POST was made", mp.called)
check("is_synced=1 after sync", row_d and row_d[0]==1)
check("Payload has student_id", 'student_id' in keys, f"keys={keys}")
check("Payload has timestamp", 'timestamp' in keys)
check("Payload has status", 'status' in keys)
check("No embedding in payload (privacy)", 'face_embedding' not in keys and 'embedding' not in keys)

# --- (e) ---
print("\n[e] Run sync AGAIN -> no duplicate POST (all rows already synced)")
with patch('requests.post', return_value=mock_resp) as mp2:
    with patch.object(sync, 'is_connected', return_value=True):
        sync.sync_records()
unsynced = db.get_unsynced_records()
check("No POST on second sync", not mp2.called)
check("Zero unsynced records", len(unsynced)==0, f"unsynced={len(unsynced)}")

# --- (f) ---
print("\n[f] WAL resilience: write record, reopen DB fresh, all data intact")
db.log_attendance("STU_AUDIT_02", enc_emb, status="Present")
conn2 = sqlite3.connect(DB_PATH)
conn2.execute("PRAGMA journal_mode=WAL;")
rows_f = conn2.execute("SELECT student_id, status, is_synced FROM attendance ORDER BY id").fetchall()
conn2.close()
check("STU_AUDIT_01 intact", any(r[0]==STUDENT_ID for r in rows_f))
check("STU_AUDIT_02 intact", any(r[0]=="STU_AUDIT_02" for r in rows_f))
print(f"     All rows: {rows_f}")

# --- Bonus: duplicate check ---
print("\n[bonus] Duplicate same-day check-in blocked by has_checked_in_today()")
already = db.has_checked_in_today(STUDENT_ID)
check("has_checked_in_today returns True -> blocks repeat scan", already)

# Cleanup
print("\nCleaning up scratch DB...")
import time as _t
_t.sleep(0.5)
for f in [DB_PATH, DB_PATH+"-wal", DB_PATH+"-shm"]:
    try:
        if os.path.exists(f): os.remove(f)
    except Exception as ex:
        print(f"  (Could not remove {f}: {ex})")

print("\n" + "=" * 60)
print("All audit tests complete.")
print("=" * 60)
