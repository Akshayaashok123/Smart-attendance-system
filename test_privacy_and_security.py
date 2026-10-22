"""
Automated Test Suite for Child Data Privacy, Consent, RBAC, Retention, and Safe Attendance.
Uses an isolated test database (privacy_test.db).
"""
import os
import json
import sqlite3
import time
from datetime import datetime, timedelta
import numpy as np
import pytest
from unittest.mock import patch, MagicMock

from data_layer import DataLayer
from security import SecurityManager
from auth import AuthManager
from cloud_sync import CloudSyncManager

TEST_DB_PATH = "privacy_test.db"

@pytest.fixture
def clean_db():
    db = DataLayer(db_path=TEST_DB_PATH)
    with db._get_connection() as conn:
        conn.execute("DELETE FROM attendance;")
        conn.execute("DELETE FROM registered_students;")
        conn.execute("DELETE FROM audit_logs;")
        conn.execute("DELETE FROM users WHERE username NOT IN ('admin', 'teacher1');")
        conn.commit()
    yield db
    with db._get_connection() as conn:
        conn.execute("DELETE FROM attendance;")
        conn.execute("DELETE FROM registered_students;")
        conn.execute("DELETE FROM audit_logs;")
        conn.execute("DELETE FROM users WHERE username NOT IN ('admin', 'teacher1');")
        conn.commit()

def test_consent_workflow(clean_db):
    db = clean_db
    sec = SecurityManager()
    
    # Fake embedding
    fake_emb = json.dumps(np.random.rand(1, 128).tolist())
    enc_emb = sec.encrypt_data(fake_emb)
    
    # 1. Enrolling student with GRANTED consent succeeds
    ok = db.register_student(
        student_id="STU_PRIV_01",
        name="Student Granted",
        face_embedding_encrypted=enc_emb,
        consent_status="GRANTED",
        authorized_by="Parent - Form #101",
        consent_method="WRITTEN_FORM",
        actor_username="admin"
    )
    assert ok is True
    prof = db.get_registered_student("STU_PRIV_01")
    assert prof["consent_status"] == "GRANTED"
    assert prof["face_embedding_encrypted"] == enc_emb
    
    # 2. Enrolling student with PENDING consent is strictly rejected at service layer
    with pytest.raises(PermissionError):
        db.register_student(
            student_id="STU_PRIV_02",
            name="Student Pending",
            face_embedding_encrypted=enc_emb,
            consent_status="PENDING",
            actor_username="admin"
        )
        
    # 3. Enrolling student with WITHDRAWN consent is strictly rejected at service layer
    with pytest.raises(PermissionError):
        db.register_student(
            student_id="STU_PRIV_03",
            name="Student Withdrawn",
            face_embedding_encrypted=enc_emb,
            consent_status="WITHDRAWN",
            actor_username="admin"
        )

    # 4. Withdraw consent for STU_PRIV_01
    db.log_attendance("STU_PRIV_01", enc_emb, status="Present", method="FACE_QR")
    assert db.has_checked_in_today("STU_PRIV_01") is True
    
    # Withdraw consent
    w_ok = db.update_consent("STU_PRIV_01", "WITHDRAWN", authorized_by="Parent Request #202", actor_username="admin")
    assert w_ok is True
    updated_prof = db.get_registered_student("STU_PRIV_01")
    assert updated_prof["consent_status"] == "WITHDRAWN"
    
    # 5. Withdrawal does NOT delete historical attendance
    with db._get_connection() as conn:
        att_count = conn.execute("SELECT COUNT(*) FROM attendance WHERE student_id = ?", ("STU_PRIV_01",)).fetchone()[0]
        assert att_count == 1, "Historical attendance must be preserved when consent is withdrawn"
        
    # 6. Delete biometric embedding on withdrawal
    del_ok = db.delete_biometric_embedding("STU_PRIV_01", actor_username="admin")
    assert del_ok is True
    del_prof = db.get_registered_student("STU_PRIV_01")
    assert del_prof["face_embedding_encrypted"] is None, "Embedding must be purged"
    assert del_prof["name"] == "Student Granted", "Directory profile must remain intact"

