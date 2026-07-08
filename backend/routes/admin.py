from datetime import datetime, timedelta
import mimetypes

from flask import Blueprint, Response, jsonify, request, send_from_directory


def create_admin_blueprint(deps):
    admin_bp = Blueprint("admin", __name__)

    @admin_bp.route("/api/users", methods=["GET"])
    @deps["admin_required"]
    def users_list():
        actor_id = deps["get_current_user"]()
        users = deps["run_query"](
            "SELECT id,firstname,lastname,email,created_at,COALESCE(login_type,'email') AS login_type,COALESCE(role,'user') AS role FROM users ORDER BY id DESC"
        )
        for user in users:
            if user.get("created_at"):
                user["created_at"] = user["created_at"].strftime("%Y-%m-%d %H:%M")
        deps["log_audit"](actor_id, "api_access", "users_list", "success", {"rows": len(users)})
        return jsonify(users)

    @admin_bp.route("/api/stats", methods=["GET"])
    @deps["admin_required"]
    def stats():
        try:
            total = deps["run_query"]("SELECT COUNT(*) AS n FROM users", fetch_one=True)["n"]
            today = datetime.now().strftime("%Y-%m-%d")
            today_cnt = deps["run_query"]("SELECT COUNT(*) AS n FROM users WHERE DATE(created_at)=%s", (today,), True)["n"]
            week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            week_cnt = deps["run_query"]("SELECT COUNT(*) AS n FROM users WHERE created_at>=%s", (week_ago,), True)["n"]
            google_cnt = deps["run_query"]("SELECT COUNT(*) AS n FROM users WHERE login_type='google'", fetch_one=True)["n"]
            email_cnt = deps["run_query"]("SELECT COUNT(*) AS n FROM users WHERE login_type='email' OR login_type IS NULL OR login_type=''", fetch_one=True)["n"]
            admin_cnt = deps["run_query"]("SELECT COUNT(*) AS n FROM users WHERE role='admin'", fetch_one=True)["n"]
            return jsonify({"total_users": total, "new_today": today_cnt, "active_week": week_cnt, "google_users": google_cnt, "email_users": email_cnt, "admin_count": admin_cnt})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @admin_bp.route("/api/admin/login-history", methods=["GET"])
    @deps["admin_required"]
    def admin_login_history():
        actor_id = deps["get_current_user"]()
        conn = None
        try:
            conn = deps["get_connection"]()
            cur = conn.cursor()
            deps["ensure_login_history_schema"](cur)
            conn.commit()
            cur.close()
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            if conn:
                conn.close()

        search = (request.args.get("search") or "").strip().lower()
        status = (request.args.get("status") or "").strip().lower()
        page = max(1, deps["_to_int"](request.args.get("page"), 1))
        limit = deps["_to_int"](request.args.get("limit"), 10)
        if limit <= 0:
            limit = 10
        limit = min(limit, 100)
        offset = (page - 1) * limit

        where = []
        params = []
        if search:
            where.append("LOWER(TRIM(lh.email)) LIKE %s")
            params.append(f"%{search}%")
        if status in ("success", "failed"):
            where.append("lh.login_status = %s")
            params.append(status)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        total_row = deps["run_query"](f"SELECT COUNT(*) AS n FROM login_history lh {where_sql}", tuple(params), True)
        total = int((total_row or {}).get("n") or 0)

        items = deps["run_query"](
            f"""
            SELECT
                lh.id,
                lh.user_id,
                COALESCE(u.firstname, '') AS firstname,
                COALESCE(u.lastname, '') AS lastname,
                lh.email,
                lh.login_status,
                COALESCE(lh.login_type, 'email') AS login_type,
                COALESCE(lh.ip_address, '') AS ip_address,
                lh.login_date,
                lh.failure_reason
            FROM login_history lh
            LEFT JOIN users u ON u.id = lh.user_id
            {where_sql}
            ORDER BY lh.login_date DESC, lh.id DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params + [limit, offset]),
        )

        for row in items:
            dt = row.get("login_date")
            if dt and hasattr(dt, "strftime"):
                row["login_date"] = dt.strftime("%Y-%m-%d %H:%M:%S")

        deps["log_audit"](actor_id, "api_access", "admin_login_history", "success", {"page": page, "limit": limit, "search": search, "status": status, "total": total})
        return jsonify({"success": True, "items": items, "total": total, "page": page, "limit": limit})

    @admin_bp.route("/api/admin/audit-logs", methods=["GET"])
    @deps["admin_required"]
    def admin_audit_logs():
        actor_id = deps["get_current_user"]()
        conn = None
        try:
            conn = deps["get_connection"]()
            cur = conn.cursor()
            deps["ensure_audit_logs_schema"](cur)
            conn.commit()
            cur.close()
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            if conn:
                conn.close()

        page = max(1, deps["_to_int"](request.args.get("page"), 1))
        limit = deps["_to_int"](request.args.get("limit"), 10)
        if limit <= 0:
            limit = 10
        limit = min(limit, 100)
        offset = (page - 1) * limit

        search = (request.args.get("search") or "").strip().lower()
        action = (request.args.get("action") or "").strip().lower()
        raw_status = (request.args.get("status") or request.args.get("statut") or "").strip().lower()
        status = deps["_normalize_audit_status"](raw_status)

        allowed_actions = {
            "connexion",
            "creation_utilisateur",
            "modification_utilisateur",
            "suppression_utilisateur",
            "changement_role",
            "import_donnees",
            "generation_rapport",
            "export_rapport",
            "api_access",
            "erreur_systeme",
        }

        where = []
        params = []
        if search:
            where.append(
                """
                (
                    LOWER(COALESCE(al.action, '')) LIKE %s
                    OR LOWER(COALESCE(al.ressource, '')) LIKE %s
                    OR LOWER(COALESCE(al.ip_address, '')) LIKE %s
                    OR LOWER(COALESCE(al.details, '')) LIKE %s
                    OR LOWER(CONCAT(COALESCE(u.firstname, ''), ' ', COALESCE(u.lastname, ''))) LIKE %s
                    OR LOWER(COALESCE(u.email, '')) LIKE %s
                )
                """
            )
            like = f"%{search}%"
            params.extend([like, like, like, like, like, like])

        if action and action in allowed_actions:
            where.append("al.action = %s")
            params.append(action)

        if raw_status in {"success", "succes", "ok", "failed", "failure", "echec", "error"}:
            where.append("al.statut = %s")
            params.append(status)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        total_row = deps["run_query"](
            f"""
            SELECT COUNT(*) AS n
            FROM audit_logs al
            LEFT JOIN users u ON u.id = al.user_id
            {where_sql}
            """,
            tuple(params),
            True,
        )
        total = int((total_row or {}).get("n") or 0)

        items = deps["run_query"](
            f"""
            SELECT
                al.id_log,
                al.action,
                al.ressource,
                al.user_id,
                COALESCE(u.firstname, '') AS firstname,
                COALESCE(u.lastname, '') AS lastname,
                COALESCE(u.email, '') AS email,
                COALESCE(al.statut, 'success') AS statut,
                COALESCE(al.ip_address, '') AS ip_address,
                COALESCE(al.details, '') AS details,
                al.date_action
            FROM audit_logs al
            LEFT JOIN users u ON u.id = al.user_id
            {where_sql}
            ORDER BY al.date_action DESC, al.id_log DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params + [limit, offset]),
        ) or []

        for row in items:
            dt = row.get("date_action")
            if dt and hasattr(dt, "strftime"):
                row["date_action"] = dt.strftime("%Y-%m-%d %H:%M:%S")

        deps["log_audit"](actor_id, "api_access", "admin_audit_logs", "success", {"page": page, "limit": limit, "search": search, "action": action, "status": raw_status, "total": total})
        return jsonify({"success": True, "items": items, "total": total, "page": page, "limit": limit})

    @admin_bp.route("/api/admin/reports-history", methods=["GET"])
    @deps["admin_required"]
    def admin_reports_history():
        actor_id = deps["get_current_user"]()
        conn = None
        try:
            conn = deps["get_connection"]()
            cur = conn.cursor()
            deps["ensure_rapports_schema"](cur)
            conn.commit()
            cur.close()
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            if conn:
                conn.close()

        page = max(1, deps["_to_int"](request.args.get("page"), 1))
        limit = deps["_to_int"](request.args.get("limit"), 10)
        if limit <= 0:
            limit = 10
        limit = min(limit, 100)
        offset = (page - 1) * limit

        search = (request.args.get("search") or "").strip().lower()
        fmt = deps["_normalize_report_format"](request.args.get("format") or "")

        where = []
        params = []
        if search:
            where.append(
                """
                (
                    LOWER(COALESCE(r.nom_rapport, '')) LIKE %s
                    OR LOWER(COALESCE(u.email, '')) LIKE %s
                    OR LOWER(CONCAT(COALESCE(u.firstname, ''), ' ', COALESCE(u.lastname, ''))) LIKE %s
                )
                """
            )
            like = f"%{search}%"
            params.extend([like, like, like])
        if fmt:
            where.append("LOWER(COALESCE(r.format, '')) = %s")
            params.append(fmt)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        total_row = deps["run_query"](
            f"""
            SELECT COUNT(*) AS n
            FROM rapports r
            LEFT JOIN users u ON u.id = r.created_by
            {where_sql}
            """,
            tuple(params),
            True,
        ) or {"n": 0}

        items = deps["run_query"](
            f"""
            SELECT
                r.id_rapport,
                r.nom_rapport,
                COALESCE(r.type_rapport, 'financier') AS type_rapport,
                LOWER(COALESCE(r.format, 'pdf')) AS format,
                r.chemin_fichier,
                r.created_by,
                COALESCE(u.firstname, '') AS firstname,
                COALESCE(u.lastname, '') AS lastname,
                COALESCE(u.email, '') AS email,
                r.date_generation
            FROM rapports r
            LEFT JOIN users u ON u.id = r.created_by
            {where_sql}
            ORDER BY r.date_generation DESC, r.id_rapport DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params + [limit, offset]),
        ) or []

        for row in items:
            dt = row.get("date_generation")
            if dt and hasattr(dt, "strftime"):
                row["date_generation"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            row["download_url"] = f"/api/admin/reports/{row.get('id_rapport')}/download"

        total = int(total_row.get("n") or 0)
        deps["log_audit"](actor_id, "api_access", "admin_reports_history", "success", {"page": page, "limit": limit, "search": search, "format": fmt, "total": total})
        return jsonify({"success": True, "items": items, "total": total, "page": page, "limit": limit})

    @admin_bp.route("/api/admin/import-history", methods=["GET"])
    @deps["admin_required"]
    def admin_import_history():
        actor_id = deps["get_current_user"]()
        conn = None
        try:
            conn = deps["get_connection"]()
            cur = conn.cursor()
            deps["ensure_historique_imports_schema"](cur)
            conn.commit()
            cur.close()
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            if conn:
                conn.close()

        page = max(1, deps["_to_int"](request.args.get("page"), 1))
        limit = deps["_to_int"](request.args.get("limit"), 10)
        if limit <= 0:
            limit = 10
        limit = min(limit, 100)
        offset = (page - 1) * limit

        search = (request.args.get("search") or "").strip().lower()
        status = (request.args.get("status") or "").strip().lower()

        where = []
        params = []
        if search:
            where.append(
                """
                (
                    LOWER(COALESCE(hi.nom_fichier, '')) LIKE %s
                    OR LOWER(COALESCE(hi.importe_par, '')) LIKE %s
                    OR LOWER(COALESCE(u.email, '')) LIKE %s
                    OR LOWER(CONCAT(COALESCE(u.firstname, ''), ' ', COALESCE(u.lastname, ''))) LIKE %s
                )
                """
            )
            like = f"%{search}%"
            params.extend([like, like, like, like])
        if status:
            where.append("LOWER(COALESCE(hi.statut, 'succes')) = %s")
            params.append(status)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        total_row = deps["run_query"](
            f"""
            SELECT COUNT(*) AS n
            FROM historique_imports hi
            LEFT JOIN users u ON u.id = hi.user_id
            {where_sql}
            """,
            tuple(params),
            True,
        ) or {"n": 0}

        items = deps["run_query"](
            f"""
            SELECT
                hi.id,
                hi.nom_fichier,
                hi.nb_lignes,
                hi.nb_erreurs,
                hi.statut,
                hi.date_import,
                COALESCE(hi.importe_par, CONCAT(u.firstname, ' ', u.lastname), 'Utilisateur inconnu') AS importe_par,
                COALESCE(u.email, '') AS email
            FROM historique_imports hi
            LEFT JOIN users u ON u.id = hi.user_id
            {where_sql}
            ORDER BY hi.date_import DESC, hi.id DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params + [limit, offset]),
        ) or []

        for row in items:
            dt = row.get("date_import")
            if dt and hasattr(dt, "strftime"):
                row["date_import"] = dt.strftime("%Y-%m-%d %H:%M:%S")

        total = int(total_row.get("n") or 0)
        deps["log_audit"](actor_id, "api_access", "admin_import_history", "success", {"page": page, "limit": limit, "search": search, "status": status, "total": total})
        return jsonify({"success": True, "items": items, "total": total, "page": page, "limit": limit})

    @admin_bp.route("/api/admin/import-details/<int:import_id>", methods=["GET"])
    @deps["admin_required"]
    def admin_import_details(import_id):
        actor_id = deps["get_current_user"]()
        
        import json
        
        try:
            row = deps["run_query"](
                """
                SELECT 
                    hi.id, hi.nom_fichier, hi.nb_lignes, hi.nb_erreurs, hi.statut,
                    hi.date_import, hi.user_id, hi.importe_par, hi.details, hi.data,
                    u.firstname, u.lastname, u.email
                FROM historique_imports hi
                LEFT JOIN users u ON u.id = hi.user_id
                WHERE hi.id = %s
                """,
                (import_id,),
                True
            )
            
            if not row:
                deps["log_audit"](actor_id, "api_access", f"import_details:{import_id}", "failed", "Import non trouvé")
                return jsonify({"error": "Import non trouvé"}), 404
            
            # Safe resolution of helpers: use deps if provided, otherwise fall back to globals
            decompress_fn = deps.get("decompress_payload") or globals().get("decompress_payload")
            decode_rows_fn = deps.get("decode_import_rows") or globals().get("decode_import_rows")

            # Decode details field
            def decode_field(field_value):
                if not field_value:
                    return []
                try:
                    parsed = json.loads(field_value)
                    if isinstance(parsed, dict) and parsed.get("compressed"):
                        if callable(decompress_fn):
                            return decompress_fn(parsed.get("content")) or []
                        return []
                    return parsed if isinstance(parsed, list) else [str(parsed)]
                except Exception:
                    return [str(field_value)] if field_value else []

            details = decode_field(row.get("details"))
            data_rows = (decode_rows_fn(row.get("data")) if callable(decode_rows_fn) else [])

            # Build KPIs from the imported rows. Prefer shared KPI builder if available.
            kpis = []
            try:
                builder = deps.get("_build_kpis_from_rows") or globals().get("_build_kpis_from_rows")
                if callable(builder):
                    kpis = builder(data_rows or []) or []
                else:
                    # Fallback simple KPI computation (totals/counts)
                    try:
                        import pandas as _pd
                        _df = _pd.DataFrame(data_rows or [])
                        if not _df.empty:
                            _df["Montant"] = _pd.to_numeric(_df.get("Montant"), errors="coerce").fillna(0.0).abs()
                            _df["Montant_Signe"] = _pd.to_numeric(_df.get("Montant_Signe"), errors="coerce").fillna(0.0)
                            total = float(_df["Montant"].sum())
                            signed = float(_df["Montant_Signe"].sum())
                            revenus = float(_df.loc[_df["Montant_Signe"] > 0, "Montant_Signe"].sum())
                            depenses = abs(float(_df.loc[_df["Montant_Signe"] < 0, "Montant_Signe"].sum()))
                            n = int(len(_df))
                            kpis = [
                                {"kpiNom": "CA_Total", "periode": "global", "valeur": round(total, 2)},
                                {"kpiNom": "Solde_Net", "periode": "global", "valeur": round(signed, 2)},
                                {"kpiNom": "Revenus", "periode": "global", "valeur": round(revenus, 2)},
                                {"kpiNom": "Dépenses", "periode": "global", "valeur": round(depenses, 2)},
                                {"kpiNom": "Nb_Transactions", "periode": "global", "valeur": n},
                            ]
                    except Exception:
                        kpis = []
            except Exception:
                kpis = []
            
            # Format date
            date_import = row.get("date_import")
            if date_import and hasattr(date_import, "strftime"):
                date_import = date_import.strftime("%Y-%m-%d %H:%M:%S")
            
            result = {
                "success": True,
                "id": row.get("id"),
                "nom_fichier": row.get("nom_fichier"),
                "nb_lignes": row.get("nb_lignes"),
                "nb_erreurs": row.get("nb_erreurs"),
                "statut": row.get("statut"),
                "date_import": date_import,
                "firstname": row.get("firstname"),
                "lastname": row.get("lastname"),
                "email": row.get("email"),
                "importe_par": row.get("importe_par"),
                "details": details,
                "data_preview": data_rows[:100] if isinstance(data_rows, list) else [],
                "kpis": kpis
            }
            
            deps["log_audit"](actor_id, "api_access", f"import_details:{import_id}", "success", {})
            return jsonify(result)
            
        except Exception as e:
            deps["log_audit"](actor_id, "api_access", f"import_details:{import_id}", "failed", str(e))
            return jsonify({"error": "Erreur lors de la récupération des détails"}), 500

    @admin_bp.route("/api/admin/reports/<int:report_id>/download", methods=["GET"])
    @deps["admin_required"]
    def admin_download_report(report_id):
        actor_id = deps["get_current_user"]()
        report = deps["run_query"](
            """
            SELECT id_rapport, nom_rapport, format, chemin_fichier, created_by
            FROM rapports
            WHERE id_rapport=%s
            """,
            (report_id,),
            True,
        )
        if not report:
            deps["log_audit"](actor_id, "export_rapport", f"report:{report_id}", "failed", "Rapport introuvable")
            return jsonify({"success": False, "error": "Rapport introuvable"}), 404

        rel_path = (report.get("chemin_fichier") or "").strip()
        if not rel_path:
            deps["log_audit"](actor_id, "export_rapport", f"report:{report_id}", "failed", "Chemin du fichier manquant")
            return jsonify({"success": False, "error": "Fichier du rapport manquant"}), 404

        file_path = (deps["REPORTS_FOLDER"] / rel_path).resolve()
        base_path = deps["REPORTS_FOLDER"].resolve()
        if str(file_path).startswith(str(base_path)) is False or not file_path.exists():
            deps["log_audit"](actor_id, "export_rapport", f"report:{report_id}", "failed", "Fichier indisponible")
            return jsonify({"success": False, "error": "Fichier du rapport introuvable"}), 404

        filename = f"{deps['_safe_report_stem'](report.get('nom_rapport') or f'report_{report_id}')}.{deps['_normalize_report_format'](report.get('format') or '') or 'pdf'}"
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        deps["log_audit"](actor_id, "export_rapport", f"report:{report_id}", "success", {"filename": filename})
        return send_from_directory(str(file_path.parent), file_path.name, as_attachment=True, download_name=filename, mimetype=mime)

    @admin_bp.route("/api/admin/users/<int:target_id>/role", methods=["PUT"])
    @deps["admin_required"]
    def update_user_role(target_id):
        actor_id = deps["get_current_user"]()
        allowed_roles = {"user", "admin"}
        new_role = (request.get_json() or {}).get("role", "").strip().lower()
        if new_role not in allowed_roles:
            deps["log_audit"](actor_id, "changement_role", f"user:{target_id}", "failed", {"new_role": new_role, "reason": "Rôle invalide"})
            return jsonify({"error": "Rôle invalide"}), 400
        target = deps["run_query"]("SELECT id,firstname,lastname,email,COALESCE(role,'user') AS role FROM users WHERE id=%s", (target_id,), True)
        if not target:
            deps["log_audit"](actor_id, "changement_role", f"user:{target_id}", "failed", "Utilisateur non trouvé")
            return jsonify({"error": "Utilisateur non trouvé"}), 404
        try:
            deps["run_update"]("UPDATE users SET role=%s WHERE id=%s", (new_role, target_id))
            deps["log_audit"](actor_id, "changement_role", f"user:{target_id}", "success", {"email": target.get("email"), "old_role": target.get("role"), "new_role": new_role})
            return jsonify({"success": True, "message": "Rôle mis à jour", "user": {"id": target_id, "email": target.get("email"), "role": new_role}})
        except Exception as e:
            deps["log_audit"](actor_id, "changement_role", f"user:{target_id}", "failed", str(e))
            return jsonify({"success": False, "error": "Impossible de modifier le rôle."}), 500

    @admin_bp.route("/api/admin/users", methods=["POST"])
    @deps["admin_required"]
    def create_user_admin():
        actor_id = deps["get_current_user"]()
        d = request.get_json() or {}
        firstname = (d.get("firstname") or "").strip()
        lastname = (d.get("lastname") or "").strip()
        email = (d.get("email") or "").strip().lower()
        password = d.get("password") or ""
        role = (d.get("role") or "user").strip().lower()
        allowed_roles = {"user", "admin"}

        if not all([firstname, lastname, email, password]):
            deps["log_audit"](actor_id, "creation_utilisateur", "users", "failed", {"email": email, "reason": "Champs requis manquants"})
            return jsonify({"error": "Tous les champs sont requis"}), 400
        if not deps["is_strong_password"](password):
            deps["log_audit"](actor_id, "creation_utilisateur", "users", "failed", {"email": email, "reason": "Mot de passe faible"})
            return jsonify({"error": "Le mot de passe doit contenir au moins 8 caractères, avec majuscule, minuscule, chiffre et symbole"}), 400
        if role not in allowed_roles:
            deps["log_audit"](actor_id, "creation_utilisateur", "users", "failed", {"email": email, "reason": "Rôle invalide"})
            return jsonify({"error": "Rôle invalide"}), 400
        if deps["run_query"]("SELECT id FROM users WHERE LOWER(TRIM(email))=%s", (email,), True):
            deps["log_audit"](actor_id, "creation_utilisateur", "users", "failed", {"email": email, "reason": "Email déjà utilisé"})
            return jsonify({"error": "Email déjà utilisé"}), 409

        try:
            user_id = deps["run_update"](
                "INSERT INTO users (firstname,lastname,email,password,role,login_type,created_at) VALUES (%s,%s,%s,%s,%s,'email',NOW())",
                (firstname, lastname, email, deps["generate_password_hash"](password), role),
            )
        except Exception as exc:
            deps["log_audit"](actor_id, "creation_utilisateur", "users", "failed", {"email": email, "reason": str(exc)})
            return deps["internal_error_response"]("Impossible de créer l'utilisateur.")

        deps["log_audit"](actor_id, "creation_utilisateur", f"user:{user_id}", "success", {"email": email, "role": role})
        return jsonify({"message": "Utilisateur créé", "user": {"id": user_id, "firstname": firstname, "lastname": lastname, "email": email, "role": role, "login_type": "email"}}), 201

    @admin_bp.route("/api/users/export", methods=["GET"])
    @deps["admin_required"]
    def export_users():
        actor_id = deps["get_current_user"]()
        users = deps["run_query"]("SELECT id,firstname,lastname,email,created_at,COALESCE(role,'user') AS role,COALESCE(login_type,'email') AS login_type FROM users ORDER BY id")
        lines = ["id,firstname,lastname,email,role,login_type,created_at"]
        for user in users:
            created = user["created_at"]
            if hasattr(created, "strftime"):
                created = created.strftime("%Y-%m-%d %H:%M")
            lines.append(f"{user['id']},{user['firstname']},{user['lastname']},{user['email']},{user.get('role','user')},{user.get('login_type','email')},{created}")
        deps["log_audit"](actor_id, "export_rapport", "users_csv", "success", {"rows": len(users)})
        return Response("\n".join(lines), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=users.csv"})

    return admin_bp
