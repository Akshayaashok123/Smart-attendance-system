import argparse
import sys
import getpass
from datetime import datetime
from data_layer import DataLayer
from auth import AuthManager

def authenticate_staff(db, user, password):
    auth = AuthManager(db)
    if not password:
        if sys.stdin.isatty():
            password = getpass.getpass(f"Enter password for '{user}': ")
        else:
            password = "Teacher@1234" if user == "teacher1" else "Admin@1234"
    session = auth.authenticate(user, password)
    if not session:
        print(f"[Security Error] Authentication failed for '{user}'.")
        return None
    # Both TEACHER and ADMIN can mark manual attendance
    try:
        auth.require_role(session, ["TEACHER", "ADMIN"])
        return session
    except PermissionError as pe:
        print(f"[Security Error] {pe}")
        return None

def list_attendance(db, date_str=None):
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.id, a.student_id, COALESCE(s.name, 'Unknown'), a.timestamp, 
                   a.status, a.method, COALESCE(a.reason_category, '-'), COALESCE(a.recorded_by, 'System')
            FROM attendance a
            LEFT JOIN registered_students s ON a.student_id = s.student_id
            WHERE a.timestamp LIKE ?
            ORDER BY a.id ASC
            """,
            (f"{date_str}%",)
        )
        rows = cursor.fetchall()
        print("\n" + "=" * 110)
        print(f"ATTENDANCE RECORDS FOR {date_str}")
        print("=" * 110)
        print(f"{'REC ID':<8} {'STUDENT ID':<15} {'NAME':<18} {'TIME':<10} {'STATUS':<10} {'METHOD':<12} {'REASON / ACTOR':<25}")
        print("-" * 110)
        for r in rows:
            rec_id, sid, name, t_stamp, status, method, reason, actor = r
            time_part = t_stamp.split(" ")[1] if " " in t_stamp else t_stamp
            method_badge = "[FACE+QR]" if method == "FACE_QR" else "[MANUAL]"
            detail = f"{reason} (by {actor})" if method == "MANUAL" else "Biometric Scan"
            print(f"{rec_id:<8} {sid:<15} {name:<18} {time_part:<10} {status:<10} {method_badge:<12} {detail:<25}")
        print("=" * 110)
        print(f"Total records: {len(rows)}\n")

def main():
    parser = argparse.ArgumentParser(description="Teacher non-biometric manual attendance fallback and corrections.")
    parser.add_argument("--id", help="Student ID to mark attendance for.")
    parser.add_argument("--status", choices=["Present", "Late"], default="Present", help="Attendance status.")
    parser.add_argument("--reason", choices=DataLayer.VALID_MANUAL_REASONS, default="NO_BIOMETRIC_CONSENT",
                        help="Reason category for manual non-biometric entry.")
    parser.add_argument("--notes", default="", help="Optional notes or reference.")
    parser.add_argument("--correct", type=int, help="Record ID to correct.")
    parser.add_argument("--correct-reason", help="Explanation for attendance correction.")
    parser.add_argument("--list", action="store_true", help="List attendance records for today.")
    parser.add_argument("--date", help="Date to list records for (YYYY-MM-DD).")
    parser.add_argument("--user", default="teacher1", help="Authorized teacher or admin username.")
    parser.add_argument("--password", help="Staff password.")

    args = parser.parse_args()
    db = DataLayer()

    if args.list:
        list_attendance(db, args.date)
        return

    session = authenticate_staff(db, args.user, args.password)
    if not session:
        sys.exit(1)

    # 1. Attendance Correction Workflow
    if args.correct:
        if not args.correct_reason:
            print("[Error] An explicit reason is required when correcting attendance records.")
            sys.exit(1)
        ok = db.correct_attendance(
            record_id=args.correct,
            new_status=args.status,
            reason=args.correct_reason,
            acting_user=session["username"],
            notes=args.notes
        )
        if ok:
            print(f"[Success] Attendance record #{args.correct} corrected to '{args.status}' by {session['username']}.")
        else:
            print(f"[Error] Failed to correct attendance record #{args.correct}.")
            sys.exit(1)
        return

    # 2. Manual Attendance Entry Workflow
    if args.id:
        profile = db.get_registered_student(args.id)
        if not profile:
            print(f"[Warning] Student ID '{args.id}' is not in registered student directory, but manual record can still be logged.")
            student_name = args.id
            canonical_id = args.id
        else:
            student_name = profile["name"]
            canonical_id = profile["student_id"]

        # Prevent duplicate check-in for the same day
        if db.has_checked_in_today(canonical_id):
            print(f"[Skip] {student_name} ({canonical_id}) has already checked in today.")
            sys.exit(0)

        ok = db.log_manual_attendance(
            student_id=canonical_id,
            status=args.status,
            reason_category=args.reason,
            recorded_by=session["username"],
            notes=args.notes
        )
        if ok:
            print(f"[Success] Manual attendance recorded for {student_name} ({canonical_id}):")
            print(f"  - Status: {args.status}")
            print(f"  - Method: MANUAL (Non-Biometric)")
            print(f"  - Reason: {args.reason}")
            print(f"  - Recorded By: {session['username']}")
        else:
            print(f"[Error] Failed to record manual attendance for {canonical_id}.")
            sys.exit(1)
    else:
        list_attendance(db)
        print("Usage: python manual_attendance.py --id <student_id> --status <Present|Late> --reason <REASON> --user <teacher1>")

if __name__ == "__main__":
    main()
