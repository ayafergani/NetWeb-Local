import logging
import copy
import os
import re
import threading
import time

import psycopg2
import psycopg2.extras
from flask import Blueprint, jsonify

from Database.alerts import row_to_alert
from Database.db import get_db_connection

dashboard_bp = Blueprint("dashboard", __name__)
logger = logging.getLogger(__name__)
_SUMMARY_CACHE = {"data": None, "expires_at": 0}
_SUMMARY_LOCK = threading.Lock()
_VLAN_CREATED_AT_COLUMN = None
CRITICAL_SEVERITIES = ("critical", "critique", "high", "haute", "élevée", "elevee")
MEDIUM_SEVERITIES = ("medium", "moyen", "moyenne", "moderate", "moderee", "modérée")
LOW_SEVERITIES = ("low", "faible", "basse")


def _empty_summary(error=None):
    payload = {
        "success": True,
        "stats": {
            "total": 0,
            "critical": 0,
            "medium": 0,
            "low": 0,
            "unique_sources": 0,
            "rate_per_minute": 0,
        },
        "recentAlerts": [],
        "trafficStatus": {"status": "Normal", "color": "#22c55e", "bg": "bg-green"},
        "rulesCount": 0,
        "vlanStats": {"count": 0, "lastCreated": None, "quarantine": None},
        "interfaceStats": {
            "total": 0,
            "up": 0,
            "down": 0,
            "securePorts": 0,
            "hasAlert": False,
        },
        "lastTriggeredRule": None,
        "userActivities": [],
    }
    if error:
        payload["warning"] = error
    return payload


def _traffic_status_from_counts(total_count, critical_count, medium_count):
    if critical_count >= 5:
        return {"status": "Critique", "color": "#ef4444", "bg": "bg-red"}
    if critical_count >= 2 or medium_count >= 8:
        return {"status": "Sous surveillance", "color": "#f59e0b", "bg": "bg-amber"}
    if total_count >= 30:
        return {"status": "Activite elevee", "color": "#3b82f6", "bg": "bg-blue"}
    return {"status": "Normal", "color": "#22c55e", "bg": "bg-green"}


