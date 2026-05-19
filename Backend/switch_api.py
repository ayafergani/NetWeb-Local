from flask import Blueprint, request, jsonify
from Database.db import get_db_connection
from utils.crypto_utils import encrypt_password, decrypt_password
import psycopg2.extras
from netmiko import ConnectHandler

switch_bp = Blueprint('switch', __name__)

@switch_bp.route("/api/switches", methods=["GET"])
def get_switches():
    """Récupère les switchs pour le tableau de bord (sans MDP)"""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id_switch, reference_id, nom, ip, masque, username, nb_ports, status
            FROM switchs
            ORDER BY id_switch ASC
        """)
        switches = cur.fetchall()
        return jsonify({"success": True, "switches": switches}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

@switch_bp.route("/api/switches", methods=["POST"])
def add_switch():
    """Ajoute un switch de manière sécurisée"""
    data = request.json
    reference = (data.get("reference") or "").strip()
    masque = data.get("masque") or "255.255.255.0"
    nom, ip, username, password = data.get("nom"), data.get("ip"), data.get("username"), data.get("password")

    if not all([nom, ip, username, password]):
        return jsonify({"success": False, "error": "Données incomplètes"}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO switchs (reference_id, nom, ip, masque, username, password, nb_ports, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'UNKNOWN')
            RETURNING id_switch
            """,
            (reference, nom, ip, masque, username, encrypt_password(password), data.get("nb_ports", 24))
        )
        conn.commit()
        return jsonify({"success": True, "message": "Switch ajouté !"}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if 'conn' in locals(): conn.close()

@switch_bp.route("/api/switches/<int:switch_id>/test", methods=["POST"])
def test_switch_connection(switch_id):
    """Test de connexion SSH direct (réutilise ton infrastructure Netmiko)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT ip, username, password FROM switchs WHERE id_switch = %s", (switch_id,))
        switch = cur.fetchone()
        
        if not switch:
            return jsonify({"success": False, "error": "Switch introuvable"}), 404

        # Test SSH léger
        device = {
            'device_type': 'cisco_ios',
            'host': switch["ip"],
            'username': switch["username"],
            'password': decrypt_password(switch["password"]),
            'session_timeout': 10,
            'auth_timeout': 10,
        }
        
        net_connect = ConnectHandler(**device)
        net_connect.disconnect() # Si on arrive ici, le SSH marche !

        cur.execute("UPDATE switchs SET status = 'UP' WHERE id_switch = %s", (switch_id,))
        conn.commit()
        return jsonify({"success": True, "statut": "UP"}), 200

    except Exception as e:
        cur.execute("UPDATE switchs SET status = 'DOWN' WHERE id_switch = %s", (switch_id,))
        conn.commit()
        return jsonify({"success": False, "error": str(e), "statut": "DOWN"}), 400
    finally:
        if 'conn' in locals(): conn.close()
