import re
import unicodedata
from collections import defaultdict

import requests
from flask import Blueprint, jsonify, request


def _normalize_question_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text.lower()).strip()
    return text


def _safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _is_analysis_question(question_norm: str) -> bool:
    analysis_markers = (
        "analyse",
        "analyser",
        "recommand",
        "tendance",
        "expliquer",
        "interpret",
        "amelior",
        "amélior",
        "strategie",
        "stratégie",
        "decision",
        "décision",
        "pourquoi",
    )
    return any(marker in question_norm for marker in analysis_markers)


def _is_simple_local_question(question_norm: str) -> bool:
    if not question_norm:
        return True
    if _is_analysis_question(question_norm):
        return False

    local_markers = (
        "combien de transactions",
        "nb transactions",
        "nombre de transactions",
        "total revenus",
        "revenus totaux",
        "total depenses",
        "depenses totales",
        "solde net",
        "marge",
        "meilleur departement",
        "plus grande depense",
        "donnees disponibles",
        "données disponibles",
        "nombre d imports",
        "nombre d'import",
        "combien d imports",
        "combien d'import",
        "nombre imports",
    )
    return any(marker in question_norm for marker in local_markers)


def _is_import_count_question(question_norm: str) -> bool:
    has_import_word = "import" in question_norm
    has_count_word = any(token in question_norm for token in ("combien", "nombre", "nb"))
    return has_import_word and has_count_word


def _extract_period(row) -> str:
    period = str(row.get("year_month") or row.get("YearMonth") or "").strip()
    if re.match(r"^\d{4}-\d{2}$", period):
        return period
    date_val = str(row.get("date_val") or row.get("Date") or "").strip()
    if re.match(r"^\d{4}-\d{2}", date_val):
        return date_val[:7]
    return ""


def _build_import_count_answer(user_id, deps) -> str | None:
    run_query = deps.get("run_query")
    if callable(run_query) and user_id:
        try:
            row = run_query(
                "SELECT COUNT(*) AS n FROM historique_imports WHERE user_id=%s",
                (user_id,),
                True,
            ) or {"n": 0}
            count = int(row.get("n") or 0)
            return f"Vous avez {count} import(s) enregistre(s) dans l'historique."
        except Exception:
            return None
    return None


def _build_compact_financial_summary(user_id, deps) -> dict:
    dataset_loader = deps.get("get_active_user_dataset")
    rows = []
    if callable(dataset_loader):
        try:
            dataset = dataset_loader(user_id) or {}
            if dataset.get("success"):
                rows = dataset.get("rows") or []
        except Exception:
            rows = []

    if rows:
        revenus = 0.0
        depenses = 0.0
        solde = 0.0
        by_department = defaultdict(float)
        by_expense_type = defaultdict(float)
        periods = []

        for row in rows:
            signed = _safe_float(row.get("Montant_Signe"), 0.0)
            montant = abs(_safe_float(row.get("Montant"), abs(signed)))
            if signed >= 0:
                revenus += signed
            else:
                depenses += abs(signed)
            solde += signed

            dep_name = str(row.get("departement") or "Non renseigne").strip() or "Non renseigne"
            by_department[dep_name] += signed

            type_depense = str(row.get("type_depense") or "N/A").strip() or "N/A"
            tx_type = str(row.get("type_transaction") or "").strip().lower()
            if signed < 0:
                by_expense_type[type_depense] += abs(signed)
            elif "depense" in tx_type:
                by_expense_type[type_depense] += montant

            period = _extract_period(row)
            if period:
                periods.append(period)

        top_departements = sorted(
            by_department.items(),
            key=lambda item: abs(item[1]),
            reverse=True,
        )[:3]
        top_depenses = sorted(
            by_expense_type.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:3]

        period_coverage = "N/A"
        if periods:
            sorted_periods = sorted(set(periods))
            period_coverage = f"{sorted_periods[0]} -> {sorted_periods[-1]}"

        marge = (solde / revenus * 100.0) if revenus else 0.0
        return {
            "has_data": True,
            "total_transactions": int(len(rows)),
            "revenus_totaux": round(revenus, 2),
            "depenses_totales": round(depenses, 2),
            "solde_net": round(solde, 2),
            "marge_pct": round(marge, 2),
            "top_departements": [
                {"departement": name, "solde_net": round(value, 2)}
                for name, value in top_departements
            ],
            "top_types_depenses": [
                {"type_depense": name, "montant": round(value, 2)}
                for name, value in top_depenses
            ],
            "periode_couverte": period_coverage,
        }

    context = deps["get_financial_context"](user_id) or {}
    return {
        "has_data": False,
        "total_transactions": int(context.get("total_transactions") or 0),
        "revenus_totaux": round(_safe_float(context.get("revenus_totaux"), 0.0), 2),
        "depenses_totales": round(_safe_float(context.get("depenses_totales"), 0.0), 2),
        "solde_net": round(_safe_float(context.get("solde_net"), 0.0), 2),
        "marge_pct": round(
            (_safe_float(context.get("solde_net"), 0.0) / _safe_float(context.get("revenus_totaux"), 1.0) * 100.0)
            if _safe_float(context.get("revenus_totaux"), 0.0) else 0.0,
            2,
        ),
        "top_departements": (context.get("top_departements") or [])[:3],
        "top_types_depenses": [],
        "periode_couverte": "N/A",
    }


