# Smart Attendance System

A Python-based attendance tracking application that uses facial recognition and QR code generation to record student attendance.

## Features
- Real‑time dual-factor face + QR recognition check‑in (YuNet + SFace + WeChat QR)
- **Child Data Privacy & Consent Management**: Persistent `PENDING`, `GRANTED`, `WITHDRAWN` states
- **Non-Biometric Attendance Fallback**: Teacher manual entry with auditable reason tracking
- **Role-Based Access Control (RBAC)**: PBKDF2 password authentication for `ADMIN` and `TEACHER`
- **Configurable Retention Policy**: Dry-run previews and confirmed deletion for expired records
- **Tamper-Evident Audit Logging**: Comprehensive security audit trail in local SQLite
- Secure AES-encrypted storage of biometric embeddings (Fernet)
- Offline-first operation with deferred Google Sheets batch synchronization

## Quick Start & Usage

### 1. Run Live Attendance Scanner
```bash
python main.py
```
*(Press `q` to quit, or `m` in preview for manual attendance instructions)*

### 2. Student Biometric Enrollment (Admin Only)
```bash
python register_student.py --id "STU_2026_03" --name "Student Name" --consent GRANTED --authorized-by "Parent Form #101" --user admin --password Admin@1234
```

### 3. Generate QR Badge
```bash
python -c "from generate_qr import create_student_qr; create_student_qr('STU_2026_03', 'Student Name')"
```

### 4. Non-Biometric Manual Attendance Fallback (Teacher / Admin)
```bash
python manual_attendance.py --id "STU_2026_03" --status Present --reason NO_BIOMETRIC_CONSENT --user teacher1 --password Teacher@1234
```

### 5. Consent Management & Biometric Withdrawal (Admin Only)
```bash
# View all student consent states
python consent_manager.py --list --user admin --password Admin@1234

# Withdraw consent and purge biometric embeddings
python consent_manager.py --id "STU_2026_03" --status WITHDRAWN --delete-biometrics --user admin --password Admin@1234
```

### 6. Configurable Data Retention (Admin Only)
```bash
# Preview records eligible for deletion (dry-run)
python retention.py --preview --user admin --password Admin@1234

# Execute approved deletion
python retention.py --cleanup --confirm --user admin --password Admin@1234
```

### 7. Run Privacy & Security Test Suite
```bash
pytest test_privacy_and_security.py -v
```

See [PRIVACY_AND_STUDENT_DATA.md](PRIVACY_AND_STUDENT_DATA.md) for complete privacy architecture, threat model, and legal disclaimers.

## License
MIT License
