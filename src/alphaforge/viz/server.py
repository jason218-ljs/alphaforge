"""Flask 服务器：提供仪表盘页面与 JSON API。"""

from __future__ import annotations

from pathlib import Path
from threading import Thread
from typing import Optional

from alphaforge.config import VizConfig
from alphaforge.logger import get_logger

logger = get_logger("viz.server")

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class DashServer:
    """仪表盘服务器封装。"""

    def __init__(self, output_dir: Path | str = "./output", config: Optional[VizConfig] = None):
        self.output_dir = Path(output_dir)
        config = config or VizConfig()
        try:
            from flask import Flask
        except ImportError:
            raise RuntimeError("缺少 flask，请运行 pip install flask")
        self.app = Flask(__name__,
                         template_folder=str(_TEMPLATES_DIR),
                         static_folder=str(_TEMPLATES_DIR),
                         static_url_path="/static")
        self.host = config.host
        self.port = config.port
        self.debug = config.debug
        self._thread: Optional[Thread] = None

        # 注册 API
        from alphaforge.viz.api import api, init_api
        init_api(self.output_dir)
        self.app.register_blueprint(api)

        @self.app.route("/")
        def index():
            return self.app.send_static_file("dashboard.html")

        @self.app.route("/attribution")
        def attribution_page():
            return self.app.send_static_file("attribution.html")

        @self.app.route("/backtest")
        def backtest_page():
            return self.app.send_static_file("backtest.html")

        @self.app.route("/screener")
        def screener_page():
            return self.app.send_static_file("screener.html")

        @self.app.route("/scoring")
        def scoring_page():
            return self.app.send_static_file("scoring.html")

    def run(self, debug: Optional[bool] = None) -> None:
        """阻塞启动服务器。"""
        self.app.run(host=self.host, port=self.port,
                     debug=self.debug if debug is None else debug)

    def run_background(self) -> str:
        """后台启动，返回 URL。"""
        import threading, random
        if self._thread and self._thread.is_alive():
            return f"http://{self.host}:{self.port}"
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None


def create_app(output_dir: Path | str = "./output",
               config: Optional[VizConfig] = None) -> "DashServer":
    """工厂函数创建 DashServer。"""
    return DashServer(output_dir, config)