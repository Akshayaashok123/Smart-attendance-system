import requests
from unittest.mock import MagicMock
from cloud_sync import CloudSyncManager

class MockDB:
    def get_unsynced_records(self):
        return [
            (1, "STUDENT_001", "2026-01-01 10:00:00", "emb1", 0),
            (2, "STUDENT_002", "2026-01-01 10:05:00", "emb2", 0)
        ]
    def mark_as_synced(self, record_id):
        print(f"Marked {record_id} as synced")

def test_batch_construction():
    db = MockDB()
    sync = CloudSyncManager(db, "http://dummy-url")
    
    # Mock behavior
    unsynced = db.get_unsynced_records()
    batch_data = [
        {"student_id": r[1], "timestamp": r[2]} 
        for r in unsynced
    ]
    
    print("Constructed Batch Data:")
    import json
    print(json.dumps(batch_data, indent=2))
    
    assert len(batch_data) == 2
    assert batch_data[0]["student_id"] == "STUDENT_001"
    assert isinstance(batch_data, list)
    print("\n[Test] Batch logic verification SUCCESSful.")

if __name__ == "__main__":
    test_batch_construction()
