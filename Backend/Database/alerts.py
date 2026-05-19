from flask import Blueprint, jsonify, request
from Database.db import get_db_connection
import copy
import psycopg2.extras
import os
import threading
import time

# â”€â”€â”€ Blueprint (pas de Flask() ici, app.py s'en charge) â”€â”€â”€
alerts_bp = Blueprint("alerts", __name__)
_STATS_CACHE = {"data": None, "expires_at": 0}
_STATS_LOCK = threading.Lock()
CRITICAL_SEVERITIES = ("critical", "critique", "high", "haute", "\u00e9lev\u00e9e", "elevee")
MEDIUM_SEVERITIES = ("medium", "moyen", "moyenne", "moderate", "moderee", "mod\u00e9r\u00e9e")
LOW_SEVERITIES = ("low", "faible", "basse")


def row_to_alert(row):
    src = f"{row['source_ip']}:{row['source_port']}" if row['source_port'] else row['source_ip']
    dst = f"{row['destination_ip']}:{row['destination_port']}" if row['destination_port'] else row['destination_ip']

    sev_raw = (row['severity'] or "").strip().lower()
    if sev_raw in CRITICAL_SEVERITIES:
        sev = "critical"
    elif sev_raw in MEDIUM_SEVERITIES:
        sev = "medium"
    else:
        sev = "low"

    return {
        "id":               row["id"],
        "timestamp":        row["timestamp"].isoformat() if row["timestamp"] else None,
        "src":              src,
        "dst":              dst,
        "source_ip":        row["source_ip"],
        "destination_ip":   row["destination_ip"],
        "source_port":      row["source_port"],
        "destination_port": row["destination_port"],
        "name":             row["attack_type"] or "Unknown",
        "proto":            row["protocol"] or "N/A",
        "severity":         sev,
        "detection_engine": row["detection_engine"],
        "details":          row["details"],
        "loss":             row["loss"],
        "volume":           row["volume"],
        "service":          row["service"],
        "sid":              f"1:{row['id']}",
        "rule":             row["details"] or "",
        "payload":          row["details"] or "",
    }


def analyze_traffic_status(alerts):
    critical_count = sum(1 for alert in alerts if alert.get("severity") == "critical")
    medium_count = sum(1 for alert in alerts if alert.get("severity") == "medium")
    total_count = len(alerts)

    if critical_count >= 5:
        return {"status": "Critique", "color": "#ef4444", "bg": "bg-red"}
    if critical_count >= 2 or medium_count >= 8:
        return {"status": "Sous surveillance", "color": "#f59e0b", "bg": "bg-amber"}
    if total_count >= 30:
        return {"status": "Activite elevee", "color": "#3b82f6", "bg": "bg-blue"}
    return {"status": "Normal", "color": "#22c55e", "bg": "bg-green"}


def clear_stats_cache():
    with _STATS_LOCK:
        _STATS_CACHE["data"] = None
        _STATS_CACHE["expires_at"] = 0


def clear_dashboard_cache():
    try:
        from dashboard_api import clear_dashboard_cache as clear_cache
        clear_cache()
    except Exception:
        pass


