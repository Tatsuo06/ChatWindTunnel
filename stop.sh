#!/bin/bash
# Port-scoped so we never kill the sibling ChatTowingTank (which runs the same
# `uvicorn backend.main:app` / `streamlit run frontend/app.py` on ports 8001/8502).
pkill -f "uvicorn backend.main:app.*--port 8000" 2>/dev/null || true
pkill -f "streamlit run frontend/app.py.*--server.port 8501" 2>/dev/null || true
echo "Stopped."
