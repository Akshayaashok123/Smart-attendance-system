import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

class EmailNotifier:
    """Handles zero-cost automated email alerts via SMTP."""
    
    def __init__(self, smtp_server="smtp.gmail.com", smtp_port=587, sender_email=None, sender_password=None):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender_email = sender_email
        self.sender_password = sender_password

    def send_alert(self, receiver_email, student_id):
        """Sends an attendance check-in email alert."""
        if not self.sender_email or not self.sender_password:
            print(f"[Offline Alert] Attendance logged for {student_id}. (Email not configured)")
            return False

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        subject = f"Attendance Alert: {student_id}"
        body = f"Hello, \n\nThis is an automated alert to inform you that student {student_id} has successfully checked in at the smart attendance terminal.\n\nTime: {now_str}"
        
        msg = MIMEMultipart()
        msg['From'] = self.sender_email
        msg['To'] = receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.sender_email, self.sender_password)
            server.send_message(msg)
            server.quit()
            print(f"Email alert sent for {student_id} to {receiver_email}")
            return True
        except Exception as e:
            print(f"Failed to send email alert: {e}")
            return False

def time_str():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

if __name__ == "__main__":
    # Test (will fail if no credentials provided)
    notifier = EmailNotifier(sender_email="your_email@gmail.com", sender_password="your_app_password")
    # notifier.send_alert("parent_email@gmail.com", "STUDENT_123")
