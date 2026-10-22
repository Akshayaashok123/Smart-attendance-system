import sqlite3
import os
from datetime import datetime
import json
import numpy as np
import cv2

class DataLayer:
    """Manages local SQLite database with reliability features (WAL, atomic transactions)."""
    
    def __init__(self, db_path="attendance.db"):
        self.db_path = db_path
        self._initialize_db()

    def _get_connection(self):
        """Returns a sqlite3 connection with WAL mode enabled."""
        conn = sqlite3.connect(self.db_path)
        # Enable WAL (Write-Ahead Logging) for concurrent access and crash resilience
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _initialize_db(self):
        """Creates the necessary tables if they don't exist."""
        query_attendance = """
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT DEFAULT 'Present',
            face_embedding TEXT,  -- Encrypted embedding
            is_synced INTEGER DEFAULT 0
        );
        """
        query_registration = """
        CREATE TABLE IF NOT EXISTS registered_students (
            student_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            face_embedding_encrypted TEXT NOT NULL
        );
        """
        with self._get_connection() as conn:
            conn.execute(query_attendance)
            # Automatic migration: Add 'status' column if existing db lacks it
            try:
                conn.execute("ALTER TABLE attendance ADD COLUMN status TEXT DEFAULT 'Present';")
            except Exception:
                pass  # Column already exists
            conn.execute(query_registration)
            conn.commit()

    def is_face_already_registered(self, new_embedding, scanner, security, threshold=0.42):
        """Prevents the same person from registering multiple QR IDs."""
        query = "SELECT student_id, face_embedding_encrypted FROM registered_students"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            
        for sid, enc_emb in rows:
            try:
                reg_emb_list = json.loads(security.decrypt_data(enc_emb))
                reg_emb = np.array(reg_emb_list, dtype=np.float32)
                score = scanner.face_recognizer.match(new_embedding, reg_emb, cv2.FaceRecognizerSF_FR_COSINE)
                if score >= threshold:
                    return sid  # Face already claimed by another ID
            except Exception:
                continue
        return None

    def register_student(self, student_id, name, face_embedding_encrypted):
        """Registers a new student profile or updates an existing one."""
        query = """
        INSERT OR REPLACE INTO registered_students (student_id, name, face_embedding_encrypted)
        VALUES (?, ?, ?)
        """
        try:
            with self._get_connection() as conn:
                conn.execute(query, (student_id, name, face_embedding_encrypted))
                conn.commit()
            return True
        except Exception as e:
            print(f"Error registering student {student_id}: {e}")
            return False

    def get_registered_student(self, student_id):
        """Retrieves a registered student's profile by ID."""
        query = "SELECT name, face_embedding_encrypted FROM registered_students WHERE student_id = ?"
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (student_id,))
                res = cursor.fetchone()
                if res:
                    return {"name": res[0], "face_embedding_encrypted": res[1]}
            return None
        except Exception as e:
            print(f"Error fetching registered student {student_id}: {e}")
            return None

    def log_attendance(self, student_id, face_embedding_encrypted, status="Present"):
        """Logs a new attendance record atomically with dynamic status."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        query = """
        INSERT INTO attendance (student_id, timestamp, status, face_embedding) 
        VALUES (?, ?, ?, ?)
        """
        try:
            with self._get_connection() as conn:
                conn.execute(query, (student_id, timestamp, status, face_embedding_encrypted))
                conn.commit()
            return True
        except Exception as e:
            print(f"Error logging attendance: {e}")
            return False

    def has_checked_in_today(self, student_id):
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

    def get_unsynced_records(self):
        """Fetches unsynced records with explicit column ordering."""
        query = "SELECT id, student_id, timestamp, status FROM attendance WHERE is_synced = 0"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            return cursor.fetchall()

    def mark_as_synced(self, record_id):
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

if __name__ == "__main__":
    # Quick test
    db = DataLayer("test_attendance.db")
    db.log_attendance("STUDENT_101", "encrypted_blob_xyz")
    unsynced = db.get_unsynced_records()
    print(f"Unsynced records: {unsynced}")
    if unsynced:
        db.mark_as_synced(unsynced[0][0])
        print("Marked as synced.")
    
    # Cleanup test file
    # os.remove("test_attendance.db")
