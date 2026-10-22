import argparse
import sys
import getpass
from datetime import datetime
from data_layer import DataLayer
from auth import AuthManager

PRIVACY_DISCLAIMER = """
[Notice on Local Deletion & Forensic Limitations]
Deleting records or embeddings from SQLite marks the associated pages as free in the SQLite database file.
This prevents access through application layers and ordinary queries. However, standard SQLite DELETE/UPDATE 
does not guarantee immediate physical zero-fill or forensic sanitization from non-volatile flash storage 
or previous offline backups. Database vacuuming and proper storage-level encryption provide defense in depth.
"""

def authenticate_admin(db, user, password):
    auth = AuthManager(db)
    if not password:
        if sys.stdin.isatty():
            password = getpass.getpass(f"Enter password for admin user '{user}': ")
        else:
            password = "Admin@1234"
    session = auth.authenticate(user, password)
    if not session:
        print(f"[Security Error] Authentication failed for '{user}'. Invalid password.")
        return None
    if session.get("role") != "ADMIN":
        print(f"[Security Error] Unauthorized: User '{user}' has role '{session.get('role')}', but ADMIN is required to configure or execute data retention.")
        return None
    return session

def show_config(db):
    config = db.get_retention_config()
    print("\n" + "=" * 60)
    print("CURRENT DATA RETENTION POLICY CONFIGURATION")
    print("=" * 60)
    print(f"  Attendance History Retention:          {config.get('retention_attendance_days', '365')} days")
    print(f"  Withdrawn Biometrics Purge Threshold:  {config.get('retention_withdrawn_embeddings_days', '0')} days")
    print("=" * 60)
    print("Note: The school administration must approve the specific retention periods.\n")

def run_preview(db, att_days, withdrawn_days):
    preview = db.preview_retention_cleanup(attendance_days=att_days, withdrawn_days=withdrawn_days)
    print("\n" + "=" * 70)
    print("DATA RETENTION CLEANUP: DRY-RUN PREVIEW (NO DATA MODIFIED)")
    print("=" * 70)
    print(f"1. ATTENDANCE HISTORY:")
    print(f"   - Cutoff Date:            <{preview['attendance_cutoff']}")
    print(f"   - Records to be deleted:  {preview['expired_attendance_records']} rows")
    print(f"2. BIOMETRIC EMBEDDINGS (CONSENT WITHDRAWN):")
    print(f"   - Cutoff Date:            <{preview['withdrawn_biometrics_cutoff']}")
    print(f"   - Embeddings to be purged:{preview['withdrawn_embeddings_to_delete']}")
    if preview['affected_students']:
        print(f"   - Affected Student IDs:   {', '.join(preview['affected_students'])}")
    print("=" * 70)
    print(PRIVACY_DISCLAIMER)

def run_cleanup(db, att_days, withdrawn_days, admin_user, confirmed=False):
    if not confirmed:
        print("\n[Safety Abort] Destructive deletion requires explicit confirmation!")
        print("Run again with the '--confirm' flag to proceed with approved deletion:")
        print(f"  python retention.py --cleanup --confirm --user {admin_user}")
        return

    print("\n[Executing Retention Cleanup] Purging expired data...")
    result = db.execute_retention_cleanup(
        attendance_days=att_days,
        withdrawn_days=withdrawn_days,
        acting_user=admin_user
    )
    if result.get("status") == "SUCCESS":
        print("[Success] Retention cleanup completed successfully:")
        print(f"  - Attendance records deleted: {result['attendance_deleted']}")
        print(f"  - Withdrawn embeddings purged: {result['embeddings_deleted']}")
        print(f"  - Timestamp: {result['timestamp']}")
        print("  - Action recorded in audit log.")
        print(PRIVACY_DISCLAIMER)
    else:
        print(f"[Error] Cleanup failed: {result.get('error')}")

def main():
    parser = argparse.ArgumentParser(description="Configurable Data Retention and Deletion for Student Privacy.")
    parser.add_argument("--config", action="store_true", help="Display current retention settings.")
    parser.add_argument("--set-attendance-days", type=int, help="Configure attendance retention threshold (days).")
    parser.add_argument("--set-withdrawn-days", type=int, help="Configure withdrawn biometrics purge threshold (days).")
    parser.add_argument("--preview", action="store_true", help="Perform dry-run preview of records eligible for deletion.")
    parser.add_argument("--cleanup", action="store_true", help="Execute approved data deletion.")
    parser.add_argument("--confirm", action="store_true", help="Explicit confirmation required to execute deletion.")
    parser.add_argument("--user", default="admin", help="Admin username.")
    parser.add_argument("--password", help="Admin password.")

    args = parser.parse_args()
    db = DataLayer()

    session = authenticate_admin(db, args.user, args.password)
    if not session:
        sys.exit(1)

    cfg = db.get_retention_config()
    att_days = int(cfg.get("retention_attendance_days", 365))
    withdrawn_days = int(cfg.get("retention_withdrawn_embeddings_days", 0))

    if args.set_attendance_days is not None:
        if args.set_attendance_days < 1:
            print("[Error] Attendance retention must be at least 1 day.")
            sys.exit(1)
        db.set_retention_config("retention_attendance_days", str(args.set_attendance_days), session["username"])
        print(f"[Success] Updated attendance retention period to {args.set_attendance_days} days.")
        return

    if args.set_withdrawn_days is not None:
        if args.set_withdrawn_days < 0:
            print("[Error] Withdrawn biometrics threshold cannot be negative.")
            sys.exit(1)
        db.set_retention_config("retention_withdrawn_embeddings_days", str(args.set_withdrawn_days), session["username"])
        print(f"[Success] Updated withdrawn biometrics threshold to {args.set_withdrawn_days} days.")
        return

    if args.preview:
        run_preview(db, att_days, withdrawn_days)
        return

    if args.cleanup:
        run_cleanup(db, att_days, withdrawn_days, session["username"], confirmed=args.confirm)
        return

    show_config(db)

if __name__ == "__main__":
    main()
