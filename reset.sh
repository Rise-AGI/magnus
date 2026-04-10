#!/bin/bash
# Magnus Reset Script
# 清理所有数据并重启服务器

set -e

echo "=== Magnus Reset Script ==="
echo ""

# 1. 停止服务
echo "[1/8] Stopping services..."
pkill -f "uvicorn" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
pkill -f "node.*next" 2>/dev/null || true
sleep 2
echo "      Services stopped."
echo ""

# 2. 清理数据库
echo "[2/8] Cleaning database..."
rm -f ~/magnus/back_end/magnus.db
echo "      Database removed."
echo ""

# 3. 清理工作目录
echo "[3/8] Cleaning workspace..."
rm -rf ~/.magnus/workspace/jobs/*
echo "      Workspace cleaned."
echo ""

# 4. 清理共享文件夹
echo "[4/8] Cleaning shared files..."
rm -rf ~/.magnus/sharedfile/* 2>/dev/null || true
rm -rf ~/.magnus/archived_sharedfile/* 2>/dev/null || true
rm -rf /data/sharedfile/* 2>/dev/null || true
rm -rf /data/archived_sharedfile/* 2>/dev/null || true
echo "      Shared files cleaned."
echo ""

# 5. 清理日志
echo "[5/8] Cleaning logs..."
rm -f ~/.magnus/backend.log
rm -f ~/.magnus/frontend.log
echo "      Logs removed."
echo ""

# 6. 清理前端缓存
echo "[6/8] Cleaning frontend cache..."
rm -rf ~/magnus/front_end/.next 2>/dev/null || true
echo "      Frontend cache cleaned."
echo ""

# 7. 启动后端
echo "[7/8] Starting backend..."
cd ~/magnus/back_end
uv run -m server.main > ~/.magnus/backend.log 2>&1 &
BACKEND_PID=$!
echo "      Backend started (PID: $BACKEND_PID)"
echo ""

# 等待后端启动
echo "      Waiting for backend to initialize..."
sleep 5
echo ""

# 8. 创建新用户
echo "[8/8] Creating user..."
cd ~/magnus/back_end
uv run python create_user.py
echo ""

# 输出形如：sk-N2jXo1nc-HsBCI9n5hDUKjh_L9IdJbVw

echo "=== Reset Complete ==="
echo ""
echo "Backend running at: http://127.0.0.1:8017"
echo "Frontend: cd ~/magnus/front_end && npm run dev"
echo ""
echo "Check logs: tail -f ~/.magnus/backend.log"