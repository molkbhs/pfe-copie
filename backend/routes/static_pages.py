from flask import Blueprint, send_from_directory


def create_static_blueprint(frontend_path):
    static_bp = Blueprint("static_pages", __name__)

    @static_bp.route("/")
    def home():
        return send_from_directory(frontend_path, "index.html")

    @static_bp.route("/<path:filename>")
    def serve_static(filename):
        return send_from_directory(frontend_path, filename)

    return static_bp
