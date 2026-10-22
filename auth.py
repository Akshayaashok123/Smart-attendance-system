import os
import hashlib
import hmac
from datetime import datetime
from typing import Optional, Dict, Any, List

class AuthManager:
    """
    Manages Role-Based Access Control (RBAC) and offline authentication.
    Roles:
      - 'ADMIN': manage users, consent, retention settings, embedding deletion, audit logs.
      - 'TEACHER': take attendance, use manual fallback, view minimal required student info.
    """
    PBKDF2_ITERATIONS = 600_000

    def __init__(self, db_layer):
        self.db = db_layer
        self._ensure_default_users()

    @staticmethod
    def hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
        """Hashes a password with PBKDF2-HMAC-SHA256 and a random 16-byte salt."""
        if salt is None:
            salt = os.urandom(16)
        pwd_hash = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt,
            AuthManager.PBKDF2_ITERATIONS
        )
        return pwd_hash.hex(), salt.hex()

    @staticmethod
    def verify_password(password: str, salt_hex: str, expected_hash_hex: str) -> bool:
        """Verifies a password against the stored salt and hash using constant-time comparison."""
        try:
            salt = bytes.fromhex(salt_hex)
            computed_hash, _ = AuthManager.hash_password(password, salt=salt)
            return hmac.compare_digest(computed_hash, expected_hash_hex)
        except Exception:
            return False

    def _ensure_default_users(self):
        """Initializes default admin and teacher accounts if no users exist."""
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users")
            count = cursor.fetchone()[0]
            if count == 0:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # Default Admin: admin / Admin@1234
                admin_hash, admin_salt = self.hash_password("Admin@1234")
                cursor.execute(
                    "INSERT INTO users (username, password_hash, salt, role, full_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    ("admin", admin_hash, admin_salt, "ADMIN", "System Administrator", now)
                )
                # Default Teacher: teacher1 / Teacher@1234
                t_hash, t_salt = self.hash_password("Teacher@1234")
                cursor.execute(
                    "INSERT INTO users (username, password_hash, salt, role, full_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    ("teacher1", t_hash, t_salt, "TEACHER", "Class Teacher 1", now)
                )
                conn.commit()
                # Log audit event
                self.db.log_audit_event("SYSTEM", "INITIALIZE_DEFAULT_USERS", "users", "Default admin and teacher accounts initialized", "SUCCESS")

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Authenticates user credentials against local SQLite database."""
        if not username or not password:
            return None
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT username, password_hash, salt, role, full_name FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            if not row:
                self.db.log_audit_event(username, "LOGIN_ATTEMPT", username, "User not found", "FAILED")
                return None
            
            u_name, p_hash, salt_hex, role, full_name = row
            if self.verify_password(password, salt_hex, p_hash):
                self.db.log_audit_event(u_name, "LOGIN_SUCCESS", u_name, f"Role: {role}", "SUCCESS")
                return {
                    "username": u_name,
                    "role": role,
                    "full_name": full_name
                }
            else:
                self.db.log_audit_event(username, "LOGIN_FAILURE", username, "Incorrect password", "FAILED")
                return None

    def require_role(self, session: Optional[Dict[str, Any]], allowed_roles: List[str]):
        """Enforces least-privilege role requirement. Raises PermissionError if unauthorized."""
        if not session or "role" not in session:
            raise PermissionError("Authentication required: No active session.")
        if session["role"] not in allowed_roles:
            raise PermissionError(
                f"Unauthorized action: Role '{session['role']}' is not permitted. Required: {allowed_roles}"
            )

    def create_user(self, acting_session: Dict[str, Any], new_username: str, password: str, role: str, full_name: str) -> bool:
        """Creates a new user. Only ADMIN can perform this action."""
        self.require_role(acting_session, ["ADMIN"])
        if role not in ("ADMIN", "TEACHER"):
            raise ValueError("Role must be 'ADMIN' or 'TEACHER'.")
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")
        
        p_hash, salt = self.hash_password(password)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self.db._get_connection() as conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash, salt, role, full_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (new_username, p_hash, salt, role, full_name, now)
                )
                conn.commit()
            self.db.log_audit_event(acting_session["username"], "CREATE_USER", new_username, f"Assigned role: {role}", "SUCCESS")
            return True
        except Exception as e:
            self.db.log_audit_event(acting_session["username"], "CREATE_USER", new_username, str(e), "FAILED")
            return False
