# 开发环境启动器：从本文件夹启动耳辩后端（5001，局域网可访问）
# 用法：python start_dev.py
from app import app

if __name__ == "__main__":
    print("🚀 耳辩服务已启动：http://127.0.0.1:5001/game")
    app.run(host="0.0.0.0", port=5001, debug=False)
