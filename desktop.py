import webview
import threading
import time
from app import app  # 导入 Flask 应用

def start_flask():
    app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)

if __name__ == '__main__':
    # 后台启动 Flask
    threading.Thread(target=start_flask, daemon=True).start()
    # 等 Flask 起来
    time.sleep(2)
    # 创建桌面窗口
    webview.create_window(
        '耳辩 · 渡口辩论棋',
        'http://127.0.0.1:5001/',
        width=1200,
        height=850,
        resizable=True
    )
    webview.start()