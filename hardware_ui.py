import cv2
import numpy as np
import time
import requests
import os
import sys

class AttendanceScanner:
    """Manages Camera, QR Scanning, and Face Recognition using YuNet and SFace."""
    
    def __init__(self, model_dir="models"):
        self.model_dir = model_dir
        self.yunet_path = os.path.join(model_dir, "face_detection_yunet_2023mar.onnx")
        self.sface_path = os.path.join(model_dir, "face_recognition_sface_2021dec.onnx")
        self._ensure_models_exist()
        
        self.qr_detector = cv2.QRCodeDetector()
        
        # Initialize YuNet Face Detector
        # input_size is dynamic, will be updated in scan_frame
        self.face_detector = cv2.FaceDetectorYN.create(
            model=self.yunet_path,
            config="",
            input_size=(320, 320),
            score_threshold=0.6,
            nms_threshold=0.3,
            top_k=5000
        )
        
        # Initialize SFace Face Recognizer
        self.face_recognizer = cv2.FaceRecognizerSF.create(
            model=self.sface_path,
            config=""
        )
        self.cap = None

    def _ensure_models_exist(self):
        """Downloads YuNet and SFace models if not present."""
        if not os.path.exists(self.model_dir):
            os.makedirs(self.model_dir)
            
        # Download YuNet face detector model
        if not os.path.exists(self.yunet_path):
            print("Downloading YuNet face detection model...")
            url = "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx"
            try:
                r = requests.get(url)
                r.raise_for_status()
                with open(self.yunet_path, 'wb') as f:
                    f.write(r.content)
                print("YuNet download complete.")
            except Exception as e:
                print(f"Error downloading YuNet from Hugging Face: {e}. Trying GitHub...")
                url = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
                r = requests.get(url)
                with open(self.yunet_path, 'wb') as f:
                    f.write(r.content)
                print("YuNet download complete.")

        # Download SFace face recognizer model
        if not os.path.exists(self.sface_path):
            print("Downloading SFace face recognition model (approx. 34MB)...")
            url = "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx"
            try:
                r = requests.get(url)
                r.raise_for_status()
                with open(self.sface_path, 'wb') as f:
                    f.write(r.content)
                print("SFace download complete.")
            except Exception as e:
                print(f"Error downloading SFace from Hugging Face: {e}. Trying GitHub...")
                url = "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
                r = requests.get(url)
                with open(self.sface_path, 'wb') as f:
                    f.write(r.content)
                print("SFace download complete.")

    def start_camera(self):
        # On Windows, DirectShow (CAP_DSHOW) is faster and more reliable
        self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(0)
        return self.cap.isOpened()

    def stop_camera(self):
        if self.cap:
            self.cap.release()
            cv2.destroyAllWindows()

    def scan_frame(self, frame):
        """
        Processes a single frame for Dual-Factor Verification.
        Returns (student_id, face_detected, embedding, faces)
        """
        # 1. QR Scan (using OpenCV built-in)
        student_id = None
        retval, decoded_info, points, straight_qrcode = self.qr_detector.detectAndDecodeMulti(frame)
        if retval and decoded_info and any(decoded_info):
            student_id = [info for info in decoded_info if info][0]

        # 2. Face Detection using YuNet
        h, w, _ = frame.shape
        self.face_detector.setInputSize((w, h))
        _, faces = self.face_detector.detect(frame)
        
        face_detected = faces is not None and len(faces) > 0
        embedding = None
        
        if face_detected:
            # Use the first face detected (highest confidence score)
            face = faces[0]
            
            # Align and crop the face using SFace
            aligned_face = self.face_recognizer.alignCrop(frame, face)
            
            # Compute 128-dimensional embedding
            embedding = self.face_recognizer.feature(aligned_face)

        return student_id, face_detected, embedding, faces

    def run_live_preview(self, callback):
        """Runs a live camera feed and calls callback(student_id, embedding) on detection."""
        if not self.start_camera():
            print("Could not open camera.")
            return

        print("Press 'q' to quit.")
        last_logged_time = 0
        cooldown = 5 # seconds

        while True:
            ret, frame = self.cap.read()
            if not ret:
                print("[System] Failed to read frame from camera.")
                break

            student_id, face_detected, embedding, faces = self.scan_frame(frame)

            # UI Overlays
            if faces is not None:
                for face in faces:
                    x, y, w, h = map(int, face[0:4])
                    cv2.rectangle(frame, (x, y), (x+w, y+h), (255, 0, 0), 2)

            status_text = "Waiting for QR + Face..."
            color = (0, 0, 255)

            if student_id and face_detected:
                status_text = f"Verified ID: {student_id} — Processing..."
                color = (0, 255, 0)

                # Trigger attendance if cooldown passed
                if time.time() - last_logged_time > cooldown:
                    callback(student_id, embedding)
                    status_text = f"Verified ID: {student_id} — Done"
                    last_logged_time = time.time()

            cv2.putText(frame, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            try:
                cv2.imshow("Attendance Scanner (Dual-Factor)", frame)
            except cv2.error as e:
                print("[Warning] OpenCV GUI not available; skipping frame display.")
                # In headless mode, we still need a short wait to avoid busy loop
                cv2.waitKey(1)
            else:
                # Wait 1ms for key press, if 'q' is pressed exit
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    print("[System] Quit key pressed.")
                    break

        self.stop_camera()

if __name__ == "__main__":
    def my_callback(sid, emb):
        print(f"ATTENDANCE LOGGED: {sid}, embedding shape: {emb.shape}")

    scanner = AttendanceScanner()
    scanner.run_live_preview(my_callback)