def _compact_summary_to_text(summary: dict) -> str:
    if not summary.get("total_transactions"):
        return (
            "Aucune donnee exploitable n'est disponible pour cet utilisateur. "
            "Aucun import valide n'a ete detecte."
        )

    lines = [
        f"Transactions: {int(summary.get('total_transactions') or 0)}",
        f"Revenus: {float(summary.get('revenus_totaux') or 0):.2f} DT",
        f"Depenses: {float(summary.get('depenses_totales') or 0):.2f} DT",
        f"Solde net: {float(summary.get('solde_net') or 0):.2f} DT",
        f"Marge: {float(summary.get('marge_pct') or 0):.2f}%",
        f"Periode couverte: {summary.get('periode_couverte') or 'N/A'}",
    ]

    top_deps = summary.get("top_departements") or []
    if top_deps:
        deps_txt = ", ".join(
            f"{d.get('departement')}: {float(d.get('solde_net') or 0):.2f} DT"
            for d in top_deps[:3]
        )
        lines.append(f"Top departements: {deps_txt}")

    top_types = summary.get("top_types_depenses") or []
    if top_types:
        types_txt = ", ".join(
            f"{t.get('type_depense')}: {float(t.get('montant') or 0):.2f} DT"
            for t in top_types[:3]
        )
        lines.append(f"Top types de depenses: {types_txt}")

    return "\n".join(lines)


