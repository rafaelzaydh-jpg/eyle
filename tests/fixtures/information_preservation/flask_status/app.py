import os
from flask import Flask

app = Flask(__name__)


@app.route("/")
def index():
    return {"status": "ok"}


@app.route("/health")
def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
