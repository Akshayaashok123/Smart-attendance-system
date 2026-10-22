import argparse
import sys
import os
import cv2
import json
import numpy as np
from data_layer import DataLayer
from security import SecurityManager
from hardware_ui import AttendanceScanner

def register_from_image(student_id, name, image_path, db, security, scanner):
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
    if existing_id:
        print(f"[Error] Face already registered under ID: {existing_id}")
        return False

    encrypted_emb = security.encrypt_data(json.dumps(embedding_list))
    
    if db.register_student(student_id, name, encrypted_emb):
        print(f"[Success] Registered student {student_id} ({name}) successfully using image.")
        return True
    else:
        print(f"[Error] Failed to save registration in database.")
        return False

def register_from_webcam(student_id, name, db, security, scanner):
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
        # Convert embedding to JSON list and encrypt
        embedding_list = captured_embedding.tolist()

        # Check if face is already registered
        existing_id = db.is_face_already_registered(captured_embedding, scanner, security, threshold=0.42)
        if existing_id:
            print(f"[Error] Face already registered under ID: {existing_id}")
            return False

        encrypted_emb = security.encrypt_data(json.dumps(embedding_list))
        
        if db.register_student(student_id, name, encrypted_emb):
            print(f"[Success] Registered student {student_id} ({name}) successfully.")
            return True
        else:
            print("[Error] Failed to save registration in database.")
            return False
            
    return False

def main():
    parser = argparse.ArgumentParser(description="Register a student's face for dual-factor attendance.")
    parser.add_argument("--id", required=True, help="Unique Student ID")
    parser.add_argument("--name", required=True, help="Student Name")
    parser.add_argument("--image", help="Optional path to an image file of the student's face. If not provided, webcam will be used.")
    
    args = parser.parse_args()
    
    db = DataLayer()
    security = SecurityManager()
    scanner = AttendanceScanner()
    
    if args.image:
        success = register_from_image(args.id, args.name, args.image, db, security, scanner)
    else:
        success = register_from_webcam(args.id, args.name, db, security, scanner)
        
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
