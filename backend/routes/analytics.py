import time
from datetime import datetime

from flask import Blueprint, jsonify, request

_ANALYTICS_RESPONSE_CACHE = {}
_ANALYTICS_CACHE_TTL = 120  # seconds


def _get_cached_analytics_response(cache_key):
    entry = _ANALYTICS_RESPONSE_CACHE.get(cache_key)
    if not entry:
        return None
    if entry["expires_at"] < time.time():
        _ANALYTICS_RESPONSE_CACHE.pop(cache_key, None)
        return None
    return entry["payload"]


def _set_cached_analytics_response(cache_key, payload):
    if not cache_key:
        return
    _ANALYTICS_RESPONSE_CACHE[cache_key] = {
        "payload": payload,
        "expires_at": time.time() + _ANALYTICS_CACHE_TTL,
    }


def _to_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _to_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


def _clamp_int(value, default, min_value, max_value):
    parsed = _to_int(value, default)
    if parsed < min_value:
        return min_value
    if parsed > max_value:
        return max_value
    return parsed


def _extract_period(row):
    period = str(row.get("year_month") or row.get("YearMonth") or "").strip()
    if len(period) >= 7:
        return period[:7]
    date_val = str(row.get("date_val") or row.get("Date") or "").strip()
    if len(date_val) >= 7 and date_val[4] == "-":
        return date_val[:7]
    return "N/A"


def _clean_label(value, default):
    text = str(value or "").strip()
    return text if text else default


def _build_analytics_aggregations(rows):
    period_stats = {}
    departement_stats = {}
    tx_type_stats = {}
    expense_type_stats = {}

    for row in rows:
        signed = _to_float(row.get("Montant_Signe"), 0.0)
        montant = abs(_to_float(row.get("Montant"), abs(signed)))
        period = _extract_period(row)
        departement = _clean_label(row.get("departement"), "Non renseigne")
        tx_type = _clean_label(row.get("type_transaction"), "Non renseigne")
        expense_type = _clean_label(row.get("type_depense"), "N/A")

        p = period_stats.setdefault(period, {"periode": period, "revenus": 0.0, "depenses": 0.0, "solde_net": 0.0, "transactions": 0})
        if signed >= 0:
            p["revenus"] += signed
        else:
            p["depenses"] += abs(signed)
        p["solde_net"] += signed
        p["transactions"] += 1

        d = departement_stats.setdefault(
            departement,
            {"departement": departement, "revenus": 0.0, "depenses": 0.0, "solde_net": 0.0, "transactions": 0},
        )
        if signed >= 0:
            d["revenus"] += signed
        else:
            d["depenses"] += abs(signed)
        d["solde_net"] += signed
        d["transactions"] += 1

        t = tx_type_stats.setdefault(
            tx_type,
            {"type_transaction": tx_type, "volume": 0.0, "revenus": 0.0, "depenses": 0.0, "transactions": 0},
        )
        t["volume"] += montant
        if signed >= 0:
            t["revenus"] += signed
        else:
            t["depenses"] += abs(signed)
        t["transactions"] += 1

        if signed < 0:
            e = expense_type_stats.setdefault(
                expense_type,
                {"type_depense": expense_type, "depenses": 0.0, "transactions": 0},
            )
            e["depenses"] += abs(signed)
            e["transactions"] += 1

    return period_stats, departement_stats, tx_type_stats, expense_type_stats


