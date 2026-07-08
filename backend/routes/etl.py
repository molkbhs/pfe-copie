import json

from flask import Blueprint, jsonify, request, send_from_directory

HISTORY_ROWS_SAMPLE_LIMIT = 200
UPLOAD_PREVIEW_ROWS_LIMIT = 200
PROCESS_PREVIEW_ROWS_LIMIT = 200


def _sample_rows(rows, limit):
    if not isinstance(rows, list):
        return []
    if limit <= 0:
        return []
    return rows[:limit]


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _safe_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def create_etl_blueprint(deps):
    etl_bp = Blueprint("etl", __name__)

    @etl_bp.route("/api/etl/history", methods=["GET"])
    @deps["jwt_required"]
    def get_etl_history():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"error": "Auth requis"}), 401

        current = deps["run_query"]("SELECT id,COALESCE(role,'user') AS role FROM users WHERE id=%s", (user_id,), True)
        if not current:
            return jsonify({"error": "Utilisateur introuvable"}), 404

        base_sql = """
            SELECT h.id,h.user_id,h.nom_fichier,h.date_import,h.nb_lignes,h.nb_erreurs,
                   h.statut,h.departement,h.importe_par,h.details,h.data,
                   u.firstname,u.lastname,u.email
            FROM historique_imports h
            LEFT JOIN users u ON u.id=h.user_id
        """
        if current["role"] == "admin":
            rows = deps["run_query"](base_sql + " ORDER BY COALESCE(u.firstname,u.email) ASC, h.date_import DESC")
        else:
            rows = deps["run_query"](base_sql + " WHERE h.user_id=%s ORDER BY h.date_import DESC", (user_id,))

        grouped = {}
        for row in rows:
            uname = (f"{row.get('firstname') or ''} {row.get('lastname') or ''}".strip() or row.get("email") or row.get("importe_par") or f"User {row.get('user_id')}")
            grouped.setdefault(uname, [])

            def _decode(field):
                raw = row.get(field)
                if not raw:
                    return []
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and parsed.get("compressed"):
                        return deps["decompress_payload"](parsed.get("content")) or []
                    return parsed
                except Exception:
                    return []

            data_rows = deps["decode_import_rows"](row.get("data"))
            total_rows = _safe_int(row.get("nb_lignes"), len(data_rows))
            sampled_rows = _sample_rows(data_rows, HISTORY_ROWS_SAMPLE_LIMIT)
            grouped[uname].append({
                "id": row["id"],
                "user_id": row["user_id"],
                "nom_fichier": row["nom_fichier"],
                "filename": row["nom_fichier"],
                "date_import": row["date_import"].isoformat() if row.get("date_import") else None,
                "imported_at": row["date_import"].isoformat() if row.get("date_import") else None,
                "date_import_label": row["date_import"].strftime("%Y-%m-%d %H:%M") if row.get("date_import") else "",
                "nb_lignes": row["nb_lignes"],
                "nb_erreurs": row["nb_erreurs"],
                "rows_count": row["nb_lignes"],
                "statut": row["statut"],
                "departement": row["departement"],
                "status": row["statut"],
                "importe_par": row["importe_par"],
                "details": _decode("details"),
                "data": {"total_rows": total_rows, "rows": sampled_rows},
            })

        return jsonify({"success": True, "groups": [{"user_name": k, "items": v} for k, v in grouped.items()]})

    @etl_bp.route("/api/etl/history/<int:import_id>", methods=["DELETE"])
    @deps["jwt_required"]
    def delete_etl_history(import_id):
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"error": "Auth requis"}), 401
        try:
            deps["run_update"]("DELETE FROM historique_imports WHERE id=%s AND user_id=%s", (import_id, user_id))
            return jsonify({"success": True, "message": "Import supprimé"})
        except Exception as e:
            deps["log_audit"](user_id, "import_donnees", f"delete_import:{import_id}", "failed", str(e)[:255])
            return deps["internal_error_response"]("Impossible de supprimer l'import.")

    @etl_bp.route("/api/etl/ping", methods=["GET"])
    def etl_ping():
        return jsonify({"status": "ok", "message": "Backend Flask opérationnel"})

    @etl_bp.route("/api/etl/upload", methods=["POST"])
    @etl_bp.route("/api/upload", methods=["POST"])
    @deps["jwt_required"]
    @deps["limiter"].limit(deps["UPLOAD_RATE_LIMIT"])
    def etl_upload():
        user_id = deps["get_current_user"]()
        if "file" not in request.files:
            deps["log_audit"](user_id, "api_access", "etl_upload", "failed", "Aucun fichier reçu")
            return jsonify({"error": "Aucun fichier reçu"}), 400
        file = request.files["file"]
        fname = file.filename or ""
        if not any(fname.lower().endswith(e) for e in (".csv", ".xlsx", ".xls")):
            deps["log_audit"](user_id, "api_access", "etl_upload", "failed", {"filename": fname, "reason": "Format non supporte"})
            return jsonify({"error": "Format accepté : CSV, XLSX, XLS"}), 400

        try:
            safe_fname, save_path = deps["safe_upload_path"](fname)
        except ValueError as exc:
            deps["log_audit"](user_id, "api_access", "etl_upload", "failed", {"filename": fname, "reason": str(exc)})
            return jsonify({"error": str(exc)}), 400

        file.save(str(save_path))
        try:
            df = deps["read_table_rows"](save_path)
            imported_rows = deps["make_json_safe"](df.to_dict(orient="records"))
            preview_rows = _sample_rows(imported_rows, UPLOAD_PREVIEW_ROWS_LIMIT)
            return jsonify({
                "success": True,
                "filename": safe_fname,
                "imported_rows": preview_rows,
                "stats": {
                    "columns": deps["make_json_safe"](list(df.columns)),
                    "rows_preview": len(preview_rows),
                    "total_rows": len(imported_rows),
                },
            })
        except Exception as e:
            deps["log_audit"](user_id, "api_access", "etl_upload", "failed", str(e))
            return deps["internal_error_response"]("Impossible de lire le fichier importé.")

    @etl_bp.route("/api/etl/process", methods=["POST"])
    @deps["jwt_required"]
    def etl_process():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"error": "Authentification requise"}), 401

        d = request.get_json() or {}
        filename = d.get("filename")
        replace_existing = True
        if not filename:
            deps["log_audit"](user_id, "import_donnees", "etl_process", "failed", "Nom de fichier manquant")
            return jsonify({"error": "Nom de fichier manquant"}), 400

        try:
            filename, file_path = deps["safe_upload_path"](filename)
        except ValueError as exc:
            deps["log_audit"](user_id, "import_donnees", "etl_process", "failed", {"filename": filename, "reason": str(exc)})
            return jsonify({"error": str(exc)}), 400

        if not file_path.exists():
            deps["log_audit"](user_id, "import_donnees", f"etl_file:{filename}", "failed", "Fichier non trouvé")
            return jsonify({"error": "Fichier non trouvé"}), 404

        try:
            result = deps["run_generic_etl"](str(file_path), replace_existing=replace_existing)
            safe = lambda x: deps["make_json_safe"](x)

            if not result.get("success"):
                elog = safe(result.get("log", []))
                try:
                    deps["save_import_history"](user_id, filename, {"lignes": 0, "nb_erreurs": 1}, elog, [], False)
                except Exception:
                    pass
                deps["log_audit"](user_id, "import_donnees", f"etl_file:{filename}", "failed", {"error": result.get("error", "Erreur ETL"), "detail": result.get("detail")})
                return jsonify({"success": False, "error": result.get("error", "Erreur ETL"), "detail": result.get("detail"), "log": elog}), 500

            stats = safe(result.get("stats", {}))
            log = safe(result.get("log", []))
            after_rows = safe(result.get("after_rows", []))
            before_rows = safe(result.get("before_rows", []))
            nb_lignes = int((stats or {}).get("lignes", len(after_rows)) or 0)

            if nb_lignes <= 0 or not after_rows:
                msg = "Import invalide: données nettoyées vides. Veuillez vérifier le fichier source."
                try:
                    deps["save_import_history"](user_id, filename, {"lignes": 0, "nb_erreurs": 1}, log + [msg], [], False)
                except Exception:
                    pass
                deps["log_audit"](user_id, "import_donnees", f"etl_file:{filename}", "failed", {"error": msg})
                return jsonify({"success": False, "error": msg, "log": log}), 400

            rows_sample_for_history = _sample_rows(after_rows, HISTORY_ROWS_SAMPLE_LIMIT)
            rows_sample_for_response = _sample_rows(after_rows, PROCESS_PREVIEW_ROWS_LIMIT)
            before_rows_sample = _sample_rows(before_rows, PROCESS_PREVIEW_ROWS_LIMIT)
            stats_for_history = dict(stats or {})
            stats_for_history.setdefault("total_rows", nb_lignes)

            try:
                import_id = deps["save_import_history"](user_id, filename, stats_for_history, log, rows_sample_for_history, True)
            except Exception as he:
                deps["log_audit"](user_id, "import_donnees", f"etl_file:{filename}", "failed", {"error": "Impossible d'enregistrer l'historique", "detail": str(he)})
                return deps["internal_error_response"]("Nettoyage terminé mais impossible d'enregistrer l'historique d'import.")

            deps["log_audit"](user_id, "import_donnees", f"import:{import_id}", "success", {"filename": filename, "rows_inserted": (result.get("db_result") or {}).get("rows_inserted", 0), "changed_rows": result.get("changed_rows", 0), "replace_existing": replace_existing})
            return jsonify({
                "success": True,
                "log": log,
                "stats": {**(stats or {}), "total_rows": nb_lignes},
                "before_rows": before_rows_sample,
                "after_rows": rows_sample_for_response,
                "changed_rows": safe(result.get("changed_rows", 0)),
                "db_result": safe(result.get("db_result", {})),
                "import_id": import_id,
                "preview_limit": PROCESS_PREVIEW_ROWS_LIMIT,
                "total_rows": nb_lignes,
                "replace_existing": replace_existing,
            })
        except Exception as e:
            deps["log_audit"](user_id, "import_donnees", f"etl_file:{filename or ''}", "failed", str(e))
            return deps["internal_error_response"]("Le traitement ETL a échoué.")

    @etl_bp.route("/api/etl/download", methods=["GET"])
    @deps["jwt_required"]
    def etl_download():
        user_id = deps["get_current_user"]()
        cleaned = deps["UPLOAD_FOLDER"] / "donnees_nettoyees.csv"
        if not cleaned.exists():
            deps["log_audit"](user_id, "export_rapport", "etl_download", "failed", "Aucun fichier nettoyé disponible")
            return jsonify({"error": "Aucun fichier nettoyé disponible"}), 404
        deps["log_audit"](user_id, "export_rapport", "etl_download", "success", "Export du fichier nettoyé")
        return send_from_directory(str(deps["UPLOAD_FOLDER"]), "donnees_nettoyees.csv", as_attachment=True, mimetype="text/csv")

    @etl_bp.route("/api/etl/table-data", methods=["GET"])
    @deps["jwt_required"]
    def etl_table_data():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"error": "Authentification requise"}), 401
        filename = request.args.get("filename", "").strip()
        stage = request.args.get("stage", "before").strip().lower()
        if not filename:
            return jsonify({"error": "Nom de fichier manquant"}), 400
        try:
            if stage == "before":
                src = deps["UPLOAD_FOLDER"] / filename
                if not src.exists():
                    return jsonify({"error": "Fichier source introuvable"}), 404
                df = deps["read_table_rows"](src)
            elif stage == "after":
                cleaned = deps["UPLOAD_FOLDER"] / "donnees_nettoyees.csv"
                if not cleaned.exists():
                    return jsonify({"error": "Fichier nettoyé introuvable"}), 404
                df = deps["pd"].read_csv(str(cleaned), encoding="utf-8-sig", low_memory=False)
            else:
                return jsonify({"error": "Stage invalide (before|after)"}), 400
            rows = deps["make_json_safe"](df.to_dict(orient="records"))
            return jsonify({"success": True, "stage": stage, "rows": rows, "total": len(rows)})
        except Exception as e:
            deps["log_audit"](user_id, "api_access", "etl_table_data", "failed", str(e)[:255])
            return deps["internal_error_response"]("Impossible de charger les données du tableau.")

    @etl_bp.route("/api/etl/schema", methods=["GET"])
    @deps["admin_required"]
    def etl_schema():
        try:
            sqlalchemy_mod = __import__("sqlalchemy", fromlist=["create_engine", "inspect"])
            create_engine = sqlalchemy_mod.create_engine
            sa_inspect = sqlalchemy_mod.inspect
            from config import DB_CONFIG as dbc
            engine = create_engine(f"mysql+pymysql://{dbc['user']}:{dbc['password']}@{dbc.get('host','localhost')}:{int(dbc.get('port', 3307))}/{dbc['database']}")
            insp = sa_inspect(engine)
            return jsonify({"success": True, "tables": {t: [{"name": c["name"], "type": str(c["type"])} for c in insp.get_columns(t)] for t in insp.get_table_names()}})
        except Exception as e:
            deps["log_audit"](deps["get_current_user"](), "api_access", "etl_schema", "failed", str(e)[:255])
            return deps["internal_error_response"]("Impossible de lire le schéma de la base.")

    return etl_bp
