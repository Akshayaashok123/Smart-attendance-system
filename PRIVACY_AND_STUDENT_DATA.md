# Privacy and Student Data Handling Specification

## Rural-Edge Smart Attendance System

This document outlines the data protection architecture, consent management, access controls, retention and deletion workflows, and risk limitations implemented in the Rural-Edge Smart Attendance System.

---

## 1. Data Collected and Its Purpose

| Data Field | Category | Storage Location | Purpose |
|---|---|---|---|
| **Student ID & Name** | Directory Identity | SQLite (`registered_students`) | Roster identification & mapping QR codes to students. |
| **Facial Biometric Embedding** (128-d vector) | Biometric Identifier | SQLite (`registered_students`, encrypted at rest) | Dual-factor identity verification when presenting a student badge. |
| **Attendance Timestamp & Status** (`Present`/`Late`) | Operational History | SQLite (`attendance`) & Google Sheets | Recording attendance compliance relative to the school cutoff time. |
| **Attendance Method** (`FACE_QR` or `MANUAL`) | Operational Metadata | SQLite (`attendance`) & Google Sheets | Distinguishing automated biometric check-ins from teacher manual check-ins. |
| **Reason Category** | Operational Metadata | SQLite (`attendance`) | Documenting the legitimate cause for non-biometric manual attendance. |
| **Consent Status & Metadata** | Governance Record | SQLite (`registered_students`) | Tracking authorization status (`PENDING`, `GRANTED`, `WITHDRAWN`), date, authorizer, and method. |
| **Audit Logs** | Security Record | SQLite (`audit_logs`) | Accountability trail for administrative actions (consent changes, manual entries, deletions). |

> **Critical Privacy Constraint**: The system **NEVER** stores raw video frames, high-resolution facial images, photographs, plain-text passwords, or private encryption keys in the database or remote storage.

---

## 2. Local Storage vs. Remote Google Sheets Synchronization

### Data Stored Locally (`attendance.db` on Edge Device)
- Encrypted biometric face embeddings (using AES-128-CBC + HMAC-SHA256 via Fernet).
- Full local attendance history with reason codes and recorded-by staff references.
- User authentication tables (`users`) with salted PBKDF2-HMAC-SHA256 password hashes.
- Tamper-evident local audit log (`audit_logs`).
- System retention configuration (`system_config`).

### Data Sent to Google Sheets (Cloud Sync)
- **Only the minimal operational fields**:
  - `student_id`
  - `timestamp`
  - `status` (`Present` / `Late`)
  - `method` (`FACE_QR` / `MANUAL`)
- **Strictly Prohibited from Cloud Transmission**:
  - Facial embeddings, raw camera frames, biometrics, encryption keys, passwords, salts, and consent forms are **NEVER** uploaded to Google Sheets or any external network service.

---

## 3. Consent Management and Biometric Enrollment

1. **Persistent Consent States**:
   - `PENDING`: Student record exists, but parental authorization has not yet been obtained. Biometric capture is strictly prohibited.
   - `GRANTED`: Verified parental or legal guardian authorization has been recorded with authorizer reference and method (`WRITTEN_FORM`, `PORTAL`, `IN_PERSON`). Biometric capture and facial verification are enabled.
   - `WITHDRAWN`: Parental authorization has been revoked. Biometric capture and future camera verification are immediately blocked.
2. **Consent Withdrawal & Embedding Deletion**:
   - When consent is withdrawn via `consent_manager.py`, the system marks `consent_status = 'WITHDRAWN'`.
   - The student's facial embedding can be purged immediately with `--delete-biometrics`, which sets `face_embedding_encrypted = NULL` and `biometric_enrolled_at = NULL`.
   - **Historical Attendance Retention**: Withdrawing consent **does not** delete historical attendance records, which are preserved separately under approved institutional record-keeping policies.

---

## 4. Role-Based Access Control (RBAC)

The system implements offline least-privilege access control:

| Role | Permitted Actions | Prohibited Actions |
|---|---|---|
| **`ADMIN`** | User management, student biometric enrollment, consent status update/withdrawal, retention policy configuration, embedding deletion, audit log inspection. | Cannot bypass encryption or view plaintext biometric embeddings. |
| **`TEACHER`** | Start attendance scanning, take manual attendance fallback, submit auditable attendance corrections, view student names/IDs needed for roster duties. | Cannot enroll biometric faces, delete embeddings, alter consent states, configure retention periods, or create new users. |

### Password Protection
- Passwords are encrypted using PBKDF2-HMAC-SHA256 with 600,000 iterations and a cryptographically secure 16-byte random salt per user. Plaintext passwords are never stored.

---

## 5. Non-Biometric Attendance Fallback

To ensure students are not forced to provide biometric data or penalised for camera/system failures, an authorized teacher or administrator can record attendance manually:
- **CLI Utility**: `python manual_attendance.py --id <STUDENT_ID> --status Present --reason <REASON> --user teacher1`
- **Supported Reason Categories**:
  - `NO_BIOMETRIC_CONSENT` (Student/parent chose not to enroll in biometrics)
  - `CAMERA_FAILURE` (Edge hardware, lighting, or webcam malfunction)
  - `VERIFICATION_FAILURE` (Face mismatch, physical injury, or temporary obstruction)
  - `STUDENT_ASSISTED_ENTRY`
  - `SPECIAL_NEEDS_ACCOMMODATION`
