from flask import Blueprint, jsonify


def create_debug_blueprint(admin_required, create_tables):
    debug_bp = Blueprint("debug", __name__)

    @debug_bp.route("/api/init-tables", methods=["POST"])
    @admin_required
    def init_tables():
        try:
            create_tables()
            return jsonify({"success": True, "message": "Tables créées ou déjà existantes"})
        except Exception:
            return jsonify({"success": False, "error": "Impossible d'initialiser les tables"}), 500

    return debug_bp
