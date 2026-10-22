import os
from cryptography.fernet import Fernet

class SecurityManager:
    """Handles encryption and decryption of sensitive data like face embeddings."""
    
    def __init__(self, key_file="secret.key"):
        self.key_file = key_file
        self.key = self._load_or_generate_key()
        self.cipher = Fernet(self.key)

    def _load_or_generate_key(self):
        """Loads the secret key from disk or generates a new one if it doesn't exist."""
        if os.path.exists(self.key_file):
            with open(self.key_file, "rb") as f:
                key = f.read()
            print(f"[Security] Encryption key loaded from '{self.key_file}'.")
            print(f"[Security] WARNING: Back up '{self.key_file}' securely. Losing it means all registered face embeddings become unreadable and all students must re-register.")
            return key
        else:
            key = Fernet.generate_key()
            with open(self.key_file, "wb") as f:
                f.write(key)
            print(f"[Security] New encryption key generated and saved to '{self.key_file}'.")
            print(f"[Security] WARNING: Back up '{self.key_file}' securely. Losing it means all registered face embeddings become unreadable.")
            return key

    def encrypt_data(self, data: str) -> str:
        """Encrypts a string (e.g., embedding string) and returns the base64 encoded string."""
        if not data:
            return ""
        encrypted_bytes = self.cipher.encrypt(data.encode())
        return encrypted_bytes.decode()

    def decrypt_data(self, encrypted_data: str) -> str:
        """Decrypts a base64 encoded string and returns the original string."""
        if not encrypted_data:
            return ""
        decrypted_bytes = self.cipher.decrypt(encrypted_data.encode())
        return decrypted_bytes.decode()

if __name__ == "__main__":
    # Quick test
    sm = SecurityManager()
    test_str = "[0.12, -0.45, 0.88, ...]"
    encrypted = sm.encrypt_data(test_str)
    decrypted = sm.decrypt_data(encrypted)
    print(f"Original: {test_str}")
    print(f"Encrypted: {encrypted}")
    print(f"Decrypted: {decrypted}")
    assert test_str == decrypted
    print("Security check passed!")
