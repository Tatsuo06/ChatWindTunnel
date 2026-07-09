#!/bin/bash
# ChatWindTunnel 停止スクリプト

# Port-scoped so we never kill the sibling ChatTowingTank (same commands on 8001/8502).
pkill -f "uvicorn backend.main:app.*--port 8000" 2>/dev/null && echo "バックエンド停止" || echo "バックエンドは起動していません"
pkill -f "streamlit run frontend/app.py.*--server.port 8501" 2>/dev/null && echo "フロントエンド停止" || echo "フロントエンドは起動していません"
rm -f /tmp/chatwt_backend.pid /tmp/chatwt_frontend.pid