def create_chatbot_blueprint(deps):
    chatbot_bp = Blueprint("chatbot", __name__)

    @chatbot_bp.route("/api/chatbot/ask", methods=["POST"])
    @deps["jwt_required"]
    def chatbot_ask():
        try:
            payload = request.get_json(silent=True) or {}
            question = str(payload.get("question") or payload.get("message") or "").strip()
            user_id = deps["get_current_user"]()
            question_norm = _normalize_question_text(question)
            if _is_import_count_question(question_norm):
                import_answer = _build_import_count_answer(user_id, deps)
                if import_answer:
                    return jsonify({"success": True, "answer": import_answer})
            answer = deps["generate_classic_chatbot_answer"](user_id, question)
            return jsonify({"success": True, "answer": answer})
        except Exception:
            return jsonify({
                "success": True,
                "answer": "Je n'ai pas pu repondre pour le moment. Verifiez vos donnees puis reessayez.",
            })

    @chatbot_bp.route("/api/chatbot/ask-ai", methods=["POST"])
    @deps["jwt_required"]
    def chatbot_ask_ai():
        payload = request.get_json(silent=True) or {}
        question = str(payload.get("question") or payload.get("message") or "").strip()
        if not question:
            return jsonify({"success": False, "error": "Question vide"}), 400

        user_id = deps["get_current_user"]()
        question_norm = _normalize_question_text(question)
        if _is_simple_local_question(question_norm):
            import_answer = _build_import_count_answer(user_id, deps) if _is_import_count_question(question_norm) else None
            local_answer = import_answer or deps["generate_classic_chatbot_answer"](user_id, question)
            return jsonify({"success": True, "answer": local_answer, "model": "local", "fallback": False})

        compact_summary = _build_compact_financial_summary(user_id, deps)
        context_text = _compact_summary_to_text(compact_summary)
        system_prompt = (
            deps["SYSTEM_PROMPT"]
            + "\n\n"
            + "Contrainte de reponse: utilise uniquement le resume compact fourni. "
            + "Ne mentionne jamais de valeurs non presentes dans ce resume."
        )

        body = {
            "model": deps["OLLAMA_MODEL"],
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Question utilisateur: {question}\n\n"
                        f"Resume financier compact:\n{context_text}\n\n"
                        "Reponds en francais, de facon concise, orientee action."
                    ),
                },
            ],
            "options": {"temperature": 0.2, "num_predict": 120},
        }

        try:
            resp = requests.post(deps["OLLAMA_CHAT_URL"], json=body, timeout=25)
        except requests.exceptions.Timeout:
            fallback_answer = deps["generate_classic_chatbot_answer"](user_id, question)
            return jsonify({
                "success": True,
                "answer": f"Ollama est lent pour le moment. Voici une reponse locale: {fallback_answer}",
                "model": "local",
                "fallback": True,
            })
        except requests.exceptions.ConnectionError:
            fallback_answer = deps["generate_classic_chatbot_answer"](user_id, question)
            return jsonify({
                "success": True,
                "answer": f"Ollama est indisponible. Voici une reponse locale: {fallback_answer}",
                "model": "local",
                "fallback": True,
            })
        except Exception:
            fallback_answer = deps["generate_classic_chatbot_answer"](user_id, question)
            return jsonify({
                "success": True,
                "answer": f"Erreur temporaire IA. Voici une reponse locale: {fallback_answer}",
                "model": "local",
                "fallback": True,
            })

        if resp.status_code >= 400:
            err_msg = "Erreur Ollama"
            try:
                err_json = resp.json() or {}
                err_msg = str(err_json.get("error") or err_json.get("message") or err_msg)
            except Exception:
                err_msg = f"Erreur Ollama (HTTP {resp.status_code})"
            fallback_answer = deps["generate_classic_chatbot_answer"](user_id, question)
            return jsonify({
                "success": True,
                "answer": f"{err_msg}. Voici une reponse locale: {fallback_answer}",
                "model": "local",
                "fallback": True,
            })

        try:
            data = resp.json() or {}
        except Exception:
            fallback_answer = deps["generate_classic_chatbot_answer"](user_id, question)
            return jsonify({
                "success": True,
                "answer": f"Reponse IA invalide. Voici une reponse locale: {fallback_answer}",
                "model": "local",
                "fallback": True,
            })

        answer = str((data.get("message") or {}).get("content") or data.get("response") or "").strip()
        if not answer:
            fallback_answer = deps["generate_classic_chatbot_answer"](user_id, question)
            return jsonify({
                "success": True,
                "answer": f"Ollama n'a pas retourne de reponse exploitable. Reponse locale: {fallback_answer}",
                "model": "local",
                "fallback": True,
            })

        return jsonify({"success": True, "answer": answer, "model": deps["OLLAMA_MODEL"]})

    @chatbot_bp.route("/api/ollama/status", methods=["GET"])
    def ollama_status():
        available_models = []
        running = False

        try:
            resp = requests.get(f"{deps['OLLAMA_BASE_URL']}/api/tags", timeout=8)
            running = resp.status_code < 500
            if resp.ok:
                payload = resp.json() or {}
                models = payload.get("models") or []
                available_models = [str(m.get("name")) for m in models if m.get("name")]
        except Exception:
            running = False
            available_models = []

        configured = deps["OLLAMA_MODEL"]
        selected = configured if configured in available_models else None
        if not selected and available_models:
            configured_family = configured.split(":")[0]
            selected = next((m for m in available_models if m.split(":")[0] == configured_family), available_models[0])

        return jsonify({
            "running": running,
            "available_models": available_models,
            "selected_model": selected,
            "configured_model": configured,
        })

    return chatbot_bp
