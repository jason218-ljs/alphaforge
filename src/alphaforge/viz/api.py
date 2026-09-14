"""JSON API 端点：封装 ChartData 为 Flask 蓝图。"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from alphaforge.viz.charts import ChartData

api = Blueprint("api", __name__)

_chart_data: ChartData | None = None


def init_api(output_dir: Path | str) -> None:
    """初始化全局 ChartData。"""
    global _chart_data
    _chart_data = ChartData(output_dir)


def _data() -> ChartData:
    if _chart_data is None:
        raise RuntimeError("API 未初始化，请先调用 init_api")
    return _chart_data


@api.route("/api/dashboard")
def dashboard():
    date = request.args.get("date")
    return jsonify(_data().dashboard(date))


@api.route("/api/dates")
def dates():
    rec = _data()._find_records()
    if rec:
        return jsonify({"dates": _data()._available_dates_from_records(rec)})
    bt = _data()._find_backtest()
    return jsonify({"dates": _data()._available_dates(bt)})


@api.route("/api/attribution")
def attribution():
    return jsonify(_data().attribution())


@api.route("/api/backtest")
def backtest():
    return jsonify(_data().backtest_detail())


@api.route("/api/screener")
def screener():
    return jsonify(_data().screener())


@api.route("/api/scoring")
def scoring():
    return jsonify(_data().scoring())


@api.route("/api/health")
def health():
    return jsonify({"status": "ok"})