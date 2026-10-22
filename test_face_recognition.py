import os
import json
import sqlite3
import pytest
import numpy as np
import cv2
from data_layer import DataLayer
from security import SecurityManager
from hardware_ui import AttendanceScanner

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_attendance.db"
    dl = DataLayer(db_path=str(db_file))
    yield dl

def test_database_registration(temp_db):
    student_id = "STU001"
    name = "Alice Smith"
    fake_embedding = json.dumps(np.random.rand(1, 128).tolist())
    
    # Register
    success = temp_db.register_student(student_id, name, fake_embedding)
    assert success is True
    
    # Retrieve
    profile = temp_db.get_registered_student(student_id)
    assert profile is not None
    assert profile["name"] == name
    assert profile["face_embedding_encrypted"] == fake_embedding
    
    # Retrieve non-existing
    assert temp_db.get_registered_student("NON_EXISTENT") is None

def test_encryption_integration():
    security = SecurityManager()
    embedding_array = np.random.rand(1, 128)
    embedding_json = json.dumps(embedding_array.tolist())
    
    encrypted = security.encrypt_data(embedding_json)
    assert encrypted != embedding_json
    
    decrypted = security.decrypt_data(encrypted)
    assert decrypted == embedding_json
    
    decrypted_list = json.loads(decrypted)
    decrypted_array = np.array(decrypted_list, dtype=np.float32)
    
    assert np.allclose(embedding_array, decrypted_array)

def test_scanner_initialization(tmp_path):
    # Tests that the AttendanceScanner downloads models and initializes without crash
    model_dir = tmp_path / "models"
    scanner = AttendanceScanner(model_dir=str(model_dir))
    
    assert os.path.exists(scanner.yunet_path)
    assert os.path.exists(scanner.sface_path)
    assert scanner.face_detector is not None
    assert scanner.face_recognizer is not None

def test_sface_matching_logic():
    # Make sure SFace match works on random normalized vectors
    scanner = AttendanceScanner()
    
    # Generate 2 random 128-dim features
    feat1 = np.random.rand(1, 128).astype(np.float32)
    feat1 = feat1 / np.linalg.norm(feat1)
    
    feat2 = np.random.rand(1, 128).astype(np.float32)
    feat2 = feat2 / np.linalg.norm(feat2)
    
    score = scanner.face_recognizer.match(feat1, feat2, cv2.FaceRecognizerSF_FR_COSINE)
    assert isinstance(score, float)
    
    # Comparing the same feature should result in perfect similarity (1.0)
    score_self = scanner.face_recognizer.match(feat1, feat1, cv2.FaceRecognizerSF_FR_COSINE)
    assert pytest.approx(score_self, 1e-4) == 1.0
