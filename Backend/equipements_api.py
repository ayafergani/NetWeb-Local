from flask import Blueprint, request, jsonify
from Database.db import get_db_connection
from services.switch_sync import sync_switch_state
import psycopg2.extras
import logging

equipements_bp = Blueprint('equipements', __name__)
logger = logging.getLogger(__name__)


def _row_to_switch(row):
    return {
        "id": row["id_switch"],
        "nom": row["nom"],
        "ip": row["ip"],
        "masque": row["masque"] or "",
        "username": row["username"],
        "nb_ports": row["nb_ports"],
        "status": row["status"] or "UNKNOWN",
        "reference": row.get("reference_id", "") or "",
    }


def _row_to_ssh_user(row):
    return {
        "id": row["id_ssh_user"],
        "id_switch": row["id_switch"],
        "username": row["username"],
        "privilege": row["privilege"],
        "nom_switch": row.get("nom_switch", ""),
    }


def _row_to_app_user(row):
    return {
        "id": row["id_user"],
        "username": row["username"],
        "role": row.get("role", ""),
        "email": row.get("email", ""),
    }


@equipements_bp.route("/api/switches", methods=["GET"])
def get_switches():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id_switch, nom, ip, masque, username, password, nb_ports, status, reference_id
            FROM switchs
            ORDER BY nom
            """
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"success": True, "switches": [_row_to_switch(r) for r in rows]})
    except Exception as e:
        logger.error("GET /api/switches : %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@equipements_bp.route("/api/switches", methods=["POST"])
def create_switch():
    data = request.json or {}
    reference = (data.get("reference") or "").strip()
    nom = (data.get("nom") or "").strip()
    ip = (data.get("ip") or "").strip()
    masque = (data.get("masque") or "255.255.255.0").strip()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    nb_ports = int(data.get("nb_ports", 24))

    if not nom or not ip or not username or not password:
        return jsonify({"success": False, "error": "nom, ip, username et password sont requis."}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            INSERT INTO switchs (reference_id, nom, ip, masque, username, password, nb_ports, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'UNKNOWN')
            RETURNING id_switch, nom, ip, masque, username, password, nb_ports, status, reference_id
            """,
            (reference, nom, ip, masque, username, password, nb_ports),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "switch": _row_to_switch(row)}), 201
    except Exception as e:
        logger.error("POST /api/switches : %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@equipements_bp.route("/api/switches/<int:switch_id>", methods=["PUT"])
def update_switch(switch_id):
    data = request.json or {}
    reference = (data.get("reference") or "").strip()
    nom = (data.get("nom") or "").strip()
    ip = (data.get("ip") or "").strip()
    masque = (data.get("masque") or "").strip()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    nb_ports = data.get("nb_ports")

    if not nom or not ip:
        return jsonify({"success": False, "error": "nom et ip sont requis."}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if password:
            cur.execute(
                """
                UPDATE switchs
                SET reference_id=%s, nom=%s, ip=%s, masque=%s, username=%s, password=%s,
                    nb_ports=COALESCE(%s, nb_ports)
                WHERE id_switch=%s
                RETURNING id_switch, nom, ip, masque, username, password, nb_ports, status, reference_id
                """,
                (reference, nom, ip, masque, username, password, nb_ports, switch_id),
            )
        else:
            cur.execute(
                """
                UPDATE switchs
                SET reference_id=%s, nom=%s, ip=%s, masque=%s, username=%s,
                    nb_ports=COALESCE(%s, nb_ports)
                WHERE id_switch=%s
                RETURNING id_switch, nom, ip, masque, username, password, nb_ports, status, reference_id
                """,
                (reference, nom, ip, masque, username, nb_ports, switch_id),
            )

        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"success": False, "error": "Switch introuvable."}), 404
        return jsonify({"success": True, "switch": _row_to_switch(row)})
    except Exception as e:
        logger.error("PUT /api/switches/%s : %s", switch_id, e)
        return jsonify({"success": False, "error": str(e)}), 500


