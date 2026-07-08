from flask import Blueprint, jsonify, request


def create_contact_blueprint(limiter, contact_rate_limit):
    contact_bp = Blueprint("contact", __name__)

    @contact_bp.route("/api/contact", methods=["POST"])
    @limiter.limit(contact_rate_limit)
    def contact():
        payload = request.get_json() or {}
        if not all((payload.get("name") or "").strip(), (payload.get("email") or "").strip(),
                   (payload.get("subject") or "").strip(), (payload.get("message") or "").strip()):
            return jsonify({"success": False, "error": "Tous les champs sont requis"}), 400
        return jsonify({"success": True, "message": "Message envoyé avec succès"}), 201

    return contact_bp
