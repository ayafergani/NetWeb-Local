"""
audit_api.py — Traçabilité complète des actions utilisateurs NetGuard
=====================================================================
Ce Blueprint enregistre et expose les événements d'audit en base de données.

Table SQL à créer :
-------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id            SERIAL PRIMARY KEY,
    timestamp     TIMESTAMPTZ DEFAULT NOW(),
    actor         VARCHAR(100) NOT NULL,         -- username de l'auteur
    role          VARCHAR(50),                   -- rôle au moment de l'action
    action_type   VARCHAR(80)  NOT NULL,         -- ex: create_vlan, delete_user...
    module        VARCHAR(50),                   -- ex: VLAN, Users, Switches...
    target        TEXT,                          -- objet affecté (nom, IP, ID...)
    ip_source     VARCHAR(45),                   -- IP du client
    details       JSONB,                         -- données techniques supplémentaires
    result        VARCHAR(10) DEFAULT 'success'  -- success | error
);

Intégration dans app.py :
--------------------------
    from audit_api import audit_bp, log_action
    app.register_blueprint(audit_bp)

Utilisation dans les autres modules :
--------------------------------------
    from audit_api import log_action

    # Dans une route Flask :
    log_action(
        actor    = "username",  # À fournir depuis votre source d'authentification
        role     = session_role,
        action   = "create_vlan",
        module   = "VLAN",
        target   = f"VLAN {vlan_id} — {vlan_name}",
        details  = {"vlan_id": vlan_id, "subnet": subnet},
        result   = "success"
    )
"""

from flask import Blueprint, request, jsonify, g
from Database.db import get_db_connection
import psycopg2.extras
import logging

audit_bp = Blueprint('audit', __name__)
logger   = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  Helper : écrire un événement dans la table audit_logs
# ═══════════════════════════════════════════════════════════════

def log_action(actor: str, action: str, *,
               role: str = None,
               module: str = None,
               target: str = None,
               details: dict = None,
               result: str = "success",
               ip_source: str = None):
    """
    Insère un enregistrement dans audit_logs.
    Silencieux en cas d'erreur pour ne pas bloquer les routes métier.
    """
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO audit_logs
                (actor, role, action_type, module, target, ip_source, details, result)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            actor,
            role,
            action,
            module,
            target,
            ip_source or _get_ip(),
            psycopg2.extras.Json(details or {}),
            result,
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning("audit log_action failed: %s", e)


def _get_ip():
    """Récupère l'IP du client depuis le contexte Flask si disponible."""
    try:
        return request.remote_addr
    except RuntimeError:
        return None


# ═══════════════════════════════════════════════════════════════
#  Routes API
# ═══════════════════════════════════════════════════════════════

@audit_bp.route("/api/audit/logs", methods=["GET"])
def get_audit_logs():
    """
    Retourne les événements d'audit avec filtres optionnels.

    Query params :
        ?limit=50          — nb max de lignes (défaut 100)
        ?actor=admin       — filtrer par acteur
        ?action=create_vlan — filtrer par type d'action
        ?module=VLAN       — filtrer par module
        ?severity=High     — filtrer par sévérité (calculée côté serveur)
    """
    SEVERITY_MAP = {
        "login":           "Info",
        "logout":          "Info",
        "create_user":     "Medium",
        "delete_user":     "High",
        "create_switch":   "Medium",
        "delete_switch":   "High",
        "test_switch":     "Info",
        "create_vlan":     "Medium",
        "delete_vlan":     "High",
        "create_ssh_user": "High",
        "delete_ssh_user": "High",
        "modify_interface":"Medium",
        "activate_rule":   "High",
        "export_logs":     "Info",
    }

    limit  = min(int(request.args.get("limit", 100)), 500)
    actor  = request.args.get("actor")
    action = request.args.get("action")
    module = request.args.get("module")

    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        conditions = []
        params     = []

        if actor:
            conditions.append("actor = %s"); params.append(actor)
        if action:
            conditions.append("action_type = %s"); params.append(action)
        if module:
            conditions.append("module = %s"); params.append(module)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        cur.execute(f"""
            SELECT id, timestamp, actor, role, action_type, module,
                   target, ip_source, details, result
            FROM audit_logs
            {where}
            ORDER BY timestamp DESC
            LIMIT %s
        """, params + [limit])

        rows = cur.fetchall()
        cur.close()
        conn.close()

        events = []
        for i, r in enumerate(rows):
            sev = SEVERITY_MAP.get(r["action_type"], "Info")
            events.append({
                "id":        r["id"],
                "seq":       i + 1,
                "timestamp": r["timestamp"].isoformat() if r["timestamp"] else None,
                "actor":     r["actor"],
                "role":      r["role"] or "",
                "type":      r["action_type"],
                "module":    r["module"] or "",
                "target":    r["target"] or "",
                "ip":        r["ip_source"] or "",
                "details":   r["details"] or {},
                "result":    r["result"] or "success",
                "severity":  sev,
            })

        return jsonify({"success": True, "events": events, "total": len(events)})

    except Exception as e:
        logger.error("GET /api/audit/logs : %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@audit_bp.route("/api/audit/logs/user/<string:username>", methods=["GET"])
def get_user_audit(username):
    """Retourne tous les événements d'un utilisateur spécifique (traçabilité individuelle)."""
    limit = min(int(request.args.get("limit", 50)), 200)
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, timestamp, action_type, module, target, ip_source, details, result
            FROM audit_logs
            WHERE actor = %s
            ORDER BY timestamp DESC
            LIMIT %s
        """, (username, limit))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({
            "success": True,
            "username": username,
            "events": [{
                "id":        r["id"],
                "timestamp": r["timestamp"].isoformat() if r["timestamp"] else None,
                "type":      r["action_type"],
                "module":    r["module"] or "",
                "target":    r["target"] or "",
                "ip":        r["ip_source"] or "",
                "details":   r["details"] or {},
                "result":    r["result"] or "success",
            } for r in rows]
        })
    except Exception as e:
        logger.error("GET /api/audit/user/%s : %s", username, e)
        return jsonify({"success": False, "error": str(e)}), 500


@audit_bp.route("/api/audit/stats", methods=["GET"])
def get_audit_stats():
    """Statistiques globales pour le dashboard des logs."""
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(DISTINCT actor) AS unique_actors,
                SUM(CASE WHEN action_type IN ('delete_user','delete_switch','delete_vlan',
                                               'create_ssh_user','delete_ssh_user','activate_rule')
                         THEN 1 ELSE 0 END) AS high_count,
                SUM(CASE WHEN result = 'error' THEN 1 ELSE 0 END) AS error_count
            FROM audit_logs
        """)
        stats = dict(cur.fetchone())
        cur.close()
        conn.close()
        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        logger.error("GET /api/audit/stats : %s", e)
        return jsonify({"success": False, "error": str(e)}), 500
