import argparse
import sys
import os
import cv2
import json
import numpy as np
import getpass
from data_layer import DataLayer
from security import SecurityManager
from hardware_ui import AttendanceScanner
from auth import AuthManager

PRIVACY_NOTICE = """
================================================================================
                    CHILD DATA PRIVACY & BIOMETRIC NOTICE
Facial enrollment involves capturing numerical measurements of facial geometry 
(128-dimensional embedding). No raw photos are stored or synced to the cloud.
Embeddings are AES-encrypted at rest and used strictly for attendance verification.
Biometric enrollment requires verified authorization from a parent or legal guardian.
Students without biometric consent are fully supported via the manual attendance fallback.
================================================================================
"""

def register_from_image(student_id, name, image_path, db, security, scanner, 
                        consent_status="GRANTED", authorized_by="PARENT/GUARDIAN", 
                        consent_method="WRITTEN_FORM", actor_username="admin"):
    if consent_status != "GRANTED":
        print(f"[Privacy Protection] Biometric enrollment blocked: Consent status is '{consent_status}'.")
        # Save record without biometric embedding
        try:
            with db._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO registered_students (student_id, name, face_embedding_encrypted, consent_status, consent_authorized_by, consent_method)
                    VALUES (?, ?, NULL, ?, ?, ?)
                    ON CONFLICT(student_id) DO UPDATE SET
                        name = excluded.name,
                        face_embedding_encrypted = NULL,
                        consent_status = excluded.consent_status,
                        consent_authorized_by = excluded.consent_authorized_by,
                        consent_method = excluded.consent_method
                    """,
                    (student_id, name, consent_status, authorized_by, consent_method)
                )
                conn.commit()
            db.log_audit_event(actor_username, "STUDENT_REGISTER_NO_BIOMETRICS", student_id, f"Registered without biometrics (Consent: {consent_status})", "SUCCESS")
            print(f"[Success] Student {student_id} ({name}) registered for NON-BIOMETRIC attendance fallback.")
            return True
        except Exception as e:
            print(f"[Error] Failed to register student: {e}")
            return False

    if not os.path.exists(image_path):
        print(f"[Error] Image file not found: {image_path}")
        return False
        
    frame = cv2.imread(image_path)
    if frame is None:
        print(f"[Error] Could not read image: {image_path}")
        return False

    _, face_detected, embedding, faces = scanner.scan_frame(frame)
    if not face_detected or embedding is None:
        print("[Error] No face detected in the image.")
        return False

    # Convert embedding to JSON list and encrypt
    embedding_list = embedding.tolist()
    
    # Check if face is already registered
    existing_id = db.is_face_already_registered(embedding, scanner, security, threshold=0.42)
    if existing_id and existing_id != student_id:
        print(f"[Error] Face already registered under ID: {existing_id}")
        return False

    encrypted_emb = security.encrypt_data(json.dumps(embedding_list))
    
    if db.register_student(student_id, name, encrypted_emb, 
                           consent_status=consent_status, authorized_by=authorized_by, 
                           consent_method=consent_method, actor_username=actor_username):
        print(f"[Success] Registered student {student_id} ({name}) successfully with verified biometric consent.")
        return True
    else:
        print(f"[Error] Failed to save registration in database.")
        return False

def register_from_webcam(student_id, name, db, security, scanner,
                         consent_status="GRANTED", authorized_by="PARENT/GUARDIAN", 
                         consent_method="WRITTEN_FORM", actor_username="admin"):
    if consent_status != "GRANTED":
        print(f"[Privacy Protection] Biometric enrollment blocked: Consent status is '{consent_status}'.")
        try:
            with db._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO registered_students (student_id, name, face_embedding_encrypted, consent_status, consent_authorized_by, consent_method)
                    VALUES (?, ?, NULL, ?, ?, ?)
                    ON CONFLICT(student_id) DO UPDATE SET
                        name = excluded.name,
                        face_embedding_encrypted = NULL,
                        consent_status = excluded.consent_status,
                        consent_authorized_by = excluded.consent_authorized_by,
                        consent_method = excluded.consent_method
                    """,
                    (student_id, name, consent_status, authorized_by, consent_method)
                )
                conn.commit()
            db.log_audit_event(actor_username, "STUDENT_REGISTER_NO_BIOMETRICS", student_id, f"Registered without biometrics (Consent: {consent_status})", "SUCCESS")
            print(f"[Success] Student {student_id} ({name}) registered for NON-BIOMETRIC attendance fallback.")
            return True
        except Exception as e:
            print(f"[Error] Failed to register student: {e}")
            return False

    if not scanner.start_camera():
        print("[Error] Could not open camera.")
        return False
        
    print("\n--- Student Face Registration ---")
    print(f"Registering ID: {student_id}")
    print(f"Name: {name}")
    print("Look at the camera. Press 'c' to capture face or 'q' to cancel.")
    
    captured_embedding = None
    
    while True:
        ret, frame = scanner.cap.read()
        if not ret:
            print("[Error] Failed to read frame from camera.")
            break
            
        _, face_detected, embedding, faces = scanner.scan_frame(frame)
        
        # UI overlays
        if faces is not None:
            for face in faces:
                x, y, w, h = map(int, face[0:4])
                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
                
        status_text = "Press 'c' to capture face" if face_detected else "No face detected"
        color = (0, 255, 0) if face_detected else (0, 0, 255)
        
        cv2.putText(frame, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        cv2.imshow("Register Face (Press 'c' to capture, 'q' to quit)", frame)
        
        key = cv2.waitKey(1) & 0xFF
        if key == ord('c'):
            if face_detected and embedding is not None:
                captured_embedding = embedding
                print("[Capture] Face embedding successfully extracted.")
                break
            else:
                print("[Warning] No face detected! Cannot capture.")
        elif key == ord('q'):
            print("[Cancel] Registration cancelled.")
            break
            
    scanner.stop_camera()
    
    if captured_embedding is not None:
        embedding_list = captured_embedding.tolist()

        existing_id = db.is_face_already_registered(captured_embedding, scanner, security, threshold=0.42)
        if existing_id and existing_id != student_id:
            print(f"[Error] Face already registered under ID: {existing_id}")
            return False

        encrypted_emb = security.encrypt_data(json.dumps(embedding_list))
        
        if db.register_student(student_id, name, encrypted_emb,
                               consent_status=consent_status, authorized_by=authorized_by,
                               consent_method=consent_method, actor_username=actor_username):
            print(f"[Success] Registered student {student_id} ({name}) successfully with verified biometric consent.")
            return True
        else:
            print("[Error] Failed to save registration in database.")
            return False
            
    return False

def main():
    parser = argparse.ArgumentParser(description="Register a student profile with privacy consent tracking.")
    parser.add_argument("--id", required=True, help="Unique Student ID")
    parser.add_argument("--name", required=True, help="Student Name")
    parser.add_argument("--image", help="Optional path to an image file. If not provided, webcam will be used.")
    parser.add_argument("--consent", choices=["GRANTED", "PENDING", "WITHDRAWN"], default="GRANTED",
                        help="Authorization state for biometric processing (default: GRANTED).")
    parser.add_argument("--authorized-by", default="PARENT/GUARDIAN",
                        help="Reference/Role of authorizing party (e.g., 'Parent - Form #401').")
    parser.add_argument("--consent-method", choices=["WRITTEN_FORM", "PORTAL", "IN_PERSON", "INITIAL_SYSTEM_SETUP"],
                        default="WRITTEN_FORM", help="Method by which consent was verified.")
    parser.add_argument("--user", default="admin", help="Admin username performing the enrollment.")
    parser.add_argument("--password", help="Admin password for authentication.")
    
    args = parser.parse_args()
    
    print(PRIVACY_NOTICE)
    
    db = DataLayer()
    auth = AuthManager(db)
    
    # Enforce Role-Based Access Control: enrollment requires ADMIN
    admin_pass = args.password
    if not admin_pass:
        # Check if environment or default is available, or prompt
        if sys.stdin.isatty():
            admin_pass = getpass.getpass(f"Enter password for admin user '{args.user}': ")
        else:
            admin_pass = "Admin@1234"
            
    session = auth.authenticate(args.user, admin_pass)
    if not session or session.get("role") != "ADMIN":
        print(f"[Security Error] Unauthorized: User '{args.user}' must have ADMIN role to enroll students or manage biometric data.")
        sys.exit(1)
        
    security = SecurityManager()
    scanner = AttendanceScanner()
    
    if args.image:
        success = register_from_image(
            args.id, args.name, args.image, db, security, scanner,
            consent_status=args.consent, authorized_by=args.authorized_by,
            consent_method=args.consent_method, actor_username=session["username"]
        )
    else:
        success = register_from_webcam(
            args.id, args.name, db, security, scanner,
            consent_status=args.consent, authorized_by=args.authorized_by,
            consent_method=args.consent_method, actor_username=session["username"]
        )
        
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
