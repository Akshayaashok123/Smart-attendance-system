import requests
import time
import socket
import threading

class CloudSyncManager:
    """Manages background synchronization to Google Sheets via Google Apps Script."""
    
    def __init__(self, db_layer, script_url, sync_interval=1800):
        self.db = db_layer
        self.script_url = script_url
        self.sync_interval = sync_interval
        self.running = False
        self.thread = None

    def is_connected(self):
        """Checks for internet connection by pinging Google's DNS."""
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            return True
        except OSError:
            return False

    def sync_records(self):
        """Pushes unsynced records from SQLite to Google Sheets in a single batch."""
        if not self.is_connected():
            print("[Sync] Offline. Waiting for connectivity...")
            return

        unsynced = self.db.get_unsynced_records()
        if not unsynced:
            print("[Sync] Data is up to date.")
            return

        # Construct the batch payload using the explicit columns
        batch_data = [
            {
                "student_id": r[1],
                "timestamp": r[2],
                "status": r[3] if r[3] in ["Present", "Late"] else "Present"
            }
            for r in unsynced
        ]
        
        try:
            # Send everything in ONE request instead of a loop
            response = requests.post(self.script_url, json=batch_data, timeout=15)
            if response.status_code in (200, 201) or "Success" in response.text:
                for record in unsynced:
                    self.db.mark_as_synced(record[0])
                print(f"[Sync] Successfully synced batch of {len(batch_data)}.")
            else:
                print(f"[Sync] Server error during batch sync: {response.status_code}")
        except Exception as e:
            print(f"[Sync] Batch failed: {e}")

    def _sync_loop(self):
        while self.running:
            self.sync_records()
            time.sleep(self.sync_interval)

    def start_background_sync(self):
        if not self.thread or not self.thread.is_alive():
            self.running = True
            self.thread = threading.Thread(target=self._sync_loop, daemon=True)
            self.thread.start()
            print(f"Background sync started (Interval: {self.sync_interval}s)")

    def stop_background_sync(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

# --- GOOGLE APPS SCRIPT CODE (MASTER REGISTRY VERSION) ---
# function doPost(e) {
#   var ss = SpreadsheetApp.getActiveSpreadsheet();
#   var attendanceSheet = ss.getSheets()[0]; // Log to first tab
#   var studentSheet = ss.getSheetByName("Students"); // Your ID->Name list
#   
#   var payload = JSON.parse(e.postData.contents);
#   var records = Array.isArray(payload) ? payload : [payload];
#   var timestamp = new Date();
#   
#   // 1. Map ID to Name for quick lookup
#   var nameMap = {};
#   if (studentSheet) {
#     var studentData = studentSheet.getDataRange().getValues();
#     for (var i = 1; i < studentData.length; i++) { // Skip header
#       nameMap[studentData[i][0]] = studentData[i][1];
#     }
#   }
#   
#   // 2. Log each record
#   records.forEach(function(record) {
#     var studentName = nameMap[record.student_id] || "Unknown Student";
#     attendanceSheet.appendRow([timestamp, record.student_id, studentName, record.timestamp, "Verified (Batch)"]);
#   });
#   
#   return ContentService.createTextOutput("Success").setMimeType(ContentService.MimeType.TEXT);
# }
# --------------------------------------------------------

if __name__ == "__main__":
    from data_layer import DataLayer
    db = DataLayer("attendance.db")
    sync = CloudSyncManager(db, "YOUR_APPS_SCRIPT_WEB_APP_URL")
    sync.sync_records() # Run manual sync