@alerts_bp.route("/api/alerts", methods=["GET"])
def get_alerts():
    severity = request.args.get("severity")
    search   = request.args.get("search", "").strip()
    sort     = request.args.get("sort", "newest")   # âœ… dÃ©faut : newest = timestamp DESC
    try:
        limit = int(request.args.get("limit", 100))
        limit = max(1, limit)
    except ValueError:
        limit = 100
    try:
        offset = int(request.args.get("offset", 0))
        offset = max(0, offset)
    except ValueError:
        offset = 0

    import re as _re
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        conditions, params = [], []

        # â”€â”€ Filtre sÃ©vÃ©ritÃ© â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if severity:
            sev_map = {
                "critical": CRITICAL_SEVERITIES,
                "medium":   MEDIUM_SEVERITIES,
                "low":      LOW_SEVERITIES,
            }
            db_values    = sev_map.get(severity, (severity,))
            placeholders = ",".join(["%s"] * len(db_values))
            conditions.append(f"LOWER(TRIM(COALESCE(severity, ''))) IN ({placeholders})")
            params.extend(db_values)

        # â”€â”€ Filtre recherche â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if search:
            # DÃ©tection d'une recherche par date (format YYYY-MM-DD ou YYYY-MM ou YYYY)
            date_match = _re.match(r'^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$', search.strip())
            if date_match:
                yr, mo, dy = date_match.groups()
                if dy:
                    conditions.append("DATE(timestamp) = %s::date")
                    params.append(f"{yr}-{mo}-{dy}")
                elif mo:
                    conditions.append(
                        "EXTRACT(YEAR FROM timestamp) = %s "
                        "AND EXTRACT(MONTH FROM timestamp) = %s"
                    )
                    params.extend([int(yr), int(mo)])
                else:
                    conditions.append("EXTRACT(YEAR FROM timestamp) = %s")
                    params.append(int(yr))
            else:
                conditions.append(
                    "(LOWER(attack_type) LIKE %s OR LOWER(source_ip) LIKE %s "
                    "OR LOWER(destination_ip) LIKE %s OR LOWER(protocol) LIKE %s)"
                )
                like = f"%{search.lower()}%"
                params.extend([like, like, like, like])

        # â”€â”€ Filtre mois (ex: "2026-03") â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        month = request.args.get("month", "").strip()
        if month and _re.match(r'^\d{4}-\d{2}$', month):
            yr, mo = month.split("-")
            conditions.append(
                "EXTRACT(YEAR FROM timestamp) = %s "
                "AND EXTRACT(MONTH FROM timestamp) = %s"
            )
            params.extend([int(yr), int(mo)])

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        # âœ… FIX : ordre de tri
        #    newest  â†’ timestamp DESC  (derniÃ¨re alerte dÃ©clenchÃ©e en PREMIER)
        #    oldest  â†’ timestamp ASC   (premiÃ¨re alerte en premier)
        #    sev     â†’ sÃ©vÃ©ritÃ© puis timestamp DESC
        order_map = {
            "newest": "timestamp DESC",
            "oldest": "timestamp ASC",
            "sev":    "severity ASC, timestamp DESC",
        }
        order_clause = order_map.get(sort, "timestamp DESC")   # fallback sÃ©curisÃ©

        # â”€â”€ Comptage total (pour la pagination) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        count_query = f"SELECT COUNT(*) AS total FROM alertes {where_clause}"
        cur.execute(count_query, params)
        total_row = cur.fetchone()
        total     = int(total_row["total"]) if total_row else 0

        # â”€â”€ RequÃªte principale â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        query = f"""
            SELECT id, timestamp, source_ip, destination_ip,
                   attack_type, severity, detection_engine,
                   details, protocol, source_port, destination_port,
                   loss, volume, service
            FROM alertes
            {where_clause}
            ORDER BY {order_clause}
            LIMIT %s OFFSET %s
        """
        params.append(limit)
        params.append(offset)
        cur.execute(query, params)
        rows   = cur.fetchall()
        alerts = [row_to_alert(r) for r in rows]

        return jsonify({
            "success": True,
            "count":   len(alerts),
            "total":   total,
            "limit":   limit,
            "offset":  offset,
            "alerts":  alerts,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@alerts_bp.route("/api/alerts", methods=["POST"])
def create_alert():
    data = request.get_json(silent=True) or {}

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            INSERT INTO alertes (
                timestamp, source_ip, destination_ip,
                attack_type, severity, detection_engine,
                details, protocol, source_port, destination_port
            )
            VALUES (
                COALESCE(%s::timestamp, NOW()), %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s
            )
            RETURNING id, timestamp, source_ip, destination_ip,
                      attack_type, severity, detection_engine,
                      details, protocol, source_port, destination_port,
                      loss, volume, service
        """, (
            data.get("timestamp"),
            data.get("source_ip"),
            data.get("destination_ip"),
            data.get("attack_type") or "Unknown",
            data.get("severity")    or "inconnue",
            data.get("detection_engine") or "Snort",
            data.get("details") or data.get("attack_type") or "",
            data.get("protocol")     or "N/A",
            data.get("source_port"),
            data.get("destination_port"),
        ))
        row = cur.fetchone()
        conn.commit()
        clear_stats_cache()
        clear_dashboard_cache()
        return jsonify({"success": True, "alert": row_to_alert(row)}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@alerts_bp.route("/api/alerts/recent", methods=["GET"])
def get_recent_alerts():
    """
    Retourne les alertes des N derniÃ¨res minutes.
    âœ… TriÃ©es par timestamp DESC : la plus rÃ©cente en premier.
    """
    try:
        minutes = int(request.args.get("minutes", 1))
    except ValueError:
        minutes = 1

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, timestamp, source_ip, destination_ip,
                   attack_type, severity, detection_engine,
                   details, protocol, source_port, destination_port,
                   loss, volume, service
            FROM alertes
            WHERE timestamp >= NOW() - INTERVAL '%s minutes'
            ORDER BY timestamp DESC        -- âœ… derniÃ¨re alerte en premier
            LIMIT 100
        """, (minutes,))
        rows   = cur.fetchall()
        alerts = [row_to_alert(r) for r in rows]
        return jsonify({"success": True, "count": len(alerts), "alerts": alerts})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@alerts_bp.route("/api/alerts/<int:alert_id>", methods=["GET"])
def get_alert(alert_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, timestamp, source_ip, destination_ip,
                   attack_type, severity, detection_engine,
                   details, protocol, source_port, destination_port,
                   loss, volume, service
            FROM alertes WHERE id = %s
        """, (alert_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Alerte introuvable"}), 404
        return jsonify({"success": True, "alert": row_to_alert(row)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@alerts_bp.route("/api/stats", methods=["GET"])
def get_stats():
    now = time.time()
    cached = _STATS_CACHE.get("data")
    if cached and now < _STATS_CACHE.get("expires_at", 0):
        return jsonify(copy.deepcopy(cached))

    with _STATS_LOCK:
        now = time.time()
        cached = _STATS_CACHE.get("data")
        if cached and now < _STATS_CACHE.get("expires_at", 0):
            return jsonify(copy.deepcopy(cached))

        return _build_stats_response()


def _build_stats_response():
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT
                COUNT(*)                                                                AS total,
                COUNT(*) FILTER (
                    WHERE LOWER(TRIM(COALESCE(severity, ''))) = ANY(%s)
                )                                                                       AS critical,
                COUNT(*) FILTER (
                    WHERE LOWER(TRIM(COALESCE(severity, ''))) = ANY(%s)
                )                                                                       AS medium,
                COUNT(*) FILTER (
                    WHERE LOWER(TRIM(COALESCE(severity, ''))) = ANY(%s)
                )                                                                       AS low,
                COUNT(DISTINCT source_ip)                                               AS unique_sources,
                COUNT(*) FILTER (WHERE timestamp >= NOW() - INTERVAL '1 minute')       AS rate_per_minute
            FROM alertes
        """, (list(CRITICAL_SEVERITIES), list(MEDIUM_SEVERITIES), list(LOW_SEVERITIES)))
        row = cur.fetchone()
        payload = {"success": True, "stats": dict(row)}
        ttl = float(os.getenv("STATS_CACHE_TTL", "3"))
        _STATS_CACHE["data"] = copy.deepcopy(payload)
        _STATS_CACHE["expires_at"] = time.time() + ttl
        return jsonify(payload)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@alerts_bp.route("/api/dashboard/summary-legacy", methods=["GET"])
def get_dashboard_summary():
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT
                COUNT(*)                                                                AS total,
                COUNT(*) FILTER (
                    WHERE LOWER(TRIM(COALESCE(severity, ''))) = ANY(%s)
                )                                                                       AS critical,
                COUNT(*) FILTER (
                    WHERE LOWER(TRIM(COALESCE(severity, ''))) = ANY(%s)
                )                                                                       AS medium,
                COUNT(*) FILTER (
                    WHERE LOWER(TRIM(COALESCE(severity, ''))) = ANY(%s)
                )                                                                       AS low,
                COUNT(DISTINCT source_ip)                                               AS unique_sources,
                COUNT(*) FILTER (WHERE timestamp >= NOW() - INTERVAL '1 minute')       AS rate_per_minute
            FROM alertes
        """, (list(CRITICAL_SEVERITIES), list(MEDIUM_SEVERITIES), list(LOW_SEVERITIES)))
        stats = dict(cur.fetchone() or {})

        cur.execute("""
            SELECT id, timestamp, source_ip, destination_ip,
                   attack_type, severity, detection_engine,
                   details, protocol, source_port, destination_port,
                   loss, volume, service
            FROM alertes
            ORDER BY timestamp DESC
            LIMIT 200
        """)
        alerts = [row_to_alert(row) for row in cur.fetchall()]

        cur.execute("SELECT COUNT(*) AS count FROM regles")
        rules_count = int((cur.fetchone() or {}).get("count") or 0)

        cur.execute("""
            SELECT id_vlan, nom, reseau, gateway, type, ports, status, switch_name, switch_ip
            FROM vlan
            ORDER BY id_vlan DESC
        """)
        vlans = [dict(row) for row in cur.fetchall()]
        quarantine_vlan = next(
            (row for row in vlans if str(row.get("status") or "").lower() == "alert"),
            None
        )

        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE UPPER(COALESCE(status, '')) = 'UP') AS up,
                COUNT(*) FILTER (WHERE UPPER(COALESCE(status, '')) <> 'UP') AS down,
                COUNT(*) FILTER (WHERE port_security = TRUE) AS secure_ports,
                BOOL_OR(COALESCE(port_security, FALSE) = FALSE AND UPPER(COALESCE(status, '')) = 'UP') AS has_alert
            FROM interface
        """)
        interface_row = cur.fetchone() or {}
        interface_stats = {
            "total": int(interface_row.get("total") or 0),
            "up": int(interface_row.get("up") or 0),
            "down": int(interface_row.get("down") or 0),
            "securePorts": int(interface_row.get("secure_ports") or 0),
            "hasAlert": bool(interface_row.get("has_alert") or False),
        }

        cur.execute("""
            SELECT username, action, timestamp
            FROM (
                SELECT username, 'login' AS action, last_login AS timestamp
                FROM utilisateur
                WHERE last_login IS NOT NULL
                UNION ALL
                SELECT username, 'logout' AS action, last_logout AS timestamp
                FROM utilisateur
                WHERE last_logout IS NOT NULL
            ) activity
            ORDER BY timestamp DESC
            LIMIT 10
        """)
        user_activities = [
            {
                "username": row["username"],
                "action": row["action"],
                "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
            }
            for row in cur.fetchall()
        ]

        cur.execute("""
            SELECT
                id,
                timestamp,
                attack_type,
                details,
                protocol,
                severity
            FROM alertes
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        last_alert = cur.fetchone()
        last_triggered_rule = None
        if last_alert:
            import re

            details = last_alert.get("details", "")
            sid_match = re.search(r"sid:(\d+)", details)
            last_triggered_rule = {
                "name": last_alert.get("attack_type", "Attaque detectee"),
                "action": "ALERT",
                "description": f"Protocole: {last_alert.get('protocol', 'N/A')}",
                "sid": None,
            }

            if sid_match:
                sid = int(sid_match.group(1))
                last_triggered_rule["sid"] = sid

                cur.execute("""
                    SELECT sid, rule, action, protocol, src_ip, dst_ip
                    FROM regles
                    WHERE sid = %s
                """, (sid,))
                db_rule = cur.fetchone()

                if db_rule:
                    msg_match = re.search(r'msg:"(.*?)"', db_rule["rule"] or "")
                    last_triggered_rule["name"] = msg_match.group(1) if msg_match else f"Regle SID {sid}"
                    last_triggered_rule["action"] = (db_rule.get("action") or "alert").upper()
                    last_triggered_rule["description"] = (
                        f"{(db_rule.get('protocol') or 'TCP').upper()} "
                        f"{db_rule.get('src_ip', 'any')} -> {db_rule.get('dst_ip', 'any')}"
                    )

        return jsonify({
            "success": True,
            "stats": stats,
            "recentAlerts": alerts[:5],
            "trafficStatus": analyze_traffic_status(alerts),
            "rulesCount": rules_count,
            "vlanStats": {
                "count": len(vlans),
                "lastCreated": vlans[0] if vlans else None,
                "quarantine": quarantine_vlan,
            },
            "interfaceStats": interface_stats,
            "lastTriggeredRule": last_triggered_rule,
            "userActivities": user_activities,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@alerts_bp.route("/api/regles/by-message", methods=["GET"])
def get_regle_by_message():
    """Cherche dans la table regles par le champ message (correspondant Ã  attack_type)."""
    message = request.args.get("message", "").strip()
    if not message:
        return jsonify({"success": False, "error": "ParamÃ¨tre 'message' manquant"}), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, sid, message, protocol, src_ip, src_port,
                   dst_ip, dst_port, action, rule
            FROM regles
            WHERE LOWER(message) = LOWER(%s)
            LIMIT 1
        """, (message,))
        row = cur.fetchone()
        if not row:
            return jsonify({"success": True, "found": False, "regle": None})
        return jsonify({"success": True, "found": True, "regle": dict(row)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@alerts_bp.route("/api/last-triggered-rule", methods=["GET"])
def get_last_triggered_rule():
    """
    RÃ©cupÃ¨re la derniÃ¨re alerte dÃ©clenchÃ©e (timestamp DESC LIMIT 1)
    et retourne les infos de la rÃ¨gle Snort correspondante.
    âœ… ORDER BY timestamp DESC garantit qu'on lit bien la DERNIÃˆRE alerte.
    """
    import re
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # âœ… timestamp DESC â†’ on rÃ©cupÃ¨re la derniÃ¨re alerte dÃ©clenchÃ©e
        cur.execute("""
            SELECT
                id,
                timestamp,
                attack_type,
                details,
                protocol,
                severity
            FROM alertes
            ORDER BY timestamp DESC        -- derniÃ¨re alerte en premier
            LIMIT 1
        """)
        last_alert = cur.fetchone()

        if not last_alert:
            return jsonify({
                "success":   True,
                "has_alert": False,
                "rule":      None,
                "message":   "Aucune alerte dÃ©tectÃ©e",
            })

        # Extraire le SID depuis le champ details
        details   = last_alert.get("details", "")
        sid_match = re.search(r'sid:(\d+)', details)

        rule_info = {
            "name":        last_alert.get("attack_type", "Attaque dÃ©tectÃ©e"),
            "action":      "ALERT",
            "description": f"Protocole: {last_alert.get('protocol', 'N/A')}",
            "sid":         None,
        }

        if sid_match:
            sid = int(sid_match.group(1))
            rule_info["sid"] = sid

            cur.execute("""
                SELECT sid, rule, action, protocol, src_ip, dst_ip
                FROM regles
                WHERE sid = %s
            """, (sid,))
            db_rule = cur.fetchone()

            if db_rule:
                msg_match = re.search(r'msg:"(.*?)"', db_rule["rule"] or "")
                rule_info["name"]        = msg_match.group(1) if msg_match else f"RÃ¨gle SID {sid}"
                rule_info["action"]      = (db_rule.get("action") or "alert").upper()
                rule_info["description"] = (
                    f"{(db_rule.get('protocol') or 'TCP').upper()} "
                    f"{db_rule.get('src_ip', 'any')} â†’ {db_rule.get('dst_ip', 'any')}"
                )

        return jsonify({
            "success":         True,
            "has_alert":       True,
            "rule":            rule_info,
            "alert_timestamp": (
                last_alert["timestamp"].isoformat()
                if last_alert["timestamp"] else None
            ),
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

