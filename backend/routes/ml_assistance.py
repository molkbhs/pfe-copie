import time
import traceback

from flask import Blueprint, jsonify, request

ML_ASSISTANCE_CACHE_TTL = 300  # seconds
_ml_assistance_cache = {}


def _get_cached_response(key):
    entry = _ml_assistance_cache.get(key)
    if not entry:
        return None
    if entry["expires_at"] < time.time():
        _ml_assistance_cache.pop(key, None)
        return None
    return entry["value"]


def _set_cached_response(key, value, ttl=ML_ASSISTANCE_CACHE_TTL):
    _ml_assistance_cache[key] = {
        "value": value,
        "expires_at": time.time() + ttl,
    }


def _cache_key(prefix, user_id, *args):
    return (prefix, user_id) + tuple(args)


def create_ml_assistance_blueprint(deps):
    ml_bp = Blueprint("ml_assistance", __name__)

    @ml_bp.route("/api/assistance/ml-forecast", methods=["GET"])
    @deps["jwt_required"]
    def assistance_ml_forecast():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"success": False, "error": "Auth requis"}), 401

        trend_type = (request.args.get("type") or "seasonality_expenses").strip()
        try:
            horizon = int(request.args.get("horizon", 3))
        except Exception:
            horizon = 3
        horizon = max(1, min(horizon, 12))

        refresh = str(request.args.get("refresh", "false")).strip().lower() in {"1", "true", "yes"}
        cache_key = _cache_key("assistance_ml_forecast", user_id, trend_type, horizon)
        if not refresh:
            cached = _get_cached_response(cache_key)
            if cached is not None:
                return jsonify(cached)

        try:
            df = deps["fetch_ml_dataframe"](user_id)
            if df.empty:
                deps["log_audit"](user_id, "generation_rapport", "assistance_ml_forecast", "failed", "Aucun import disponible")
                return deps["no_import_response"]()

            series_df, meta = deps["build_trend_series"](df, trend_type)
            if series_df.empty or len(series_df) < 2:
                deps["log_audit"](user_id, "generation_rapport", "assistance_ml_forecast", "failed", "Pas assez d'historique")
                return jsonify({"success": False, "error": "Pas assez d'historique pour cette vue"}), 400

            result = deps["linear_forecast"](series_df, horizon=horizon, non_negative=meta["non_negative"])
            plain_language = deps["plain_summary"](df, trend_type, meta["entity"], result["summary"])
            deps["log_audit"](
                user_id,
                "generation_rapport",
                "assistance_ml_forecast",
                "success",
                {"type": trend_type, "horizon": horizon, "entity": meta.get("entity")},
            )

            payload = deps["make_json_safe"]({
                "success": True,
                "type": trend_type,
                "title": meta["title"],
                "selection": {"entity": meta["entity"]},
                "history": result["history"],
                "forecast": result["forecast"],
                "model": result["model"],
                "summary": result["summary"],
                "plain_language": plain_language,
            })
            _set_cached_response(cache_key, payload)
            return jsonify(payload)
        except Exception as e:
            deps["log_audit"](user_id, "generation_rapport", "assistance_ml_forecast", "failed", str(e))
            return deps["internal_error_response"]("Impossible de generer la prevision demandee.")

    @ml_bp.route("/api/ml/segmentation/run", methods=["POST"])
    @ml_bp.route("/api/ml/segmentation-multi", methods=["POST"])
    @deps["jwt_required"]
    def ml_segmentation_multi():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"error": "Auth requis"}), 401

        try:
            payload = request.get_json(silent=True) or {}
            entity = (payload.get("entity") or "fournisseur").strip().lower()
            k = int(payload.get("k", 4) or 4)

            if entity not in {"fournisseur", "projet", "departement"}:
                return jsonify({"success": False, "error": "Entite non supportee"}), 400

            df = deps["fetch_ml_dataframe"](user_id)
            if df.empty:
                return deps["no_import_response"]()
            if len(df) < 20:
                return jsonify({"success": False, "error": "Pas assez de donnees pour lancer la segmentation."}), 400

            return jsonify(deps["make_json_safe"](deps["build_multi_segmentation_payload"](df, entity, k)))
        except Exception as e:
            return jsonify({"success": False, "error": str(e), "detail": traceback.format_exc()}), 500

    @ml_bp.route("/api/assistance/simulate", methods=["POST"])
    @deps["jwt_required"]
    def simulate_what_if():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"success": False, "error": "Auth requis"}), 401

        try:
            payload = request.get_json(silent=True) or {}
            scenario = (payload.get("scenario") or "matieres_premieres").strip()
            imp, df = deps["fetch_ml_dataframe"](user_id, with_import=True)
            if not imp or df.empty:
                return deps["no_import_response"]()

            baseline_solde = float(df["Montant_Signe"].sum())
            baseline_ref = max(abs(baseline_solde), float(df["Montant_Signe"].abs().sum()) / 4, 1.0)

            delta_solde = 0.0
            headline = ""
            recommendation = ""
            details = []

            if scenario == "matieres_premieres":
                percent = float(payload.get("percent", 10) or 10)
                target = df[(df["Montant_Signe"] < 0) & (df["type_depense"].str.contains("mati", case=False, na=False))]
                if target.empty:
                    top_type = deps["top_value"](df, "type_depense", "expense")
                    target = df[(df["Montant_Signe"] < 0) & (df["type_depense"] == top_type)]
                current_cost = float(target["Montant_Signe"].abs().sum())
                delta_solde = -(current_cost * (percent / 100.0))
                headline = "Hausse du cout des matieres premieres"
                recommendation = "Surveillez l'achat des intrants, renegociez les contrats ou lissez les commandes."
                details = [
                    {"label": "Poste simule", "value": target["type_depense"].mode().iloc[0] if not target.empty else "Matieres premieres"},
                    {"label": "Cout actuel", "value": deps["fmt_dt_value"](current_cost)},
                    {"label": "Variation", "value": f"+{percent:.0f}%"},
                ]

            elif scenario == "transfert_budget":
                percent = float(payload.get("percent", 5) or 5)
                source_dep = str(payload.get("source_department") or "Inconnu")
                target_dep = str(payload.get("target_department") or "Inconnu")
                expense_type = str(payload.get("expense_type") or "Inconnu")
                source_rows = df[(df["departement"] == source_dep) & (df["type_depense"] == expense_type) & (df["Montant_Signe"] < 0)]
                current_cost = float(source_rows["Montant_Signe"].abs().sum())
                transferred = current_cost * (percent / 100.0)
                delta_solde = 0.0
                headline = "Transfert budgetaire sans effet sur le solde global"
                recommendation = "Le solde global ne change pas, mais la pression budgetaire se deplace entre deux departements."
                details = [
                    {"label": "Departement source", "value": source_dep},
                    {"label": "Departement cible", "value": target_dep},
                    {"label": "Montant transfere", "value": deps["fmt_dt_value"](transferred)},
                ]

            elif scenario == "baisse_commissions":
                percent = float(payload.get("percent", 15) or 15)
                department = str(payload.get("department") or "Inconnu")
                target = df[(df["departement"] == department) & (df["Montant_Signe"] < 0) & (df["type_depense"].str.contains("commission", case=False, na=False))]
                current_cost = float(target["Montant_Signe"].abs().sum())
                delta_solde = current_cost * (percent / 100.0)
                headline = "Reduction des commissions"
                recommendation = "Revisez la politique de commission si la marge brute est trop faible."
                details = [
                    {"label": "Departement", "value": department},
                    {"label": "Commissions actuelles", "value": deps["fmt_dt_value"](current_cost)},
                    {"label": "Reduction simulee", "value": f"-{percent:.0f}%"},
                ]

            elif scenario == "hausse_revenu_client":
                percent = float(payload.get("percent", 20) or 20)
                client_name = str(payload.get("client_fournisseur") or "Inconnu")
                target = df[(df["fournisseur"] == client_name) & (df["Montant_Signe"] > 0)]
                current_revenue = float(target["Montant_Signe"].sum())
                delta_solde = current_revenue * (percent / 100.0)
                headline = "Hausse des revenus d'un client"
                recommendation = "Concentrez les actions commerciales sur les comptes qui generent deja du revenu."
                details = [
                    {"label": "Client / partenaire", "value": client_name},
                    {"label": "Revenus actuels", "value": deps["fmt_dt_value"](current_revenue)},
                    {"label": "Hausse simulee", "value": f"+{percent:.0f}%"},
                ]

            elif scenario == "depense_to_revenu":
                expense_type = str(payload.get("expense_type") or "Inconnu")
                amount = float(payload.get("amount", 0) or 0)
                delta_solde = amount * 2.0
                headline = "Conversion d'une depense en revenu"
                recommendation = "Utilisez ce scenario pour simuler une subvention, un remboursement ou une reclassification."
                details = [
                    {"label": "Type concerne", "value": expense_type},
                    {"label": "Montant converti", "value": deps["fmt_dt_value"](amount)},
                    {"label": "Effet comptable", "value": f"+{deps['fmt_dt_value'](delta_solde)} sur le solde"},
                ]

            else:
                return jsonify({"error": "Scenario non supporte"}), 400

            new_solde = baseline_solde + delta_solde
            impact_percent = (abs(delta_solde) / baseline_ref) * 100.0
            severity, severity_label = deps["impact_severity"](impact_percent)

            return jsonify({
                "success": True,
                "import": {
                    "id": imp["id"],
                    "filename": imp.get("nom_fichier"),
                    "status": imp.get("statut"),
                    "imported_at": imp.get("date_import").isoformat() if imp.get("date_import") else None,
                    "rows_count": imp.get("nb_lignes"),
                },
                "baseline_solde": round(baseline_solde, 2),
                "delta_solde": round(delta_solde, 2),
                "new_solde": round(new_solde, 2),
                "impact_percent": round(impact_percent, 2),
                "severity": severity,
                "headline": headline,
                "recommendation": recommendation,
                "details": details + [
                    {"label": "Niveau d'impact", "value": severity_label},
                    {"label": "Nouveau solde", "value": deps["fmt_dt_value"](new_solde)},
                ],
            })
        except Exception:
            return deps["internal_error_response"]("La simulation n'a pas pu etre calculee.")

    return ml_bp