def create_analytics_blueprint(deps):
    analytics_bp = Blueprint("analytics", __name__)

    @analytics_bp.route("/api/dashboard/summary", methods=["GET"])
    @deps["jwt_required"]
    def dashboard_summary():
        started = time.perf_counter()
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"success": False, "error": "Auth requis"}), 401

        try:
            dataset = deps["get_active_user_dataset"](user_id)
            if not dataset.get("success"):
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                deps["log_audit"](
                    user_id,
                    "api_access",
                    "dashboard_summary",
                    "failed",
                    {"response_ms": elapsed_ms, "reason": dataset.get("message") or "Aucun import disponible"},
                )
                return deps["no_import_response"]()

            import_id = dataset.get("import_id") or "default"
            cache_key = ("dashboard_summary", import_id)
            cached_payload = _get_cached_analytics_response(cache_key)
            if cached_payload is not None:
                deps["log_audit"](user_id, "api_access", "dashboard_summary", "success", {"cache": True})
                return jsonify(cached_payload)

            rows = dataset.get("rows") or []
            summary = deps["build_dashboard_summary_from_rows"](rows)
            summary["import_id"] = dataset.get("import_id")
            summary["filename"] = dataset.get("filename")
            source = dataset.get("source") or "dynamic_transaction_table"

            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            payload = {
                "success": True,
                "source": source,
                "table_name": dataset.get("table_name"),
                "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "response_ms": elapsed_ms,
                "kpis": {
                    "transactions": int(summary.get("tx_count") or 0),
                    "revenus_totaux": round(deps["to_float"](summary.get("revenus_totaux"), 0.0), 2),
                    "depenses_totales": round(deps["to_float"](summary.get("depenses_totales"), 0.0), 2),
                    "solde_net": round(deps["to_float"](summary.get("solde_net"), 0.0), 2),
                    "marge_pct": round(deps["to_float"](summary.get("marge_pct"), 0.0), 2),
                },
                "period_stats": deps["make_json_safe"](summary.get("period_stats") or []),
                "departement_stats": deps["make_json_safe"](summary.get("departement_stats") or []),
            }
            if summary.get("import_id"):
                payload["import_id"] = summary.get("import_id")
                payload["filename"] = summary.get("filename")

            deps["log_audit"](user_id, "api_access", "dashboard_summary", "success", {"response_ms": elapsed_ms, "source": source})
            if elapsed_ms > 3000:
                deps["log_audit"](user_id, "erreur_systeme", "dashboard_summary", "failed", {"response_ms": elapsed_ms, "threshold_ms": 3000})

            _set_cached_analytics_response(("dashboard_summary", import_id), payload)
            return jsonify(payload)
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            deps["log_audit"](user_id, "erreur_systeme", "dashboard_summary", "failed", {"response_ms": elapsed_ms, "error": str(e)})
            return deps["internal_error_response"]("Impossible de calculer le resume du tableau de bord.")

    @analytics_bp.route("/api/analytics/data", methods=["GET"])
    @analytics_bp.route("/api/charts", methods=["GET"])
    @deps["jwt_required"]
    def analytics_data():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"success": False, "error": "Auth requis"}), 401

        dataset = deps["get_active_user_dataset"](user_id)
        rows = dataset.get("rows") or []
        if not dataset.get("success") or not rows:
            deps["log_audit"](user_id, "api_access", "analytics_data", "failed", dataset.get("message") or "Aucun import disponible")
            return deps["no_import_response"]()

        import_id = dataset.get("import_id") or "default"
        page = _clamp_int(request.args.get("page"), 1, 1, 100000)
        if request.args.get("limit") is None:
            limit = int(len(rows)) if len(rows) > 0 else 1
        else:
            limit = _clamp_int(request.args.get("limit"), 1000, 1, max(int(len(rows)), 1000000))
        cache_key = ("analytics_data", import_id, page, limit)
        cached_payload = _get_cached_analytics_response(cache_key)
        if cached_payload is not None:
            deps["log_audit"](user_id, "api_access", "analytics_data", "success", {"cache": True, "import_id": import_id, "page": page, "limit": limit})
            return jsonify(cached_payload)

        total_rows = int(len(rows))
        page = _clamp_int(request.args.get("page"), 1, 1, 100000)
        if request.args.get("limit") is None:
            limit = total_rows if total_rows > 0 else 1
        else:
            limit = _clamp_int(request.args.get("limit"), 1000, 1, max(total_rows, 1000000))
        total_pages = max(1, (total_rows + limit - 1) // limit)
        if page > total_pages:
            page = total_pages
        start = (page - 1) * limit
        end = start + limit
        paged_rows = rows[start:end]

        # Aggregations are computed once server-side to keep chart payloads light.
        period_stats, departement_stats, tx_type_stats, expense_type_stats = _build_analytics_aggregations(rows)
        period_stats_list = sorted(period_stats.values(), key=lambda x: str(x.get("periode") or ""))[:60]
        departement_stats_list = sorted(departement_stats.values(), key=lambda x: abs(_to_float(x.get("solde_net"), 0.0)), reverse=True)[:20]
        tx_type_stats_list = sorted(tx_type_stats.values(), key=lambda x: _to_float(x.get("volume"), 0.0), reverse=True)[:20]
        expense_type_stats_list = sorted(expense_type_stats.values(), key=lambda x: _to_float(x.get("depenses"), 0.0), reverse=True)[:20]

        deps["log_audit"](
            user_id,
            "api_access",
            "analytics_data",
            "success",
            {"import_id": dataset.get("import_id"), "rows": total_rows, "page": page, "limit": limit},
        )
        payload = deps["make_json_safe"]({
            "success": True,
            "analytics_ready": True,
            "message": "Donnees importees chargees avec succes",
            "import_id": dataset.get("import_id"),
            "filename": dataset.get("filename"),
            "status": "succes",
            "imported_at": dataset.get("date_import").isoformat() if dataset.get("date_import") else None,
            "table_name": dataset.get("table_name"),
            "import": {
                "id": dataset.get("import_id"),
                "filename": dataset.get("filename"),
                "status": "succes",
                "imported_at": dataset.get("date_import").isoformat() if dataset.get("date_import") else None,
                "rows_count": total_rows,
            },
            "count": total_rows,
            "total": total_rows,
            "total_rows": total_rows,
            "returned_rows": len(paged_rows),
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "source": dataset.get("source") or "dynamic_transaction_table",
            "rows": paged_rows,
            "data": paged_rows,
            "aggregations": {
                "period_stats": period_stats_list,
                "departement_stats": departement_stats_list,
                "type_transaction_stats": tx_type_stats_list,
                "type_depense_stats": expense_type_stats_list,
            },
        })
        _set_cached_analytics_response(cache_key, payload)
        return jsonify(payload)

    @analytics_bp.route("/api/analytics/kpi-refresh", methods=["POST"])
    @deps["jwt_required"]
    def kpi_refresh():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"error": "Auth requis"}), 401
        dataset = deps["get_active_user_dataset"](user_id)
        rows = dataset.get("rows") or []
        if not dataset.get("success") or not rows:
            deps["log_audit"](user_id, "generation_rapport", "analytics_kpi_refresh", "failed", "Aucun import disponible")
            return deps["no_import_response"]()

        kpis = deps["build_kpis_from_rows"](rows)
        deps["log_audit"](
            user_id,
            "generation_rapport",
            "analytics_kpi_refresh",
            "success",
            {"import_id": dataset.get("import_id"), "kpi_count": len(kpis)},
        )
        return jsonify({
            "success": True,
            "import_id": dataset.get("import_id"),
            "inserted": len(kpis),
            "message": "KPI recalcules a partir du dernier import",
        })

    @analytics_bp.route("/api/analytics/debug-import", methods=["GET"])
    @deps["jwt_required"]
    def debug_import():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"success": False, "error": "Auth requis"}), 401

        latest_for_user = deps["run_query"](
            """
            SELECT id, user_id, nom_fichier, statut, nb_lignes, date_import, CHAR_LENGTH(data) AS data_length
            FROM historique_imports
            WHERE user_id=%s
            ORDER BY date_import DESC, id DESC
            LIMIT 1
            """,
            (user_id,),
            True,
        )
        latest_success = deps["get_last_successful_import"](user_id)
        decoded_rows_count = 0
        data_length = 0
        if latest_success:
            try:
                data_length = int(latest_success.get("data_length") or len(str(latest_success.get("data") or "")))
            except Exception:
                data_length = 0
            decoded_rows_count = len(deps["decode_import_rows"](latest_success.get("data")))

        dataset = deps["get_active_user_dataset"](user_id)
        latest_success_payload = None
        if latest_success:
            latest_success_payload = {
                "id": latest_success.get("id"),
                "user_id": latest_success.get("user_id"),
                "nom_fichier": latest_success.get("nom_fichier"),
                "statut": latest_success.get("statut"),
                "nb_lignes": latest_success.get("nb_lignes"),
                "date_import": latest_success.get("date_import").isoformat() if latest_success.get("date_import") else None,
                "data_length": len(str(latest_success.get("data") or "")),
            }

        return jsonify(deps["make_json_safe"]({
            "success": True,
            "current_user_id": user_id,
            "latest_import_for_user": latest_for_user,
            "latest_success_import_for_user": latest_success_payload,
            "data_length": data_length,
            "decoded_rows_count": decoded_rows_count,
            "analytics_will_work": bool(dataset.get("success") and (dataset.get("rows") or [])),
            "message": dataset.get("message") if not dataset.get("success") else "Dataset utilisateur pret pour Analytics.",
        }))

    @analytics_bp.route("/api/analytics/debug-kpi-source", methods=["GET"])
    @deps["jwt_required"]
    def debug_kpi_source():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"success": False, "error": "Auth requis"}), 401

        def _safe_table_count(table_name):
            try:
                row = deps["run_query"](f"SELECT COUNT(*) AS n FROM {table_name}", fetch_one=True) or {"n": 0}
                return int(row.get("n") or 0)
            except Exception:
                return 0

        latest_success = deps["get_last_successful_import"](user_id)
        decoded_rows_count = 0
        if latest_success:
            decoded_rows_count = len(deps["decode_import_rows"](latest_success.get("data")))

        dataset = deps["get_active_user_dataset"](user_id)
        selected_rows = len(dataset.get("rows") or [])
        selected_source = dataset.get("source") or "unknown"

        return jsonify(deps["make_json_safe"]({
            "success": True,
            "current_user_id": user_id,
            "kpi_source_selected": selected_source,
            "kpi_rows_selected": selected_rows,
            "selection_is_valid": bool(dataset.get("success") and selected_rows > 0),
            "selected_import_id": dataset.get("import_id"),
            "selected_filename": dataset.get("filename"),
            "latest_success_import": {
                "id": latest_success.get("id") if latest_success else None,
                "filename": latest_success.get("nom_fichier") if latest_success else None,
                "status": latest_success.get("statut") if latest_success else None,
                "nb_lignes": latest_success.get("nb_lignes") if latest_success else 0,
                "decoded_rows": decoded_rows_count,
            },
            "db_counters": {
                "transactions": _safe_table_count("transactions"),
                "valeur_kpi": _safe_table_count("valeur_kpi"),
                "historique_imports": _safe_table_count("historique_imports"),
            },
            "message": dataset.get("message") if not dataset.get("success") else "Source KPI resolue avec succes.",
        }))

    return analytics_bp
