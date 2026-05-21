#!/bin/bash
# ChatWindTunnel 起動スクリプト

set -e
cd "$(dirname "$0")"

BACKEND_PORT=8000
FRONTEND_PORT=8501
BACKEND_LOG=/tmp/chatwt_backend.log
FRONTEND_LOG=/tmp/chatwt_frontend.log

# 既存プロセスを停止
pkill -f "uvicorn backend.main:app" 2>/dev/null && echo "既存バックエンドを停止" || true
pkill -f "streamlit run frontend/app.py" 2>/dev/null && echo "既存フロントエンドを停止" || true
sleep 1

# バックエンド起動
echo "バックエンド起動中 (port $BACKEND_PORT)..."
uv run uvicorn backend.main:app --port $BACKEND_PORT > "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

# ヘルスチェック
for i in $(seq 1 20); do
    if curl -sf "http://localhost:$BACKEND_PORT/health" > /dev/null 2>&1; then
        echo "バックエンド起動完了 (PID $BACKEND_PID)"
        break
    fi
    sleep 0.5
done

# フロントエンド起動
echo "フロントエンド起動中 (port $FRONTEND_PORT)..."
STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
uv run streamlit run frontend/app.py --server.port $FRONTEND_PORT --server.headless true > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
sleep 2

echo ""
echo "====================================="
echo "  Chat Wind Tunnel 起動完了"
echo "====================================="
echo "  フロントエンド: http://localhost:$FRONTEND_PORT"
echo "  バックエンド:   http://localhost:$BACKEND_PORT"
echo "  バックエンドログ: $BACKEND_LOG"
echo "  フロントエンドログ: $FRONTEND_LOG"
echo "====================================="
echo "  停止するには: ./stop.sh"
echo "====================================="

# PIDを保存
echo "$BACKEND_PID" > /tmp/chatwt_backend.pid
echo "$FRONTEND_PID" > /tmp/chatwt_frontend.pid
