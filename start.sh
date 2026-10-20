#!/bin/bash
# 耳辩 云端一键启动脚本
# 用法：bash start.sh          默认 5001 端口
#      sudo PORT=80 bash start.sh   用 80 端口（访问地址不带端口号）

set -e
cd "$(dirname "$0")"

PORT="${PORT:-5001}"

if [ ! -d "venv" ]; then
  echo "❌ 没找到 venv，请先执行："
  echo "   python3 -m venv venv && source venv/bin/activate && pip install -r requirements-server.txt -i https://mirrors.aliyun.com/pypi/simple/"
  exit 1
fi

if [ ! -f ".env" ]; then
  echo "❌ 没找到 .env，请先创建并写入：DEEPSEEK_API_KEY=sk-xxx"
  exit 1
fi

if [ "$PORT" -lt 1024 ] && [ "$(id -u)" -ne 0 ]; then
  echo "❌ 端口 $PORT 需要 root 权限，请用：sudo PORT=$PORT bash start.sh"
  exit 1
fi

source venv/bin/activate
export FLASK_DEBUG=0
export PORT

# 结束旧进程：优先按端口定位（最可靠），再兜底按命令行匹配
# 注意：venv 里的进程常显示为 python3 app.py，用 "python app.py" 匹配会漏掉
OLD=$(ss -lntp 2>/dev/null | grep -E ":${PORT}[[:space:]]" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2 || true)
if [ -z "$OLD" ]; then
  OLD=$(pgrep -f "[p]ython3? .*app\.py" | head -5 || true)
fi
if [ -n "$OLD" ]; then
  echo "⚠️  发现旧进程 $OLD，正在关闭…"
  kill $OLD 2>/dev/null || true
  sleep 2
  if kill -0 $OLD 2>/dev/null; then
    echo "   进程未退出，强制结束"
    kill -9 $OLD 2>/dev/null || true
    sleep 1
  fi
fi

nohup python app.py > server.log 2>&1 &
echo "🚀 已后台启动（端口 $PORT），日志：server.log"
sleep 3

echo "---- 健康检查 ----"
curl -s --max-time 5 "http://127.0.0.1:${PORT}/api/health" || echo "（没响应，请执行 tail -30 server.log 查看）"
echo ""
echo "---- 端口监听 ----"
ss -lntp 2>/dev/null | grep ":${PORT}" || echo "端口 ${PORT} 未监听"
