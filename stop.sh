#!/bin/bash
# ChatWindTunnel 停止スクリプト

pkill -f "uvicorn backend.main:app" 2>/dev/null && echo "バックエンド停止" || echo "バックエンドは起動していません"
pkill -f "streamlit run frontend/app.py" 2>/dev/null && echo "フロントエンド停止" || echo "フロントエンドは起動していません"
rm -f /tmp/chatwt_backend.pid /tmp/chatwt_frontend.pid
