# Smart Attendance System

A Python-based attendance tracking application that uses facial recognition and QR code generation to record student attendance.

## Features
- Real‑time face recognition for check‑in
- QR‑code generation for alternative check‑in method
- Secure storage of attendance records in SQLite
- Cloud synchronization support

## Setup
1. Clone the repository.
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in the required configuration values.
4. Run the application:
   ```bash
   python main.py
   ```

## License
MIT License