- **Audit & Anti-Duplicate**:
  - Manual records are marked `method = 'MANUAL'`.
  - Same-day duplicate prevention is enforced.
  - Corrections require an explicit explanation and are committed to `audit_logs`.

---

## 6. Data Retention and Deletion Policy

Data retention is separated into two independent categories:
1. **Attendance History**:
   - Default retention period: **365 days** (configurable by `ADMIN` via `python retention.py --set-attendance-days <DAYS>`).
   - Retained to satisfy educational board and government attendance compliance requirements.
2. **Withdrawn Biometric Embeddings**:
   - Default threshold: **0 days** (immediate purge upon confirmed retention execution).
   - Once consent is withdrawn, facial embeddings are permanently cleared from SQLite (`NULL`).

### Two-Step Confirmation Workflow
- **Dry-run / Preview**: `python retention.py --preview` displays exact row counts and affected student IDs without touching the database.
- **Execution**: Requires explicit confirmation (`--cleanup --confirm`).
- **Idempotency**: Repeated executions are safe, idempotent, and logged in `audit_logs`.

---

## 7. Encryption Design and Key Management

- **Algorithm**: Fernet symmetric encryption (AES-128-CBC with PKCS7 padding and HMAC-SHA256 authentication).
- **Key Storage**: `secret.key` is stored on the edge device filesystem with file permissions restricted to the application service user.
- **Key Isolation**: `secret.key` is explicitly ignored by version control (`.gitignore`) and never transmitted over network interfaces.
- **Limitation**: The entire SQLite database is **not** encrypted at rest; only the facial embeddings and live verification buffers are encrypted. Operating-system full-disk encryption (BitLocker / LUKS) must be enabled on the edge hardware for physical device security.

---

## 8. Known Risks and Operational Threat Model

1. **Local-Device Physical Threat**:
   - If an attacker obtains unrestricted root or physical access to the edge hardware, application-level RBAC cannot prevent direct disk forensics. Hardware full-disk encryption and locked device enclosures are required.
2. **Facial Spoofing**:
   - The YuNet/SFace pipeline performs 2D feature recognition with an institutional 0.42 Cosine Similarity threshold. While dual-factor QR matching mitigates random passerby recognition, high-resolution printed photos or digital displays could potentially spoof a 2D camera without dedicated depth/infrared liveness detection.
3. **Forensic Deletion Limits in SQLite**:
   - Deleting rows or updating fields to `NULL` in SQLite frees pages within the `.db` container file, preventing application access. However, forensic recovery from raw NAND flash memory or previous unencrypted backups remains possible until overwritten or vacuumed with secure erase.

---

## 9. Verification & Test Suite Summary

An automated test suite was developed and executed to validate privacy controls:

| Test Case | Module | Result | Verification Detail |
|---|---|---|---|
| `test_consent_workflow` | `test_privacy_and_security.py` | **PASSED** | Validated GRANTED enrollment, PENDING/WITHDRAWN rejection, and historical attendance preservation. |
| `test_role_based_access_control` | `test_privacy_and_security.py` | **PASSED** | Verified PBKDF2 authentication, salt generation, and teacher restriction from admin actions. |
| `test_non_biometric_manual_attendance` | `test_privacy_and_security.py` | **PASSED** | Verified manual fallback, reason tracking, duplicate prevention, and auditable correction. |
| `test_retention_and_deletion` | `test_privacy_and_security.py` | **PASSED** | Verified dry-run preview, confirmed idempotent deletion of expired history and withdrawn embeddings. |
| `test_sync_privacy_and_offline` | `test_privacy_and_security.py` | **PASSED** | Verified offline persistence, batch sync with `method`, zero biometric data in payload, duplicate prevention. |
| `audit_tests.py` (Regression) | `audit_tests.py` | **PASSED** | Full regression check across WAL mode, SFace cosine matching, offline resilience, and batch sync. |
| `test_batch_logic.py` (Regression) | `test_batch_logic.py` | **PASSED** | Confirmed batch construction and column mapping. |
| `test_face_recognition.py` (Regression) | `test_face_recognition.py` | **PASSED** | Verified YuNet, SFace models, and Fernet encryption round-trip. |

---

## 10. Legal & Compliance Disclaimer

> **IMPORTANT NOTICE**:
> Implementation of the technical controls described herein (consent state tracking, non-biometric manual fallback, RBAC, retention policies, and audit logging) provides technical safeguards for child data handling.
> 
> However, **this application must NOT be described as "DPDP compliant" or legally certified merely because these features have been added.**
> 
> Before deploying this system in an educational institution in India or elsewhere:
> 1. The institution must verify applicable provisions of the **Digital Personal Data Protection (DPDP) Act, 2023**, its forthcoming notification rules, parental consent verification regulations, and relevant state educational mandates.
> 2. The school must establish a formal written biometric privacy policy approved by its governing body.
> 3. Verified parental/guardian consent forms must be archived according to institutional legal requirements.
