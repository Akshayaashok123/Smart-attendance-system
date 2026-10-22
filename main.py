import os
# --- Global Silence for OpenCV Warnings ---
os.environ['OPENCV_LOG_LEVEL'] = 'ERROR'

import time
import json
import numpy as np
import cv2
import re
from datetime import datetime, time as dt_time
from dotenv import load_dotenv

# Set your institutional cutoff time (e.g., 9:00 AM)
# Anyone checking in after this time is automatically flagged as "Late"
SCHOOL_START_CUTOFF = dt_time(9, 0, 0)

def calculate_attendance_status():
    """Compares the current local clock against the cutoff threshold."""
    current_time = datetime.now().time()
    return "Late" if current_time > SCHOOL_START_CUTOFF else "Present"

# Define your institution's expected ID pattern (supports alphanumeric, dashes, underscores, and spaces)
VALID_ID_PATTERN = re.compile(r'^[A-Za-z0-9_ -]{3,30}$')

# Load variables from local .env
load_dotenv()

from hardware_ui import AttendanceScanner
from data_layer import DataLayer
from security import SecurityManager
from notifications import EmailNotifier
from cloud_sync import CloudSyncManager

# --- CONFIGURATION VIA ENVIRONMENT ---
G_APPS_SCRIPT_URL = os.getenv("G_APPS_SCRIPT_URL")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
RECEIVER_EMAIL = os.getenv("RECEIVER_EMAIL")
SYNC_INTERVAL_SEC = int(os.getenv("SYNC_INTERVAL_SEC", 1800))
# -------------------------------------

def main():
    print("--- [Smart Attendance System Initializing] ---")
    
    # 1. Initialize Layers
    db = DataLayer()
    security = SecurityManager()
    notifier = EmailNotifier(sender_email=SENDER_EMAIL, sender_password=SENDER_PASSWORD)
    
    # Start Cloud Sync in the background
    sync = CloudSyncManager(db, G_APPS_SCRIPT_URL, sync_interval=SYNC_INTERVAL_SEC)
    sync.start_background_sync()
    
    # 2. Scanner Callback
    def on_success(student_id, embedding):
        """Action to take when QR and Face are verified simultaneously."""
        student_id = str(student_id).strip()
        # Reject web URLs or noisy barcodes immediately
        if not VALID_ID_PATTERN.match(student_id) or student_id.startswith(("http://", "https://")):
            print(f"[Rejected] Invalid QR payload format: {student_id}")
            return False, "Invalid QR Format", 0.0

        # 1. Check if student is registered
        profile = db.get_registered_student(student_id)
        if not profile:
            print(f"[Verification Failed] Student ID/Name '{student_id}' is not registered in the system.")
            return False, f"ID '{student_id}' Not Registered", 0.0

        canonical_id = profile.get("student_id", student_id)

        # Check consent status before biometric verification
        consent_status = profile.get("consent_status", "PENDING")
        if consent_status != "GRANTED":
            print(f"[Verification Failed] Biometric verification disabled: Student '{canonical_id}' consent status is '{consent_status}'. Use non-biometric manual attendance.")
            return False, f"Consent {consent_status} (Use Manual)", 0.0

        if not profile.get("face_embedding_encrypted"):
            print(f"[Verification Failed] No biometric template on file for '{canonical_id}'. Use non-biometric manual attendance.")
            return False, "No Biometric Template", 0.0

        # 2. Load registered embedding
        try:
            registered_emb_json = security.decrypt_data(profile["face_embedding_encrypted"])
            registered_emb_list = json.loads(registered_emb_json)
            registered_emb = np.array(registered_emb_list, dtype=np.float32)
        except Exception as e:
            print(f"[Error] Failed to decrypt or parse registered embedding for {student_id}: {e}")
            return False, "Decrypt Error", 0.0

        # 3. Match using SFace (Cosine Similarity)
        score = float(scanner.face_recognizer.match(embedding, registered_emb, cv2.FaceRecognizerSF_FR_COSINE))
        MATCH_THRESHOLD = 0.42 # Strict zero-tolerance threshold for Cosine similarity
        
        if score >= MATCH_THRESHOLD:
            # 1. Prevent duplicate check-in for the same student on the same day
            if db.has_checked_in_today(canonical_id):
                print(f"[Skip] {profile['name']} ({canonical_id}) has already checked in today.")
                return True, f"Already Logged: {profile['name']}", score

            # 2. Determine dynamic status based on time
            status = calculate_attendance_status()
            
            print(f"[Verified] {profile['name']} ({canonical_id}) marked as: {status.upper()} (Similarity: {score:.4f})")

            # 3. Encrypt live face embedding
            live_emb_json = json.dumps(embedding.tolist())
            encrypted_emb = security.encrypt_data(live_emb_json)

            # 4. Log to SQLite with the evaluated status
            if db.log_attendance(canonical_id, encrypted_emb, status=status):
                print(f"[Local DB] Record saved with status '{status}'.")
                notifier.send_alert(RECEIVER_EMAIL, f"{canonical_id} [{status}]")
                return True, f"Logged {status}: {profile['name']}", score
            else:
                print(f"[Error] Failed to log attendance for {canonical_id}.")
                return False, "Database Write Error", score
        else:
            print(f"[Verification Failed] Face mismatch for student {canonical_id} (Similarity: {score:.4f} < {MATCH_THRESHOLD}). Attendance rejected.")
            return False, f"Face Mismatch ({score:.2f} < {MATCH_THRESHOLD})", score

    # 3. Start scanning loop
    scanner = AttendanceScanner()
    try:
        print("[System] Scanning for QR and Face...")
        scanner.run_live_preview(on_success)
    except KeyboardInterrupt:
        print("\n[System] Shutting down...")
    finally:
        sync.stop_background_sync()
        print("[System] Background sync stopped. Goodbye!")

if __name__ == "__main__":
    main()