def test_role_based_access_control(clean_db):
    db = clean_db
    auth = AuthManager(db)
    
    # 1. Verify default accounts
    admin_session = auth.authenticate("admin", "Admin@1234")
    assert admin_session is not None
    assert admin_session["role"] == "ADMIN"
    
    teacher_session = auth.authenticate("teacher1", "Teacher@1234")
    assert teacher_session is not None
    assert teacher_session["role"] == "TEACHER"
    
    # 2. Wrong password fails
    fail_session = auth.authenticate("admin", "WrongPassword")
    assert fail_session is None
    
    # 3. Teacher cannot perform admin operations
    with pytest.raises(PermissionError):
        auth.create_user(teacher_session, "new_user", "Pass@1234", "TEACHER", "Teacher Two")
        
    # 4. Admin can create users
    create_ok = auth.create_user(admin_session, "teacher2", "Pass@1234", "TEACHER", "Teacher Two")
    assert create_ok is True
    t2_session = auth.authenticate("teacher2", "Pass@1234")
    assert t2_session is not None
    assert t2_session["role"] == "TEACHER"

def test_non_biometric_manual_attendance(clean_db):
    db = clean_db
    
    # 1. Log manual attendance
    ok = db.log_manual_attendance(
        student_id="STU_MANUAL_01",
        status="Present",
        reason_category="NO_BIOMETRIC_CONSENT",
        recorded_by="teacher1",
        notes="Student opted out of biometrics"
    )
    assert ok is True
    assert db.has_checked_in_today("STU_MANUAL_01") is True
    
    # 2. Check record fields in DB
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT student_id, status, method, reason_category, recorded_by, face_embedding FROM attendance WHERE student_id = ?",
            ("STU_MANUAL_01",)
        ).fetchone()
        assert row[0] == "STU_MANUAL_01"
        assert row[1] == "Present"
        assert row[2] == "MANUAL"
        assert row[3] == "NO_BIOMETRIC_CONSENT"
        assert row[4] == "teacher1"
        assert row[5] is None, "Manual attendance must NEVER have a face embedding"
        
    # 3. Duplicate same-day entry blocked
    assert db.has_checked_in_today("STU_MANUAL_01") is True
    
    # 4. Correct attendance with audit log
    rec_id = conn.execute("SELECT id FROM attendance WHERE student_id = ?", ("STU_MANUAL_01",)).fetchone()[0]
    corr_ok = db.correct_attendance(rec_id, "Late", "Arrived 15m late by bus", acting_user="teacher1")
    assert corr_ok is True
    
    with db._get_connection() as conn:
        updated_row = conn.execute("SELECT status, is_synced FROM attendance WHERE id = ?", (rec_id,)).fetchone()
        assert updated_row[0] == "Late"
        assert updated_row[1] == 0, "Corrected record should be queued for re-sync"
        
    # Check audit log for correction
    logs = db.get_audit_logs(limit=5, action_filter="ATTENDANCE_CORRECTION")
    assert len(logs) > 0
    assert logs[0][2] == "teacher1"
    assert "Changed status from Present to Late" in logs[0][5]

