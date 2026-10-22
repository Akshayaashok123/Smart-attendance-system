import argparse
import sys
import getpass
from data_layer import DataLayer
from auth import AuthManager

def get_admin_session(db, user, password):
    auth = AuthManager(db)
    if not password:
        if sys.stdin.isatty():
            password = getpass.getpass(f"Enter password for '{user}': ")
        else:
            password = "Admin@1234"
    session = auth.authenticate(user, password)
    if not session:
        print(f"[Security Error] Authentication failed for '{user}'. Invalid password.")
        return None
    if session.get("role") != "ADMIN":
        print(f"[Security Error] Unauthorized: User '{user}' has role '{session.get('role')}', but ADMIN is required.")
        return None
    return session

def list_consents(db):
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT student_id, name, consent_status, consent_date, consent_authorized_by, 
                   consent_method, (face_embedding_encrypted IS NOT NULL)
            FROM registered_students
            ORDER BY student_id
            """
        )
        rows = cursor.fetchall()
        print("\n" + "=" * 90)
        print(f"{'STUDENT ID':<15} {'NAME':<18} {'CONSENT':<12} {'HAS BIOMETRICS':<16} {'AUTHORIZED BY':<20}")
        print("=" * 90)
        for r in rows:
            sid, name, c_status, c_date, auth_by, c_meth, has_bio = r
            has_bio_str = "YES (Encrypted)" if has_bio else "NO"
            print(f"{sid:<15} {name:<18} {c_status:<12} {has_bio_str:<16} {str(auth_by or '-'):<20}")
        print("=" * 90)
        print(f"Total students: {len(rows)}\n")

def main():
    parser = argparse.ArgumentParser(description="Manage student biometric consent and withdrawal.")
    parser.add_argument("--list", action="store_true", help="List consent status for all registered students.")
    parser.add_argument("--id", help="Target Student ID.")
    parser.add_argument("--status", choices=["PENDING", "GRANTED", "WITHDRAWN"], help="New consent state.")
    parser.add_argument("--authorized-by", default="PARENT/GUARDIAN", help="Authorizer reference/role.")
    parser.add_argument("--method", choices=["WRITTEN_FORM", "PORTAL", "IN_PERSON"], default="WRITTEN_FORM")
    parser.add_argument("--delete-biometrics", action="store_true", help="Securely purge stored facial embeddings on withdrawal.")
    parser.add_argument("--user", default="admin", help="Admin username.")
    parser.add_argument("--password", help="Admin password.")

    args = parser.parse_args()
    db = DataLayer()

    session = get_admin_session(db, args.user, args.password)
    if not session:
        sys.exit(1)

    if args.list:
        list_consents(db)
        return

    if args.id and args.status:
        success = db.update_consent(
            student_id=args.id,
            new_status=args.status,
            authorized_by=args.authorized_by,
            consent_method=args.method,
            actor_username=session["username"]
        )
        if success:
            print(f"[Success] Updated consent for student {args.id} to '{args.status}'.")
            if args.status == "WITHDRAWN":
                print("[Privacy Notice] Future biometric verification is now disabled for this student.")
                print("[Privacy Notice] Historical attendance records are preserved under school record policy.")
                if args.delete_biometrics:
                    del_ok = db.delete_biometric_embedding(args.id, actor_username=session["username"])
                    if del_ok:
                        print(f"[Success] Stored facial embedding for student {args.id} has been permanently deleted.")
                    else:
                        print(f"[Error] Failed to purge facial embedding for {args.id}.")
        else:
            print(f"[Error] Failed to update consent for student {args.id}.")
            sys.exit(1)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
