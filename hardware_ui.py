import cv2
import numpy as np
import time
import requests
import os
import sys

class AttendanceScanner:
    """Manages Camera, QR Scanning, and Face Recognition via YuNet and SFace."""
    
    def __init__(self, model_dir="models"):
        self.model_dir = model_dir
        self.yunet_path = os.path.join(model_dir, "face_detection_yunet_2023mar.onnx")
        self.sface_path = os.path.join(model_dir, "face_recognition_sface_2021dec.onnx")
        self._ensure_models_exist()
        
        # QR detectors - try WeChat (more robust), fall back to standard
        self.qr_detector = cv2.QRCodeDetector()
        try:
            self.wechat_qr = cv2.wechat_qrcode.WeChatQRCode()
            self.use_wechat_qr = True
            print("[QR] Using WeChat QR detector (enhanced reliability)")
        except Exception:
            self.use_wechat_qr = False
            print("[QR] WeChat QR unavailable, using standard detector")
        
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
                r = requests.get(url, timeout=60)
                r.raise_for_status()
                with open(self.yunet_path, 'wb') as f:
                    f.write(r.content)
                print("YuNet download complete.")
            except Exception as e:
                print(f"Error downloading YuNet from Hugging Face: {e}. Trying GitHub...")
                url = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
                r = requests.get(url, timeout=60)
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
                r = requests.get(url, timeout=60)
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
        # 1. QR Scan — try WeChat detector first (most robust), then standard detector fallbacks
        student_id = None
        self.last_qr_points = None

        if self.use_wechat_qr:
            try:
                decoded_info, points = self.wechat_qr.detectAndDecode(frame)
                if decoded_info and any(decoded_info):
                    valid_items = [info for info in decoded_info if info]
                    if valid_items:
                        student_id = valid_items[0].strip()
                        self.last_qr_points = points
            except Exception:
                pass

        if not student_id:
            # Fallback 1: Standard single-shot on original frame
            try:
                decoded_text, points, _ = self.qr_detector.detectAndDecode(frame)
                if decoded_text:
                    student_id = decoded_text.strip()
                    self.last_qr_points = [points] if points is not None else None
            except Exception:
                pass

        if not student_id:
            # Fallback 2: Standard single-shot on grayscale
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                decoded_text, points, _ = self.qr_detector.detectAndDecode(gray)
                if decoded_text:
                    student_id = decoded_text.strip()
                    self.last_qr_points = [points] if points is not None else None
            except Exception:
                pass

        if not student_id:
            # Fallback 3: Standard multi-shot on grayscale
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                retval, decoded_info, points, _ = self.qr_detector.detectAndDecodeMulti(gray)
                if retval and decoded_info and any(decoded_info):
                    valid_items = [info for info in decoded_info if info]
                    if valid_items:
                        student_id = valid_items[0].strip()
                        self.last_qr_points = points
            except Exception:
                pass

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
        cooldown = 4 # seconds
        feedback_status = None
        feedback_color = (0, 0, 255)
        feedback_until = 0
        face_box_color = (255, 0, 0)

        while True:
            ret, frame = self.cap.read()
            if not ret:
                print("[System] Failed to read frame from camera.")
                break

            student_id, face_detected, embedding, faces = self.scan_frame(frame)

            # Trigger verification callback when both QR and Face are present
            if student_id and face_detected:
                if time.time() - last_logged_time > cooldown:
                    res = callback(student_id, embedding)
                    last_logged_time = time.time()
                    feedback_until = time.time() + 3.0 # Display feedback banner for 3s

                    if isinstance(res, tuple) and len(res) >= 2:
                        is_success, msg = res[0], res[1]
                        if is_success:
                            feedback_status = f"SUCCESS: {msg}"
                            feedback_color = (0, 255, 0) # Green
                            face_box_color = (0, 255, 0)
                        else:
                            feedback_status = f"REJECTED: {msg}"
                            feedback_color = (0, 0, 255) # Red
                            face_box_color = (0, 0, 255)
                    elif res is True:
                        feedback_status = f"SUCCESS: Verified {student_id}"
                        feedback_color = (0, 255, 0)
                        face_box_color = (0, 255, 0)
                    else:
                        feedback_status = f"REJECTED: Face mismatch for {student_id}"
                        feedback_color = (0, 0, 255)
                        face_box_color = (0, 0, 255)

            # Determine banner text and face outline color
            if time.time() < feedback_until and feedback_status:
                status_text = feedback_status
                color = feedback_color
            elif student_id and face_detected:
                status_text = f"Evaluating face for {student_id}..."
                color = (0, 255, 255) # Yellow
                face_box_color = (0, 255, 255)
            elif student_id:
                status_text = f"QR Detected: {student_id} (Look at camera)"
                color = (0, 255, 255)
                face_box_color = (255, 0, 0)
            elif face_detected:
                status_text = "Face Detected (Hold up QR code)"
                color = (255, 200, 0)
                face_box_color = (255, 0, 0)
            else:
                status_text = "Waiting for QR + Face..."
                color = (0, 0, 255)
                face_box_color = (255, 0, 0)

            # UI Overlays: Face Bounding Box
            if faces is not None:
                for face in faces:
                    x, y, w, h = map(int, face[0:4])
                    cv2.rectangle(frame, (x, y), (x+w, y+h), face_box_color, 2)
                    label = "Face"
                    if time.time() < feedback_until and feedback_status:
                        label = "MATCH" if feedback_color == (0, 255, 0) else "MISMATCH"
                    cv2.putText(frame, label, (x, max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, face_box_color, 2)

            # UI Overlays: QR Bounding Polygon
            if self.last_qr_points is not None:
                for pts in self.last_qr_points:
                    if pts is not None and len(pts) > 0:
                        pts_int = np.int32(pts).reshape((-1, 1, 2))
                        cv2.polylines(frame, [pts_int], isClosed=True, color=(0, 255, 0), thickness=3)

            # Top background banner for text readability
            cv2.rectangle(frame, (0, 0), (frame.shape[1], 45), (20, 20, 20), -1)
            cv2.putText(frame, status_text, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.72, color, 2)

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