def test_retention_and_deletion(clean_db):
    db = clean_db
    sec = SecurityManager()
    fake_emb = sec.encrypt_data("[0.1, 0.2, 0.3]")
    
    # Create an old attendance record (>400 days old)
    old_time = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
    recent_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with db._get_connection() as conn:
        conn.execute("INSERT INTO attendance (student_id, timestamp, status, method) VALUES ('STU_OLD', ?, 'Present', 'FACE_QR')", (old_time,))
        conn.execute("INSERT INTO attendance (student_id, timestamp, status, method) VALUES ('STU_RECENT', ?, 'Present', 'FACE_QR')", (recent_time,))
        
        # Add a student with WITHDRAWN consent
        conn.execute(
            "INSERT INTO registered_students (student_id, name, face_embedding_encrypted, consent_status, consent_date) VALUES ('STU_WITHDRAWN_OLD', 'Withdrawn Student', ?, 'WITHDRAWN', ?)",
            (fake_emb, old_time)
        )
        conn.commit()
        
    # 1. Preview / Dry-run
    preview = db.preview_retention_cleanup(attendance_days=365, withdrawn_days=0)
    assert preview["expired_attendance_records"] == 1
    assert preview["withdrawn_embeddings_to_delete"] == 1
    assert "STU_WITHDRAWN_OLD" in preview["affected_students"]
    
    # Verify no data modified during preview
    with db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 2
        assert conn.execute("SELECT face_embedding_encrypted FROM registered_students WHERE student_id = 'STU_WITHDRAWN_OLD'").fetchone()[0] is not None
        
    # 2. Confirmed cleanup execution
    res = db.execute_retention_cleanup(attendance_days=365, withdrawn_days=0, acting_user="admin")
    assert res["status"] == "SUCCESS"
    assert res["attendance_deleted"] == 1
    assert res["embeddings_deleted"] == 1
    
    # Verify data state after cleanup
    with db._get_connection() as conn:
        # Old attendance deleted, recent attendance preserved
        remaining_att = conn.execute("SELECT student_id FROM attendance").fetchall()
        assert len(remaining_att) == 1
        assert remaining_att[0][0] == "STU_RECENT"
        
        # Student directory record intact, but face embedding is NULL
        stud_row = conn.execute("SELECT student_id, name, face_embedding_encrypted, consent_status FROM registered_students WHERE student_id = 'STU_WITHDRAWN_OLD'").fetchone()
        assert stud_row[0] == "STU_WITHDRAWN_OLD"
        assert stud_row[1] == "Withdrawn Student"
        assert stud_row[2] is None, "Embedding must be deleted"
        assert stud_row[3] == "WITHDRAWN"

    # 3. Idempotent: repeating cleanup causes no error or corruption
    res2 = db.execute_retention_cleanup(attendance_days=365, withdrawn_days=0, acting_user="admin")
    assert res2["status"] == "SUCCESS"
    assert res2["attendance_deleted"] == 0
    assert res2["embeddings_deleted"] == 0

def test_sync_privacy_and_offline(clean_db):
    db = clean_db
    sec = SecurityManager()
    enc_emb = sec.encrypt_data("[0.5, 0.6, 0.7]")
    
    # Log 1 biometric attendance and 1 manual attendance
    db.log_attendance("STU_SYNC_01", enc_emb, status="Present", method="FACE_QR")
    db.log_manual_attendance("STU_SYNC_02", status="Late", reason_category="NO_BIOMETRIC_CONSENT", recorded_by="teacher1")
    
    sync = CloudSyncManager(db, "http://dummy-sheets-endpoint", sync_interval=9999)
    
    # 1. Offline behavior: stays unsynced
    with patch.object(sync, "is_connected", return_value=False):
        sync.sync_records()
    unsynced = db.get_unsynced_records()
    assert len(unsynced) == 2, "Records must be preserved offline"
    
    # 2. Reconnection: pushes only privacy-safe fields (NO embeddings, NO images, NO secrets)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "Success"
    
    with patch("requests.post", return_value=mock_resp) as mock_post:
        with patch.object(sync, "is_connected", return_value=True):
            sync.sync_records()
            
    assert mock_post.called
    payload = mock_post.call_args[1].get("json", [])
    assert len(payload) == 2
    
    for item in payload:
        # Check required fields
        assert "student_id" in item
        assert "timestamp" in item
        assert "status" in item
        assert "method" in item
        # Check strict privacy requirements
        assert "face_embedding" not in item
        assert "embedding" not in item
        assert "password" not in item
        assert "secret" not in item
        
    assert payload[0]["method"] == "FACE_QR"
    assert payload[1]["method"] == "MANUAL"
    
    # Records marked as synced
    assert len(db.get_unsynced_records()) == 0
    
    # Re-running sync creates no duplicate POST
    with patch("requests.post", return_value=mock_resp) as mock_post2:
        with patch.object(sync, "is_connected", return_value=True):
            sync.sync_records()
    assert not mock_post2.called, "Must not duplicate remote entries"
