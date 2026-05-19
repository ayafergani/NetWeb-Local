from flask import Blueprint, request, jsonify
from Database.db import get_db_connection
from utils.security import hash_password
import psycopg2
import re

users_bp = Blueprint('users', __name__)

class User:
    def __init__(self, id, username, email, password, role):
        self.id = id
        self.username = username
        self.email = email
        self.password = password
        self.role = role

def validate_password(password):
    """Valide que le mot de passe respecte les règles de sécurité."""
    if len(password) < 8:
        return "Le mot de passe doit contenir au moins 8 caractères"
    if not re.search(r'[A-Z]', password):
        return "Le mot de passe doit contenir au moins une majuscule"
    if not re.search(r'[0-9]', password):
        return "Le mot de passe doit contenir au moins un chiffre"
    if not re.search(r'[!@#$%^&*(),.?\":{}|<>_\-+=\[\]\/\\]', password):
        return "Le mot de passe doit contenir au moins un caractère spécial"
    return None  # ✅ Mot de passe valide


# ─── CREATE ───────────────────────────────────────────────────────────────────

@users_bp.route("/users", methods=["POST"])
def create_user():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    email    = data.get("email",    "").strip()
    password = data.get("password", "")
    role     = data.get("role",     "").strip()

    if not all([username, email, password, role]):
        return jsonify({"error": "Données incomplètes"}), 400

    pw_error = validate_password(password)
    if pw_error:
        return jsonify({"error": pw_error}), 400

    hashed_pw = hash_password(password).decode('utf-8')

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO utilisateur (username, email, password, role) VALUES (%s, %s, %s, %s) RETURNING id_user;",
            (username, email, hashed_pw, role)
        )
        new_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        return jsonify({"message": "Utilisateur créé", "id": new_id}), 201
    except psycopg2.IntegrityError:
        if conn:
            conn.rollback()
        return jsonify({"error": "Cet utilisateur ou cet email existe déjà"}), 409
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ─── READ ────────────────────────────────────────────────────────────────────

@users_bp.route("/users", methods=["GET"])
def get_users():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id_user, username, email, role, last_login, last_logout 
            FROM utilisateur
            ORDER BY id_user
        """)
        users = []
        for row in cursor.fetchall():
            users.append({
                "id":          row[0],
                "username":    row[1],
                "email":       row[2],
                "role":        row[3],
                "last_login":  row[4].isoformat() if row[4] else None,
                "last_logout": row[5].isoformat() if row[5] else None,
            })
        cursor.close()
        return jsonify({"users": users}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ─── UPDATE ───────────────────────────────────────────────────────────────────
# ✅ FIX PRINCIPAL : gestion robuste de la connexion + rollback + messages clairs

@users_bp.route("/users/<int:user_id>", methods=["PUT", "PATCH", "POST"], strict_slashes=False)
def update_user(user_id):
    data = request.get_json(silent=True)

    # ✅ Vérifier que le corps JSON est valide
    if not data:
        return jsonify({"error": "Corps JSON manquant ou invalide"}), 400

    username = (data.get("username") or "").strip()
    email    = (data.get("email")    or "").strip()
    role     = (data.get("role")     or "").strip()

    if not all([username, email, role]):
        return jsonify({"error": "Données incomplètes : username, email et role sont requis"}), 400

    # ✅ Valider que le rôle est autorisé
    VALID_ROLES = {"ADMIN", "NETWORK_ADMIN", "SECURITY_ADMIN", "AUDITOR"}
    if role not in VALID_ROLES:
        return jsonify({"error": f"Rôle invalide. Valeurs acceptées : {', '.join(VALID_ROLES)}"}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # ✅ Vérifier que l'utilisateur existe
        cursor.execute("SELECT id_user FROM utilisateur WHERE id_user = %s", (user_id,))
        if not cursor.fetchone():
            return jsonify({"error": f"Utilisateur {user_id} introuvable"}), 404

        # ✅ Mettre à jour
        cursor.execute(
            """
            UPDATE utilisateur
            SET username = %s, email = %s, role = %s
            WHERE id_user = %s
            RETURNING id_user, username, email, role;
            """,
            (username, email, role, user_id)
        )
        updated = cursor.fetchone()
        conn.commit()
        cursor.close()

        if not updated:
            return jsonify({"error": "Mise à jour échouée"}), 500

        return jsonify({
            "message": "Utilisateur mis à jour",
            "user": {
                "id":       updated[0],
                "username": updated[1],
                "email":    updated[2],
                "role":     updated[3],
            }
        }), 200

    except psycopg2.IntegrityError as e:
        if conn:
            conn.rollback()
        return jsonify({"error": "Ce nom d'utilisateur ou cet email est déjà utilisé"}), 409
    except Exception as e:
        if conn:
            conn.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ─── DELETE ───────────────────────────────────────────────────────────────────

@users_bp.route("/users/<int:user_id>", methods=["DELETE"], strict_slashes=False)
def delete_user(user_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM utilisateur WHERE id_user = %s RETURNING id_user;",
            (user_id,)
        )
        deleted = cursor.fetchone()
        conn.commit()
        cursor.close()

        if not deleted:
            return jsonify({"error": "Utilisateur introuvable"}), 404

        return jsonify({"message": "Utilisateur supprimé"}), 200
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ─── ACTIVITY ─────────────────────────────────────────────────────────────────

@users_bp.route("/api/users/activity", methods=["GET"])
def get_user_activity():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT username, last_login, last_logout 
            FROM utilisateur
            WHERE last_login IS NOT NULL OR last_logout IS NOT NULL
        """)
        users = cursor.fetchall()
        cursor.close()

        logs = []
        for user in users:
            username, last_login, last_logout = user[0], user[1], user[2]
            if last_login:
                logs.append({
                    "username":  username,
                    "action":    "login",
                    "timestamp": last_login.isoformat(),
                })
            if last_logout:
                logs.append({
                    "username":  username,
                    "action":    "logout",
                    "timestamp": last_logout.isoformat(),
                })

        logs.sort(key=lambda x: x["timestamp"], reverse=True)
        logs = logs[:10]

        return jsonify({"success": True, "logs": logs}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()