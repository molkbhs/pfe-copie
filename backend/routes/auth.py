from flask import Blueprint, jsonify, request


def create_auth_blueprint(deps):
    auth_bp = Blueprint("auth", __name__)

    @auth_bp.route("/api/register", methods=["POST"])
    @deps["limiter"].limit(deps["AUTH_RATE_LIMIT"])
    def register():
        d = request.get_json() or {}
        firstname = d.get("firstname", "").strip()
        lastname = d.get("lastname", "").strip()
        email = d.get("email", "").strip().lower()
        password = d.get("password", "")

        if not all([firstname, lastname, email, password]):
            return jsonify({"error": "Tous les champs sont requis"}), 400
        if not deps["is_strong_password"](password):
            return jsonify({"error": "Le mot de passe doit contenir au moins 8 caractères, avec majuscule, minuscule, chiffre et symbole"}), 400
        if deps["run_query"]("SELECT id FROM users WHERE email=%s", (email,), True):
            return jsonify({"error": "Email déjà utilisé"}), 409

        user_id = deps["run_update"](
            "INSERT INTO users (firstname,lastname,email,password,role,created_at) VALUES (%s,%s,%s,%s,'user',NOW())",
            (firstname, lastname, email, deps["generate_password_hash"](password)),
        )
        return jsonify({"message": "Inscription réussie", "user": {"id": user_id, "firstname": firstname, "lastname": lastname, "email": email}}), 201

    @auth_bp.route("/api/login", methods=["POST"])
    @deps["limiter"].limit(deps["AUTH_RATE_LIMIT"])
    def login():
        d = request.get_json() or {}
        email = d.get("email", "").strip().lower()
        password = d.get("password", "")

        if not email or not password:
            deps["save_login_history"](None, email, "failed", "email", "Email ou mot de passe manquant")
            deps["log_audit"](None, "connexion", "auth_email", "failed", {"email": email, "reason": "Email ou mot de passe manquant"})
            return jsonify({"error": "Email et mot de passe requis"}), 400

        user = deps["run_query"](
            "SELECT id,firstname,lastname,email,password,COALESCE(role,'user') AS role,COALESCE(login_type,'email') AS login_type FROM users WHERE email=%s",
            (email,), True,
        )
        if user and str(user.get("login_type") or "email").strip().lower() == "google":
            reason = "Connexion Google desactivee"
            deps["save_login_history"](None, email, "failed", "email", reason)
            deps["log_audit"](None, "connexion", "auth_email", "failed", {"email": email, "reason": reason})
            return jsonify({"error": "Connexion Google désactivée. Veuillez réinitialiser votre mot de passe."}), 403

        if not user or not deps["check_password_hash"](user["password"], password):
            deps["save_login_history"](None, email, "failed", "email", "Identifiants invalides")
            deps["log_audit"](None, "connexion", "auth_email", "failed", {"email": email, "reason": "Identifiants invalides"})
            return jsonify({"error": "Email ou mot de passe incorrect"}), 401

        user_data = {"id": user["id"], "username": email.split("@")[0], "firstname": user["firstname"], "lastname": user["lastname"], "email": user["email"], "role": user.get("role", "user")}
        access_token = deps["create_access_token"](user_data)
        user_data["token"] = access_token
        deps["save_login_history"](user["id"], user["email"], "success", "email")
        deps["log_audit"](user["id"], "connexion", "auth_email", "success", {"email": user["email"]})
        return jsonify({"message": "Connexion réussie", "user": user_data, "token": access_token})

    @auth_bp.route("/api/profile", methods=["GET"])
    @deps["jwt_required"]
    def get_my_profile():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"error": "Non autorisé"}), 401
        user = deps["run_query"]("SELECT id,firstname,email,role FROM users WHERE id=%s", (user_id,), True)
        if not user:
            return jsonify({"error": "User non trouvé"}), 404
        return jsonify({"status": "success", "data": {"id": user["id"], "username": user["firstname"], "email": user["email"], "role": user["role"]}})

    @auth_bp.route("/api/profile/<int:user_id>", methods=["GET"])
    @deps["jwt_required"]
    def get_profile(user_id):
        user = deps["run_query"](
            "SELECT id,firstname,lastname,email,created_at,COALESCE(role,'user') AS role,COALESCE(login_type,'email') AS login_type FROM users WHERE id=%s",
            (user_id,), True,
        )
        if not user:
            return jsonify({"error": "Utilisateur non trouvé"}), 404
        if user.get("created_at"):
            user["created_at"] = user["created_at"].strftime("%Y-%m-%d %H:%M")
        return jsonify(user)

    @auth_bp.route("/api/profile/<int:user_id>", methods=["PUT"])
    @deps["jwt_required"]
    def update_profile(user_id):
        actor_id = deps["get_current_user"]()
        d = request.get_json() or {}
        firstname = (d.get("firstname") or "").strip()
        lastname = (d.get("lastname") or "").strip()
        email = (d.get("email") or "").strip().lower()

        if not all([firstname, lastname, email]):
            deps["log_audit"](actor_id, "modification_utilisateur", f"user:{user_id}", "failed", "Champs requis manquants")
            return jsonify({"error": "Tous les champs sont requis"}), 400
        if not deps["run_query"]("SELECT id FROM users WHERE id=%s", (user_id,), True):
            deps["log_audit"](actor_id, "modification_utilisateur", f"user:{user_id}", "failed", "Utilisateur non trouvé")
            return jsonify({"error": "Utilisateur non trouvé"}), 404
        owner = deps["run_query"]("SELECT id FROM users WHERE LOWER(TRIM(email))=%s", (email,), True)
        if owner and int(owner["id"]) != user_id:
            deps["log_audit"](actor_id, "modification_utilisateur", f"user:{user_id}", "failed", {"email": email, "reason": "Email déjà utilisé"})
            return jsonify({"error": "Cet email est déjà utilisé"}), 409

        try:
            deps["run_update"]("UPDATE users SET firstname=%s,lastname=%s,email=%s WHERE id=%s", (firstname, lastname, email, user_id))
            updated = deps["run_query"](
                "SELECT id,firstname,lastname,email,created_at,COALESCE(role,'user') AS role,COALESCE(login_type,'email') AS login_type FROM users WHERE id=%s",
                (user_id,), True,
            )
            deps["log_audit"](actor_id, "modification_utilisateur", f"user:{user_id}", "success", {"email": email})
            if updated and updated.get("created_at"):
                updated["created_at"] = updated["created_at"].strftime("%Y-%m-%d %H:%M")
            return jsonify({"message": "Profil mis à jour", "user": updated})
        except Exception as e:
            deps["log_audit"](actor_id, "modification_utilisateur", f"user:{user_id}", "failed", str(e))
            return deps["internal_error_response"]("Impossible de mettre à jour l'utilisateur.")

    @auth_bp.route("/api/profile/<int:user_id>/password", methods=["PUT"])
    @deps["jwt_required"]
    def update_password(user_id):
        d = request.get_json() or {}
        current_pwd = d.get("current_password") or ""
        new_pwd = d.get("new_password") or ""

        if not current_pwd or not new_pwd:
            return jsonify({"error": "Les deux mots de passe sont requis"}), 400
        if not deps["is_strong_password"](new_pwd):
            return jsonify({"error": "Le nouveau mot de passe doit contenir au moins 8 caractères, avec majuscule, minuscule, chiffre et symbole"}), 400

        user = deps["run_query"]("SELECT id,password FROM users WHERE id=%s", (user_id,), True)
        if not user:
            return jsonify({"error": "Utilisateur non trouvé"}), 404
        if not deps["check_password_hash"](user["password"], current_pwd):
            return jsonify({"error": "Mot de passe actuel incorrect"}), 401

        try:
            deps["run_update"]("UPDATE users SET password=%s WHERE id=%s", (deps["generate_password_hash"](new_pwd), user_id))
            return jsonify({"message": "Mot de passe mis à jour"})
        except Exception:
            return deps["internal_error_response"]("Impossible de mettre à jour le mot de passe.")

    @auth_bp.route("/api/auth/reset-password", methods=["POST"])
    @deps["limiter"].limit(deps["AUTH_RATE_LIMIT"])
    def reset_password():
        d = request.get_json() or {}
        token = (d.get("token") or "").strip()
        email = (d.get("email") or "").strip().lower()
        current_password = d.get("current_password") or ""
        new_password = d.get("new_password") or ""

        if not new_password:
            return jsonify({"error": "Le nouveau mot de passe est requis"}), 400
        if not deps["is_strong_password"](new_password):
            return jsonify({"error": "Le mot de passe doit contenir au moins 8 caractères, avec majuscule, minuscule, chiffre et symbole"}), 400

        if token:
            # Token-based reset is not implemented in this version.
            return jsonify({"error": "Lien de réinitialisation invalide ou expiré"}), 400

        if not email:
            return jsonify({"error": "Adresse email requise"}), 400
        if not current_password:
            return jsonify({"error": "Le mot de passe actuel est requis"}), 400

        user = deps["run_query"]("SELECT id,password FROM users WHERE email=%s", (email,), True)
        if not user:
            return jsonify({"error": "Compte non trouvé"}), 404
        if not deps["check_password_hash"](user["password"], current_password):
            return jsonify({"error": "Mot de passe actuel incorrect"}), 401

        try:
            deps["run_update"]("UPDATE users SET password=%s WHERE id=%s", (deps["generate_password_hash"](new_password), user["id"]))
            deps["log_audit"](user["id"], "reset_password", "auth_reset_password", "success", {"email": email})
            return jsonify({"message": "Mot de passe mis à jour"})
        except Exception as e:
            deps["log_audit"](user["id"], "reset_password", "auth_reset_password", "failed", str(e))
            return deps["internal_error_response"]("Impossible de mettre à jour le mot de passe.")

    @auth_bp.route("/api/profile/pending", methods=["POST"])
    @deps["jwt_required"]
    def pending_profile_action():
        d = request.get_json() or {}
        user_id = d.get("user_id")
        current_pwd = d.get("current_password") or ""
        new_pwd = d.get("new_password") or ""

        if (d.get("type") or "").strip().lower() != "password":
            return jsonify({"error": "Type d'action invalide"}), 400
        if not all([user_id, current_pwd, new_pwd]):
            return jsonify({"error": "Données manquantes"}), 400

        user = deps["run_query"]("SELECT id,email,password FROM users WHERE id=%s", (user_id,), True)
        if not user:
            return jsonify({"error": "Utilisateur non trouvé"}), 404
        if not deps["check_password_hash"](user["password"], current_pwd):
            return jsonify({"error": "Mot de passe actuel incorrect"}), 401

        return jsonify({"message": "Demande créée", "token": deps["secrets"].token_urlsafe(16)})

    @auth_bp.route("/api/profile/pending/cancel", methods=["POST"])
    @deps["jwt_required"]
    def cancel_pending_action():
        token = (request.get_json() or {}).get("token")
        if not token:
            return jsonify({"error": "Token manquant"}), 400
        return jsonify({"message": "Demande annulée"})

    @auth_bp.route("/api/profile/<int:user_id>", methods=["DELETE"])
    @deps["jwt_required"]
    def delete_account(user_id):
        actor_id = deps["get_current_user"]()
        try:
            target = deps["run_query"]("SELECT id,email,firstname,lastname FROM users WHERE id=%s", (user_id,), True)
            deps["run_update"]("DELETE FROM users WHERE id=%s", (user_id,))
            deps["log_audit"](
                actor_id,
                "suppression_utilisateur",
                f"user:{user_id}",
                "success",
                {"email": (target or {}).get("email"), "name": f"{(target or {}).get('firstname','')} {(target or {}).get('lastname','')}".strip()},
            )
            return jsonify({"message": "Compte supprimé"})
        except Exception as e:
            deps["log_audit"](actor_id, "suppression_utilisateur", f"user:{user_id}", "failed", str(e))
            return deps["internal_error_response"]("Impossible de supprimer le compte.")

    return auth_bp