@equipements_bp.route("/api/switches/<int:switch_id>", methods=["DELETE"])
def delete_switch(switch_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM switchs WHERE id_switch=%s RETURNING id_switch", (switch_id,))
        deleted = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        if not deleted:
            return jsonify({"success": False, "error": "Switch introuvable."}), 404
        return jsonify({"success": True, "message": "Switch supprimÃ©."})
    except Exception as e:
        logger.error("DELETE /api/switches/%s : %s", switch_id, e)
        return jsonify({"success": False, "error": str(e)}), 500


@equipements_bp.route("/api/switches/<int:switch_id>/test", methods=["POST"])
def test_switch(switch_id):
    result = sync_switch_state(switch_id)
    status_code = result.pop("status_code", 200 if result.get("success") else 500)
    logger.info("POST /api/switches/%s/test : %s", switch_id, result)
    return jsonify(result), status_code


@equipements_bp.route("/api/equipement-usernames", methods=["GET"])
def get_equipement_usernames():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id_user, username, role, email
            FROM utilisateur
            WHERE username IS NOT NULL AND TRIM(username) <> ''
            ORDER BY username
            """
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"success": True, "users": [_row_to_app_user(r) for r in rows]})
    except Exception as e:
        logger.error("GET /api/equipement-usernames : %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@equipements_bp.route("/api/ssh-users", methods=["GET"])
def get_ssh_users():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT u.id_ssh_user, u.id_switch, u.username, u.privilege,
                   s.nom AS nom_switch
            FROM utilisateurs_ssh u
            LEFT JOIN switchs s ON s.id_switch = u.id_switch
            ORDER BY u.id_ssh_user
            """
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"success": True, "users": [_row_to_ssh_user(r) for r in rows]})
    except Exception as e:
        logger.error("GET /api/ssh-users : %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@equipements_bp.route("/api/ssh-users", methods=["POST"])
def create_ssh_user():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    privilege = int(data.get("privilege", 15))
    deploy_all = bool(data.get("deploy_all", False))
    id_switch = data.get("id_switch")

    if not username or not password:
        return jsonify({"success": False, "error": "username et password sont requis."}), 400

    if not deploy_all and not id_switch:
        return jsonify({"success": False, "error": "id_switch est requis si deploy_all est false."}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("SELECT id_user FROM utilisateur WHERE username=%s", (username,))
        if not cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"success": False, "error": "Ce nom d'utilisateur n'existe pas dans la base."}), 400

        if deploy_all:
            cur.execute("SELECT id_switch FROM switchs")
            switch_ids = [r["id_switch"] for r in cur.fetchall()]
        else:
            switch_ids = [int(id_switch)]

        created = []
        for sid in switch_ids:
            cur.execute(
                """
                INSERT INTO utilisateurs_ssh (id_switch, username, password, privilege)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id_switch, username) DO UPDATE
                    SET password=EXCLUDED.password, privilege=EXCLUDED.privilege
                RETURNING id_ssh_user, id_switch, username, privilege
                """,
                (sid, username, password.encode(), privilege),
            )
            row = cur.fetchone()
            cur.execute("SELECT nom FROM switchs WHERE id_switch=%s", (sid,))
            sw = cur.fetchone()
            row["nom_switch"] = sw["nom"] if sw else ""
            created.append(_row_to_ssh_user(row))

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "users": created}), 201
    except Exception as e:
        logger.error("POST /api/ssh-users : %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@equipements_bp.route("/api/ssh-users/<int:user_id>", methods=["DELETE"])
def delete_ssh_user(user_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM utilisateurs_ssh WHERE id_ssh_user=%s RETURNING id_ssh_user",
            (user_id,),
        )
        deleted = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        if not deleted:
            return jsonify({"success": False, "error": "Utilisateur introuvable."}), 404
        return jsonify({"success": True, "message": "Utilisateur supprimÃ©."})
    except Exception as e:
        logger.error("DELETE /api/ssh-users/%s : %s", user_id, e)
        return jsonify({"success": False, "error": str(e)}), 500