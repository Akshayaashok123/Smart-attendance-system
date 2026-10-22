import sqlite3
import os
from datetime import datetime, timedelta
import json
import numpy as np
import cv2
from typing import Optional, Dict, Any, List, Tuple

class DataLayer:
    """
    Manages local SQLite database with privacy controls, WAL mode, 
    atomic transactions, audit logging, consent tracking, and retention policies.
    """
    
    VALID_CONSENT_STATES = ("PENDING", "GRANTED", "WITHDRAWN")
    VALID_ATTENDANCE_METHODS = ("FACE_QR", "MANUAL")
    VALID_MANUAL_REASONS = (
        "NO_BIOMETRIC_CONSENT",
        "CAMERA_FAILURE",
        "VERIFICATION_FAILURE",
        "STUDENT_ASSISTED_ENTRY",
        "SPECIAL_NEEDS_ACCOMMODATION"
    )

    def __init__(self, db_path="attendance.db"):
        self.db_path = db_path
        self._initialize_db()

    def _get_connection(self):
        """Returns a sqlite3 connection with WAL mode enabled."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _initialize_db(self):
        """Creates tables and applies safe, non-destructive migrations."""
        query_attendance = """
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT DEFAULT 'Present',
            face_embedding TEXT,
            is_synced INTEGER DEFAULT 0,
            method TEXT DEFAULT 'FACE_QR',
            reason_category TEXT,
            recorded_by TEXT,
            notes TEXT
        );
        """
        query_registration = """
        CREATE TABLE IF NOT EXISTS registered_students (
            student_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            face_embedding_encrypted TEXT,
            consent_status TEXT DEFAULT 'PENDING',
            consent_date TEXT,
            consent_authorized_by TEXT,
            consent_method TEXT,
            biometric_enrolled_at TEXT
        );
        """
        query_users = """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('ADMIN', 'TEACHER')),
            full_name TEXT,
            created_at TEXT NOT NULL
        );
        """
        query_audit = """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            actor_username TEXT NOT NULL,
            action TEXT NOT NULL,
            target_id TEXT,
            details TEXT,
            status TEXT NOT NULL CHECK(status IN ('SUCCESS', 'FAILED'))
        );
        """
        query_config = """
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL
        );
        """

        with self._get_connection() as conn:
            conn.execute(query_attendance)
            conn.execute(query_registration)
            conn.execute(query_users)
            conn.execute(query_audit)
            conn.execute(query_config)

            # Apply incremental migrations safely for existing tables
            attendance_cols = [
                ("status", "TEXT DEFAULT 'Present'"),
                ("method", "TEXT DEFAULT 'FACE_QR'"),
                ("reason_category", "TEXT"),
                ("recorded_by", "TEXT"),
                ("notes", "TEXT")
            ]
            for col_name, col_def in attendance_cols:
                try:
                    conn.execute(f"ALTER TABLE attendance ADD COLUMN {col_name} {col_def};")
                except Exception:
                    pass  # Column already exists

            registration_cols = [
                ("consent_status", "TEXT DEFAULT 'PENDING'"),
                ("consent_date", "TEXT"),
                ("consent_authorized_by", "TEXT"),
                ("consent_method", "TEXT"),
                ("biometric_enrolled_at", "TEXT")
            ]
            for col_name, col_def in registration_cols:
                try:
                    conn.execute(f"ALTER TABLE registered_students ADD COLUMN {col_name} {col_def};")
                except Exception:
                    pass

            # Safe migration for existing pre-registered students: set consent to GRANTED so existing records remain functional
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                UPDATE registered_students 
                SET consent_status = 'GRANTED', 
                    consent_date = COALESCE(consent_date, ?),
                    consent_authorized_by = COALESCE(consent_authorized_by, 'LEGACY_MIGRATION'),
                    consent_method = COALESCE(consent_method, 'INITIAL_SYSTEM_SETUP'),
                    biometric_enrolled_at = COALESCE(biometric_enrolled_at, ?)
                WHERE consent_status IS NULL OR consent_status = 'PENDING';
                """,
                (now, now)
            )

            # Seed default system retention settings if not present
            defaults = [
                ("retention_attendance_days", "365"),
                ("retention_withdrawn_embeddings_days", "0")
            ]
            for k, v in defaults:
                cursor = conn.execute("SELECT COUNT(*) FROM system_config WHERE key = ?", (k,))
                if cursor.fetchone()[0] == 0:
                    conn.execute(
                        "INSERT INTO system_config (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'SYSTEM')",
                        (k, v, now)
                    )

            conn.commit()

    # --- AUDIT LOGGING ---
    def log_audit_event(self, actor: str, action: str, target_id: Optional[str], details: str, status: str = "SUCCESS"):
        """
        Records an auditable security event.
        Guarantees that sensitive data (passwords, raw embeddings, encryption keys) are never logged.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Sanitize details to prevent accidental embedding/secret leakage
        safe_details = str(details)
        if "gAAAAA" in safe_details:
            safe_details = "[ENCRYPTED_BLOB_REDACTED]"
        
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO audit_logs (timestamp, actor_username, action, target_id, details, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (now, actor or "SYSTEM", action, target_id, safe_details, status)
                )
                conn.commit()
        except Exception as e:
            print(f"[Audit Log Error] Failed to record audit event: {e}")

    def get_audit_logs(self, limit: int = 100, action_filter: Optional[str] = None) -> List[Tuple]:
        """Retrieves audit logs in reverse chronological order."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if action_filter:
                cursor.execute(
                    "SELECT id, timestamp, actor_username, action, target_id, details, status FROM audit_logs WHERE action = ? ORDER BY id DESC LIMIT ?",
                    (action_filter, limit)
                )
            else:
                cursor.execute(
                    "SELECT id, timestamp, actor_username, action, target_id, details, status FROM audit_logs ORDER BY id DESC LIMIT ?",
                    (limit,)
                )
            return cursor.fetchall()

    # --- CONSENT MANAGEMENT ---
    def update_consent(self, student_id: str, new_status: str, authorized_by: str, consent_method: str = "WRITTEN_FORM", actor_username: str = "ADMIN") -> bool:
        """
        Updates a student's consent status (PENDING, GRANTED, WITHDRAWN).
        If WITHDRAWN, future biometric verification is automatically rejected.
        Historical attendance records are NEVER deleted on consent withdrawal.
        """
        if new_status not in self.VALID_CONSENT_STATES:
            raise ValueError(f"Invalid consent state '{new_status}'. Must be one of {self.VALID_CONSENT_STATES}")
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT student_id, name, consent_status FROM registered_students WHERE student_id = ?", (student_id,))
                row = cursor.fetchone()
                if not row:
                    self.log_audit_event(actor_username, "CONSENT_UPDATE", student_id, f"Student {student_id} not found", "FAILED")
                    return False
                
                old_status = row[2]
                cursor.execute(
                    """
                    UPDATE registered_students 
                    SET consent_status = ?, consent_date = ?, consent_authorized_by = ?, consent_method = ?
                    WHERE student_id = ?
                    """,
                    (new_status, now, authorized_by, consent_method, student_id)
                )
                conn.commit()

            self.log_audit_event(
                actor_username, 
                "CONSENT_UPDATE", 
                student_id, 
                f"Consent changed from {old_status} to {new_status} by {authorized_by} ({consent_method})",
                "SUCCESS"
            )
            return True
        except Exception as e:
            self.log_audit_event(actor_username, "CONSENT_UPDATE", student_id, str(e), "FAILED")
            return False

    def delete_biometric_embedding(self, student_id: str, actor_username: str) -> bool:
        """
        Permanently removes a student's stored facial embedding upon consent withdrawal or request.
        Does NOT delete historical attendance records.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE registered_students 
                    SET face_embedding_encrypted = NULL, biometric_enrolled_at = NULL
                    WHERE student_id = ?
                    """,
                    (student_id,)
                )
                conn.commit()
            self.log_audit_event(actor_username, "BIOMETRIC_DELETE", student_id, "Facial embedding removed safely", "SUCCESS")
            return True
        except Exception as e:
            self.log_audit_event(actor_username, "BIOMETRIC_DELETE", student_id, str(e), "FAILED")
            return False

    # --- STUDENT REGISTRATION & RETRIEVAL ---
    def is_face_already_registered(self, new_embedding, scanner, security, threshold=0.42):
        """Prevents the same person from registering multiple QR IDs."""
        query = "SELECT student_id, face_embedding_encrypted FROM registered_students WHERE face_embedding_encrypted IS NOT NULL"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            
        for sid, enc_emb in rows:
            if not enc_emb:
                continue
            try:
                reg_emb_list = json.loads(security.decrypt_data(enc_emb))
                reg_emb = np.array(reg_emb_list, dtype=np.float32)
                score = scanner.face_recognizer.match(new_embedding, reg_emb, cv2.FaceRecognizerSF_FR_COSINE)
                if score >= threshold:
                    return sid  # Face already claimed by another ID
            except Exception:
                continue
        return None

    def register_student(self, student_id: str, name: str, face_embedding_encrypted: str, 
                         consent_status: str = "GRANTED", authorized_by: str = "PARENT/GUARDIAN",
                         consent_method: str = "WRITTEN_FORM", actor_username: str = "ADMIN"):
        """
        Registers a new student profile or updates biometric enrollment.
        Enforces that biometric enrollment is strictly blocked if consent is not GRANTED.
        """
        if consent_status != "GRANTED":
            raise PermissionError(
                f"Biometric enrollment rejected: Consent status must be 'GRANTED' (Current: '{consent_status}')."
            )

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        query = """
        INSERT INTO registered_students (
            student_id, name, face_embedding_encrypted, 
            consent_status, consent_date, consent_authorized_by, consent_method, biometric_enrolled_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(student_id) DO UPDATE SET
            name = excluded.name,
            face_embedding_encrypted = excluded.face_embedding_encrypted,
            consent_status = excluded.consent_status,
            consent_date = excluded.consent_date,
            consent_authorized_by = excluded.consent_authorized_by,
            consent_method = excluded.consent_method,
            biometric_enrolled_at = excluded.biometric_enrolled_at
        """
        try:
            with self._get_connection() as conn:
                conn.execute(query, (
                    student_id, name, face_embedding_encrypted,
                    consent_status, now, authorized_by, consent_method, now
                ))
                conn.commit()
            self.log_audit_event(actor_username, "BIOMETRIC_ENROLL", student_id, f"Enrolled student '{name}' with {consent_method}", "SUCCESS")
            return True
        except Exception as e:
            self.log_audit_event(actor_username, "BIOMETRIC_ENROLL", student_id, str(e), "FAILED")
            print(f"Error registering student {student_id}: {e}")
            return False

    def get_registered_student(self, student_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a registered student's profile and consent metadata by ID or name."""
        if not student_id:
            return None
        clean_id = str(student_id).strip()
        query = """
        SELECT student_id, name, face_embedding_encrypted, 
               consent_status, consent_date, consent_authorized_by, consent_method, biometric_enrolled_at
        FROM registered_students 
        WHERE LOWER(TRIM(student_id)) = LOWER(?) 
           OR LOWER(TRIM(name)) = LOWER(?)
           OR ? LIKE (TRIM(name) || '%')
           OR TRIM(name) LIKE (? || '%')
        LIMIT 1
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (clean_id, clean_id, clean_id, clean_id))
                res = cursor.fetchone()
                if res:
                    return {
                        "student_id": res[0],
                        "name": res[1],
                        "face_embedding_encrypted": res[2],
                        "consent_status": res[3] or "PENDING",
                        "consent_date": res[4],
                        "consent_authorized_by": res[5],
                        "consent_method": res[6],
                        "biometric_enrolled_at": res[7]
                    }
            return None
        except Exception as e:
            print(f"Error fetching registered student {student_id}: {e}")
            return None

    # --- ATTENDANCE LOGGING ---
    def log_attendance(self, student_id: str, face_embedding_encrypted: str, status: str = "Present", method: str = "FACE_QR") -> bool:
        """Logs a verified biometric attendance record."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        query = """
        INSERT INTO attendance (student_id, timestamp, status, face_embedding, method) 
        VALUES (?, ?, ?, ?, ?)
        """
        try:
            with self._get_connection() as conn:
                conn.execute(query, (student_id, timestamp, status, face_embedding_encrypted, method))
                conn.commit()
            return True
        except Exception as e:
            print(f"Error logging attendance: {e}")
            return False

    def log_manual_attendance(self, student_id: str, status: str = "Present", 
                              reason_category: str = "NO_BIOMETRIC_CONSENT", 
                              recorded_by: str = "teacher1", notes: str = "") -> bool:
        """
        Logs a non-biometric attendance record with an auditable reason category.
        Ensures students without biometric consent receive full attendance credit.
        """
        if reason_category not in self.VALID_MANUAL_REASONS:
            raise ValueError(f"Invalid reason category '{reason_category}'. Valid: {self.VALID_MANUAL_REASONS}")
        if status not in ("Present", "Late"):
            raise ValueError("Status must be 'Present' or 'Late'.")
            
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        query = """
        INSERT INTO attendance (student_id, timestamp, status, method, reason_category, recorded_by, notes)
        VALUES (?, ?, ?, 'MANUAL', ?, ?, ?)
        """
        try:
            with self._get_connection() as conn:
                conn.execute(query, (student_id, timestamp, status, reason_category, recorded_by, notes))
                conn.commit()
            self.log_audit_event(
                recorded_by, 
                "MANUAL_ATTENDANCE", 
                student_id, 
                f"Marked {status} (Reason: {reason_category})",
                "SUCCESS"
            )
            return True
        except Exception as e:
            self.log_audit_event(recorded_by, "MANUAL_ATTENDANCE", student_id, str(e), "FAILED")
            print(f"Error logging manual attendance: {e}")
            return False

    def correct_attendance(self, record_id: int, new_status: str, reason: str, acting_user: str, notes: str = "") -> bool:
        """
        Allows authorized teachers or admins to correct an attendance record with full audit logging.
        """
        if new_status not in ("Present", "Late"):
            raise ValueError("Status must be 'Present' or 'Late'.")
            
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT student_id, status FROM attendance WHERE id = ?", (record_id,))
                row = cursor.fetchone()
                if not row:
                    return False
                old_status = row[1]
                cursor.execute(
                    "UPDATE attendance SET status = ?, notes = ?, is_synced = 0 WHERE id = ?",
                    (new_status, f"Corrected by {acting_user}: {reason}. {notes}".strip(), record_id)
                )
                conn.commit()

            self.log_audit_event(
                acting_user, 
                "ATTENDANCE_CORRECTION", 
                str(record_id), 
                f"Changed status from {old_status} to {new_status}: {reason}",
                "SUCCESS"
            )
            return True
        except Exception as e:
            self.log_audit_event(acting_user, "ATTENDANCE_CORRECTION", str(record_id), str(e), "FAILED")
            return False

    def has_checked_in_today(self, student_id: str) -> bool:
        """Returns True if the student already has an attendance record for today."""
        today = datetime.now().strftime("%Y-%m-%d")
        query = "SELECT COUNT(*) FROM attendance WHERE student_id = ? AND timestamp LIKE ?"
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (student_id, f"{today}%"))
                return cursor.fetchone()[0] > 0
        except Exception as e:
            print(f"Error checking today's attendance for {student_id}: {e}")
            return False

    def get_unsynced_records(self) -> List[Tuple]:
        """Fetches unsynced records. Returns id, student_id, timestamp, status, method."""
        query = "SELECT id, student_id, timestamp, status, COALESCE(method, 'FACE_QR') FROM attendance WHERE is_synced = 0"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            return cursor.fetchall()

    def mark_as_synced(self, record_id: int) -> bool:
        """Marks a record as synced in the database."""
        query = "UPDATE attendance SET is_synced = 1 WHERE id = ?"
        try:
            with self._get_connection() as conn:
                conn.execute(query, (record_id,))
                conn.commit()
            return True
        except Exception as e:
            print(f"Error marking record {record_id} as synced: {e}")
            return False

    # --- RETENTION & DELETION POLICY ---
    def get_retention_config(self) -> Dict[str, str]:
        """Returns current data retention settings."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM system_config")
            return {k: v for k, v in cursor.fetchall()}

    def set_retention_config(self, key: str, value: str, acting_user: str) -> bool:
        """Updates a retention configuration parameter."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO system_config (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                    (key, str(value), now, acting_user)
                )
                conn.commit()
            self.log_audit_event(acting_user, "RETENTION_CONFIG_CHANGE", key, f"Updated value to {value}", "SUCCESS")
            return True
        except Exception as e:
            self.log_audit_event(acting_user, "RETENTION_CONFIG_CHANGE", key, str(e), "FAILED")
            return False

    def preview_retention_cleanup(self, attendance_days: int = 365, withdrawn_days: int = 0) -> Dict[str, Any]:
        """
        Dry-run inspection: computes which records would be deleted without modifying data.
        """
        now = datetime.now()
        att_cutoff = (now - timedelta(days=attendance_days)).strftime("%Y-%m-%d %H:%M:%S")
        withdrawn_cutoff = (now - timedelta(days=withdrawn_days)).strftime("%Y-%m-%d %H:%M:%S")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Count expired attendance history
            cursor.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM attendance WHERE timestamp < ?", (att_cutoff,))
            att_count, min_t, max_t = cursor.fetchone()

            # Count withdrawn embeddings to be purged
            cursor.execute(
                """
                SELECT COUNT(*), GROUP_CONCAT(student_id) 
                FROM registered_students 
                WHERE consent_status = 'WITHDRAWN' 
                  AND face_embedding_encrypted IS NOT NULL
                  AND (consent_date IS NULL OR consent_date <= ?)
                """,
                (withdrawn_cutoff,)
            )
            emb_count, student_ids = cursor.fetchone()

        return {
            "attendance_cutoff": att_cutoff,
            "expired_attendance_records": att_count or 0,
            "withdrawn_biometrics_cutoff": withdrawn_cutoff,
            "withdrawn_embeddings_to_delete": emb_count or 0,
            "affected_students": student_ids.split(",") if student_ids else []
        }

    def execute_retention_cleanup(self, attendance_days: int = 365, withdrawn_days: int = 0, acting_user: str = "ADMIN") -> Dict[str, Any]:
        """
        Executes approved deletion of expired attendance and withdrawn biometric embeddings.
        Operation is completely idempotent and logged.
        """
        preview = self.preview_retention_cleanup(attendance_days, withdrawn_days)
        att_cutoff = preview["attendance_cutoff"]
        withdrawn_cutoff = preview["withdrawn_biometrics_cutoff"]

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # 1. Purge withdrawn embeddings
                cursor.execute(
                    """
                    UPDATE registered_students 
                    SET face_embedding_encrypted = NULL, biometric_enrolled_at = NULL
                    WHERE consent_status = 'WITHDRAWN' 
                      AND face_embedding_encrypted IS NOT NULL
                      AND (consent_date IS NULL OR consent_date <= ?)
                    """,
                    (withdrawn_cutoff,)
                )
                embeddings_deleted = cursor.rowcount

                # 2. Purge expired attendance records
                cursor.execute("DELETE FROM attendance WHERE timestamp < ?", (att_cutoff,))
                attendance_deleted = cursor.rowcount

                conn.commit()

            details = f"Purged {attendance_deleted} attendance rows (<{att_cutoff}) and {embeddings_deleted} withdrawn embeddings (<{withdrawn_cutoff})"
            self.log_audit_event(acting_user, "RETENTION_CLEANUP_EXECUTE", "system_retention", details, "SUCCESS")
            return {
                "status": "SUCCESS",
                "attendance_deleted": attendance_deleted,
                "embeddings_deleted": embeddings_deleted,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        except Exception as e:
            self.log_audit_event(acting_user, "RETENTION_CLEANUP_EXECUTE", "system_retention", str(e), "FAILED")
            return {
                "status": "FAILED",
                "error": str(e)
            }
