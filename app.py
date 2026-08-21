#!/usr/bin/env python3
"""cs2-dashboard."""
from flask import Flask, jsonify, render_template

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    return jsonify({"ok": True, "service": "cs2-dashboard"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001, threaded=True)
