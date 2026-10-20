import os

from config import DEEPSEEK_API_KEY
from flask import Flask, jsonify
from flask_cors import CORS
from routes.api import api
from werkzeug.exceptions import HTTPException


def create_app():
    app = Flask(__name__)

    CORS(
        app,
        resources={
            r"/api/*": {
                "origins": "*",
                "methods": ["GET", "POST", "OPTIONS"],
                "allow_headers": ["Content-Type", "Authorization"],
            }
        },
    )
    app.register_blueprint(api)

    @app.errorhandler(Exception)
    def handle_exception(error):
        if isinstance(error, HTTPException):
            return error
        app.logger.exception("未处理的后端异常", exc_info=error)
        return jsonify(
            {
                "error": "服务器内部错误，请查看后端日志",
                "valid": False,
                "reason": "裁判服务暂时不可用，请稍后重试",
            }
        ), 500

    return app

app = create_app()

if __name__ == "__main__":
    if not DEEPSEEK_API_KEY:
        print("⚠️  警告：DEEPSEEK_API_KEY 环境变量未设置！")
        raise SystemExit(1)

    debug_mode = os.environ.get("FLASK_DEBUG") == "1"
    port = int(os.environ.get("PORT", "5001"))
    print(f"🚀 耳辩 AI 裁判服务已启动 → http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug_mode, use_reloader=debug_mode)