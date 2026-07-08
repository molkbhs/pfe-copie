import json
import secrets
from datetime import datetime

from flask import Blueprint, jsonify, request


def create_reports_blueprint(deps):
    reports_bp = Blueprint("reports", __name__)

    @reports_bp.route("/api/reports/export", methods=["POST"])
    @deps["jwt_required"]
    def reports_export():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"success": False, "error": "Auth requis"}), 401

        data = request.get_json(silent=True) or {}
        report_name = (data.get("nom_rapport") or data.get("report_name") or "Rapport Finova").strip()
        report_type = (data.get("type_rapport") or data.get("report_type") or "financier").strip().lower() or "financier"
        report_format = deps["normalize_report_format"](data.get("format") or data.get("report_format") or "")
        file_base64 = data.get("file_base64") or data.get("file")

        if not report_format:
            deps["log_audit"](user_id, "generation_rapport", "reports_export", "failed", {"reason": "Format invalide"})
            return jsonify({"success": False, "error": "Format non supporte (pdf/png/jpg/csv/docx)"}), 400

        generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        summary_obj = data.get("summary") or data.get("kpis") or {}
        fallback_text = json.dumps(
            {
                "title": report_name,
                "type": report_type,
                "generated_at": generated_at,
                "generated_by": deps["get_user_by_id"](user_id) or {"id": user_id},
                "summary": summary_obj,
            },
            ensure_ascii=False,
            indent=2,
        )
        file_bytes = deps["decode_export_file_payload"](file_base64, fallback_text=fallback_text)
        if not file_bytes:
            file_bytes = fallback_text.encode("utf-8")

        safe_stem = deps["safe_report_stem"](report_name, fallback="rapport_finova")
        unique_name = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{user_id}_{secrets.token_hex(4)}_{safe_stem}.{report_format}"
        out_path = deps["REPORTS_FOLDER"] / unique_name

        conn = None
        try:
            conn = deps["get_connection"]()
            cur = conn.cursor()
            deps["ensure_rapports_schema"](cur)
            conn.commit()
            cur.close()
            with open(out_path, "wb") as fh:
                fh.write(file_bytes)
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            deps["log_audit"](user_id, "generation_rapport", "reports_export", "failed", {"error": str(e)})
            return jsonify({"success": False, "error": "Impossible de sauvegarder le fichier de rapport"}), 500
        finally:
            if conn:
                conn.close()

        try:
            report_id = deps["run_update"](
                """
                INSERT INTO rapports (nom_rapport, type_rapport, format, chemin_fichier, created_by, date_generation)
                VALUES (%s, %s, %s, %s, %s, NOW())
                """,
                (report_name, report_type, report_format, unique_name, user_id),
            )
        except Exception as e:
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass
            deps["log_audit"](user_id, "generation_rapport", "reports_export", "failed", {"error": str(e)})
            return jsonify({"success": False, "error": "Impossible d'enregistrer l'historique du rapport"}), 500

        deps["log_audit"](
            user_id,
            "generation_rapport",
            f"report:{report_id}",
            "success",
            {"nom_rapport": report_name, "type_rapport": report_type, "format": report_format, "size_bytes": len(file_bytes)},
        )
        deps["log_audit"](
            user_id,
            "export_rapport",
            f"report:{report_id}",
            "success",
            {"nom_rapport": report_name, "format": report_format},
        )
        return jsonify({
            "success": True,
            "id_rapport": report_id,
            "nom_rapport": report_name,
            "type_rapport": report_type,
            "format": report_format,
            "download_url": f"/api/admin/reports/{report_id}/download",
        }), 201

    return reports_bp
