import json

from flask import Blueprint, jsonify, request


def create_finance_blueprint(deps):
    finance_bp = Blueprint("finance", __name__)

    @finance_bp.route("/api/kpi/save", methods=["POST"])
    @deps["jwt_required"]
    def save_kpis():
        d = request.get_json(force=True) or {}
        kpis = d.get("kpis", [])
        source = d.get("source", "etl")
        do_replace = d.get("replace", False)
        if not kpis:
            return jsonify({"success": False, "error": "Aucun KPI fourni"}), 400

        conn = None
        try:
            conn = deps["get_connection"]()
            cur = conn.cursor()
            inserted = replaced = 0
            for kpi in kpis:
                nom = str(kpi.get("kpiNom", "")).strip()
                per = str(kpi.get("periode", "global")).strip()
                val = float(kpi.get("valeur", 0))
                evo = float(kpi.get("evolution", 0))
                dept = kpi.get("departementId")
                stype = str(kpi.get("stat_type", "sum")).strip()
                if not nom:
                    continue
                if do_replace:
                    cur.execute("DELETE FROM valeur_kpi WHERE kpiNom=%s AND periode=%s", (nom, per))
                    replaced += cur.rowcount
                cur.execute(
                    "INSERT INTO valeur_kpi (kpiNom,periode,valeur,evolution,departementId,source,stat_type) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (nom, per, val, evo, dept, source, stype),
                )
                inserted += 1
            conn.commit()
            cur.close()
            return jsonify({"success": True, "inserted": inserted, "replaced": replaced, "message": f"{inserted} KPI(s) sauvegardes"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            if conn:
                conn.close()

    @finance_bp.route("/api/kpi", methods=["GET"])
    @finance_bp.route("/api/kpis", methods=["GET"])
    @deps["jwt_required"]
    def get_kpis():
        user_id = deps["get_current_user"]()
        if not user_id:
            return jsonify({"error": "Authentification requise"}), 401

        imp, rows = deps["get_latest_import_dataset"](user_id)
        if not imp or not rows:
            return deps["no_import_response"]()

        kpi_nom = (request.args.get("kpiNom") or "").strip().lower()
        periode = (request.args.get("periode") or "").strip().lower()
        limit = max(1, deps["to_int"](request.args.get("limit", 100), 100))

        kpis = deps["build_kpis_from_rows"](rows)
        if kpi_nom:
            kpis = [k for k in kpis if kpi_nom in str(k.get("kpiNom", "")).strip().lower()]
        if periode:
            kpis = [k for k in kpis if str(k.get("periode", "")).strip().lower() == periode]

        kpis = kpis[:limit]
        deps["log_audit"](user_id, "api_access", "kpi_list", "success", {"import_id": imp["id"], "rows": len(kpis)})
        return jsonify({
            "success": True,
            "import_id": imp["id"],
            "kpis": deps["make_json_safe"](kpis),
            "total": len(kpis),
        })

    @finance_bp.route("/api/previsions", methods=["POST"])
    @deps["jwt_required"]
    def create_prevision():
        d = request.get_json(force=True) or {}
        user_id = deps["get_current_user"]()
        type_prev = (d.get("type") or "").strip()
        date_deb = d.get("dateDebut")
        date_fin = d.get("dateFin")
        resultats = d.get("resultats", {})
        dept_id = d.get("departementId")

        if not all([type_prev, date_deb, date_fin]):
            return jsonify({"success": False, "error": "type, dateDebut et dateFin sont requis"}), 400
        conn = None
        try:
            conn = deps["get_connection"]()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO previsions (type,dateDebut,dateFin,resultats,departementId,created_by) VALUES (%s,%s,%s,%s,%s,%s)",
                (type_prev, date_deb, date_fin, json.dumps(resultats, ensure_ascii=False), dept_id, user_id),
            )
            conn.commit()
            new_id = cur.lastrowid
            cur.close()
            return jsonify({"success": True, "id": new_id, "message": f"Prevision #{new_id} creee"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            if conn:
                conn.close()

    @finance_bp.route("/api/previsions", methods=["GET"])
    @deps["jwt_required"]
    def get_previsions():
        dept_id = request.args.get("departementId")
        type_p = request.args.get("type")
        limit = int(request.args.get("limit", 50))
        conn = None
        try:
            conn = deps["get_connection"]()
            cur = conn.cursor(dictionary=True)
            q, p = "SELECT * FROM previsions WHERE 1=1", []
            if dept_id:
                q += " AND departementId=%s"
                p.append(dept_id)
            if type_p:
                q += " AND type=%s"
                p.append(type_p)
            q += " ORDER BY created_at DESC LIMIT %s"
            p.append(limit)
            cur.execute(q, p)
            rows = cur.fetchall()
            cur.close()
            for r in rows:
                for f in ("created_at", "updated_at"):
                    if r.get(f):
                        r[f] = r[f].isoformat()
                for f in ("dateDebut", "dateFin"):
                    if r.get(f):
                        r[f] = str(r[f])
                if isinstance(r.get("resultats"), str):
                    try:
                        r["resultats"] = json.loads(r["resultats"])
                    except Exception:
                        pass
            return jsonify({"success": True, "previsions": rows, "total": len(rows)})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            if conn:
                conn.close()

    @finance_bp.route("/api/previsions/<int:prev_id>", methods=["DELETE"])
    @deps["jwt_required"]
    def delete_prevision(prev_id):
        conn = None
        try:
            conn = deps["get_connection"]()
            cur = conn.cursor()
            cur.execute("DELETE FROM previsions WHERE id=%s", (prev_id,))
            conn.commit()
            deleted = cur.rowcount
            cur.close()
            if deleted:
                return jsonify({"success": True, "message": f"Prevision #{prev_id} supprimee"})
            return jsonify({"success": False, "error": "Prevision non trouvee"}), 404
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            if conn:
                conn.close()

    return finance_bp