def _get_vlan_creation_order(cur):
    global _VLAN_CREATED_AT_COLUMN

    if _VLAN_CREATED_AT_COLUMN is not None:
        return _VLAN_CREATED_AT_COLUMN

    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'vlan'
          AND column_name IN ('created_at', 'date_creation', 'created_on')
        ORDER BY CASE column_name
            WHEN 'created_at' THEN 1
            WHEN 'date_creation' THEN 2
            WHEN 'created_on' THEN 3
            ELSE 4
        END
        LIMIT 1
    """)
    row = cur.fetchone()
    column_name = row.get("column_name") if row else None

    if column_name:
        _VLAN_CREATED_AT_COLUMN = {
            "select": f", {column_name} AS created_at",
            "order": f"{column_name} DESC NULLS LAST, id_vlan DESC",
        }
    else:
        _VLAN_CREATED_AT_COLUMN = {
            "select": ", xmin::text::bigint AS row_version",
            "order": "xmin::text::bigint DESC, id_vlan DESC",
        }

    return _VLAN_CREATED_AT_COLUMN


def clear_dashboard_cache():
    with _SUMMARY_LOCK:
        _SUMMARY_CACHE["data"] = None
        _SUMMARY_CACHE["expires_at"] = 0


@dashboard_bp.route("/api/dashboard/summary", methods=["GET"])
def dashboard_summary():
    now = time.time()
    cached = _SUMMARY_CACHE.get("data")
    if cached and now < _SUMMARY_CACHE.get("expires_at", 0):
        return jsonify(copy.deepcopy(cached))

    with _SUMMARY_LOCK:
        now = time.time()
        cached = _SUMMARY_CACHE.get("data")
        if cached and now < _SUMMARY_CACHE.get("expires_at", 0):
            return jsonify(copy.deepcopy(cached))

        summary = _build_dashboard_summary()
        if summary.get("success") is False:
            if cached:
                stale = copy.deepcopy(cached)
                stale["warning"] = summary.get("error") or summary.get("warning")
                return jsonify(stale)
            return jsonify(summary), 503

        ttl = float(os.getenv("DASHBOARD_CACHE_TTL", "15"))
        _SUMMARY_CACHE["data"] = copy.deepcopy(summary)
        _SUMMARY_CACHE["expires_at"] = time.time() + ttl
        return jsonify(summary)


def _build_dashboard_summary():
    summary = _empty_summary()
    conn = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (
                    WHERE LOWER(TRIM(COALESCE(severity, ''))) = ANY(%s)
                ) AS critical,
                COUNT(*) FILTER (
                    WHERE LOWER(TRIM(COALESCE(severity, ''))) = ANY(%s)
                ) AS medium,
                COUNT(*) FILTER (
                    WHERE LOWER(TRIM(COALESCE(severity, ''))) = ANY(%s)
                ) AS low,
                COUNT(DISTINCT source_ip) AS unique_sources,
                COUNT(*) FILTER (
                    WHERE timestamp >= NOW() - INTERVAL '1 minute'
                ) AS rate_per_minute
            FROM alertes
        """, (list(CRITICAL_SEVERITIES), list(MEDIUM_SEVERITIES), list(LOW_SEVERITIES)))
        summary["stats"] = dict(cur.fetchone() or summary["stats"])

        cur.execute("""
            SELECT id, timestamp, source_ip, destination_ip,
                   attack_type, severity, detection_engine,
                   details, protocol, source_port, destination_port,
                   loss, volume, service
            FROM alertes
            ORDER BY timestamp DESC
            LIMIT 5
        """)
        alerts = [row_to_alert(row) for row in cur.fetchall()]
        summary["recentAlerts"] = alerts
        summary["trafficStatus"] = _traffic_status_from_counts(
            int(summary["stats"].get("total") or 0),
            int(summary["stats"].get("critical") or 0),
            int(summary["stats"].get("medium") or 0),
        )

        cur.execute("SELECT COUNT(*) AS count FROM vlan")
        vlan_count = int((cur.fetchone() or {}).get("count") or 0)
        vlan_creation_order = _get_vlan_creation_order(cur)

        cur.execute(f"""
            SELECT id_vlan, nom, reseau, gateway, type, ports, status,
                   switch_name, switch_ip
                   {vlan_creation_order["select"]}
            FROM vlan
            ORDER BY {vlan_creation_order["order"]}
            LIMIT 1
        """)
        last_vlan = cur.fetchone()

        cur.execute(f"""
            SELECT id_vlan, nom, reseau, gateway, type, ports, status,
                   switch_name, switch_ip
                   {vlan_creation_order["select"]}
            FROM vlan
            WHERE LOWER(COALESCE(status, '')) = 'alert'
            ORDER BY {vlan_creation_order["order"]}
            LIMIT 1
        """)
        quarantine_vlan = cur.fetchone()
        summary["vlanStats"] = {
            "count": vlan_count,
            "lastCreated": dict(last_vlan) if last_vlan else None,
            "quarantine": dict(quarantine_vlan) if quarantine_vlan else None,
        }

        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE UPPER(COALESCE(status, '')) = 'UP') AS up,
                COUNT(*) FILTER (WHERE UPPER(COALESCE(status, '')) <> 'UP') AS down,
                COUNT(*) FILTER (WHERE port_security = TRUE) AS secure_ports,
                BOOL_OR(
                    COALESCE(port_security, FALSE) = FALSE
                    AND UPPER(COALESCE(status, '')) = 'UP'
                ) AS has_alert
            FROM interface
        """)
        interface_row = cur.fetchone() or {}
        summary["interfaceStats"] = {
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
        summary["userActivities"] = [
            {
                "username": row["username"],
                "action": row["action"],
                "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
            }
            for row in cur.fetchall()
        ]

        cur.execute("""
            SELECT id, timestamp, attack_type, details, protocol, severity
            FROM alertes
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        last_alert = cur.fetchone()
        if last_alert:
            summary["lastTriggeredRule"] = {
                "name": last_alert.get("attack_type", "Attaque detectee"),
                "action": "ALERT",
                "description": f"Protocole: {last_alert.get('protocol', 'N/A')}",
                "sid": None,
            }

            sid_match = re.search(r"sid:(\d+)", last_alert.get("details", "") or "")
            if sid_match:
                sid = int(sid_match.group(1))
                summary["lastTriggeredRule"]["sid"] = sid
                cur.execute("""
                    SELECT sid, rule, action, protocol, src_ip, dst_ip
                    FROM regles
                    WHERE sid = %s
                """, (sid,))
                db_rule = cur.fetchone()
                if db_rule:
                    msg_match = re.search(r'msg:"(.*?)"', db_rule.get("rule") or "")
                    summary["lastTriggeredRule"] = {
                        "name": msg_match.group(1) if msg_match else f"Regle SID {sid}",
                        "action": (db_rule.get("action") or "alert").upper(),
                        "description": (
                            f"{(db_rule.get('protocol') or 'TCP').upper()} "
                            f"{db_rule.get('src_ip', 'any')} -> "
                            f"{db_rule.get('dst_ip', 'any')}"
                        ),
                        "sid": sid,
                    }

        try:
            cur.execute("SET LOCAL statement_timeout = '1200ms'")
            cur.execute("SELECT COUNT(*) AS count FROM regles")
            summary["rulesCount"] = int((cur.fetchone() or {}).get("count") or 0)
        except psycopg2.Error as exc:
            logger.warning("rulesCount ignore dans dashboard: %s", exc)
            conn.rollback()

    except Exception as exc:
        logger.exception("Erreur /api/dashboard/summary")
        summary = _empty_summary(str(exc))
        summary["success"] = False
        summary["error"] = str(exc)
    finally:
        if conn:
            conn.close()

    return summary
