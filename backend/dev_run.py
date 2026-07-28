import os
import sys

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault(
    "DB_PATH",
    os.path.join(
        r"C:\Users\tomek\AppData\Local\Temp\claude\C--Users-tomek-repos\12367c1e-b9ce-4559-81eb-a3743be8d7cb\scratchpad",
        "running_test.db",
    ),
)
os.environ.setdefault(
    "GARMIN_TOKEN_DIR",
    os.path.join(
        r"C:\Users\tomek\AppData\Local\Temp\claude\C--Users-tomek-repos\12367c1e-b9ce-4559-81eb-a3743be8d7cb\scratchpad",
        "garmin_tokens",
    ),
)
os.environ.setdefault("FRONTEND_DIR", os.path.join(BACKEND_DIR, "..", "frontend"))
os.environ.setdefault("SYNC_INTERVAL_HOURS", "0")  # dev: manual sync only, no background job

import uvicorn

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8712)
