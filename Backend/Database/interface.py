from flask import Blueprint, jsonify, request
from Database.db import get_db_connection
import psycopg2.extras
import logging

interface_bp = Blueprint("interface", __name__)
logger = logging.getLogger(__name__)


def fetch_existing_vlan_ids(cur):
    cur.execute("SELECT id_vlan FROM vlan")
    return {row[0] for row in cur.fetchall()}


def resolve_vlan_id(vlan_id, available_vlan_ids):
    if vlan_id is None:
        return None
    if vlan_id in available_vlan_ids:
        return vlan_id
    if 1 in available_vlan_ids:
        return 1
    if available_vlan_ids:
        return min(available_vlan_ids)
    return None


def validate_vlan_reference(cur, vlan_id):
    if vlan_id is None:
        return

    cur.execute("SELECT 1 FROM vlan WHERE id_vlan = %s", (vlan_id,))
    if not cur.fetchone():
        raise ValueError(f"Le VLAN {vlan_id} n'existe pas. Creez-le d'abord dans la page VLAN.")


def sync_vlan_ports_from_interfaces(cur, vlan_ids):
    """
    Recalcule vlan.ports a partir des interfaces en mode access.
    Cela garantit que la page VLAN reste synchronisee apres une modification
    faite depuis la page Interfaces.
    """
    normalized_vlan_ids = {
        int(vlan_id) for vlan_id in (vlan_ids or set())
        if vlan_id not in (None, "", "All")
    }

    for vlan_id in normalized_vlan_ids:
        cur.execute("""
            SELECT COALESCE(STRING_AGG(nom, ', ' ORDER BY id_interface), '')
            FROM interface
            WHERE vlan_id = %s AND COALESCE(mode, 'access') = 'access'
        """, (vlan_id,))
        ports_value = cur.fetchone()[0] or ""

        cur.execute("""
            UPDATE vlan
            SET ports = %s
            WHERE id_vlan = %s
        """, (ports_value, vlan_id))


def get_switch_id_by_name(cur, switch_name):
    """
    Récupère l'id_switch à partir du nom du switch.
    ✅ Jointure correcte: nom (string) → switchs → id_switch (int)
    """
    if not switch_name:
        return None
    
    cur.execute(
        "SELECT id_switch FROM switchs WHERE nom = %s",
        (switch_name,)
    )
    result = cur.fetchone()
    if result:
        return result[0]
    
    logger.warning(f"[get_switch_id_by_name] Switch '{switch_name}' introuvable")
    return None


def get_switch_credentials(cur, id_switch):
    """
    Récupère les credentials SSH du switch à partir de son ID.
    Utilisé pour le déploiement SSH.
    """
    if not id_switch:
        return None
    
    cur.execute(
        "SELECT id_switch, nom, ip, username, password FROM switchs WHERE id_switch = %s",
        (id_switch,)
    )
    result = cur.fetchone()
    return result if result else None


def generate_default_interfaces(nb_ports=24):
    """Génère les interfaces par défaut en fonction du nombre de ports du switch"""
    interfaces = []

    # Ports cuivre (type = "access" physique)
    for port_number in range(1, nb_ports + 1):
        is_configured = port_number <= 4 or port_number == 24
        interfaces.append({
            "nom": f"Gi1/0/{port_number}",
            "ip": "192.168.1.10" if port_number == 4 else None,
            "vlan_id": 20 if port_number == 3 else (30 if port_number == 24 else 10 if is_configured else 1),
            "id_switch": None,
            "equipement_id": None, # Pour les terminaux (PCs)
            "status": "UP" if port_number <= 4 else "DOWN",
            "mode": "access",      # Configuration logicielle (access/trunk)
            "type": "access",      # Type physique (access port cuivre)
            "speed": "1Gb" if port_number <= 4 else None,
            "allowed_vlans": None,
            "port_security": port_number <= 3,
            "max_mac": 1,
            "violation_mode": "shutdown",
            "bpdu_guard": True,
        })

    # Ports fibre SFP+ (type = "uplink" physique)
    for port_number in range(1, 5):
        is_configured = port_number <= 2
        interfaces.append({
            "nom": f"Te1/1/{port_number}",
            "ip": None,
            "vlan_id": None if is_configured else 1,
            "id_switch": None,
            "equipement_id": None, # Pour les terminaux (PCs)
            "status": "UP" if port_number == 1 else "DOWN",
            "mode": "trunk" if is_configured else "access",  # Configuration logicielle
            "type": "uplink",      # Type physique (fibre SFP+ uplink)
            "speed": "10Gb" if port_number == 1 else None,
            "allowed_vlans": "all" if is_configured else None,
            "port_security": False,
            "max_mac": 1,
            "violation_mode": "shutdown",
            "bpdu_guard": False,
        })

    return interfaces


def ensure_interface_schema():
    """Vérifie que les colonnes requises existent (sans supprimer les données)"""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'interface'
        """)
        columns = {row[0] for row in cur.fetchall()}

        # Ajouter la colonne type si elle n'existe pas
        if "type" not in columns:
            try:
                cur.execute("""
                    ALTER TABLE interface 
                    ADD COLUMN type VARCHAR(10) DEFAULT 'access'
                """)
                conn.commit()
                logger.info("Colonne interface.type ajoutee (access/uplink)")
            except Exception as alter_error:
                logger.warning(f"Impossible d'ajouter la colonne type: {alter_error}")
                conn.rollback()
        else:
            logger.info("La colonne type existe deja dans la table interface")
            
        # Renommer bpd_u_guard si nécessaire
        if "bpd_u_guard" in columns and "bpdu_guard" not in columns:
            try:
                cur.execute("ALTER TABLE interface RENAME COLUMN bpd_u_guard TO bpdu_guard")
                conn.commit()
                logger.info("Colonne interface.bpd_u_guard renommee en bpdu_guard")
            except Exception as rename_error:
                logger.warning(f"Impossible de renommer la colonne: {rename_error}")
                conn.rollback()
        
        # S'assurer que la colonne id_switch existe pour lier au switch
        if "id_switch" not in columns:
            try:
                cur.execute("ALTER TABLE interface ADD COLUMN id_switch INT REFERENCES switchs(id_switch) ON DELETE CASCADE")
                conn.commit()
                logger.info("Colonne id_switch ajoutee a la table interface")
            except Exception as alter_error:
                logger.warning(f"Impossible d'ajouter la colonne id_switch: {alter_error}")
                conn.rollback()
        
        # Ajouter la colonne static_mac pour le port security
        if "static_mac" not in columns:
            try:
                cur.execute("""
                    ALTER TABLE interface 
                    ADD COLUMN static_mac VARCHAR(17) DEFAULT NULL
                """)
                conn.commit()
                logger.info("Colonne interface.static_mac ajoutee (adresse MAC statique)")
            except Exception as alter_error:
                logger.warning(f"Impossible d'ajouter la colonne static_mac: {alter_error}")
                conn.rollback()
        else:
            logger.info("La colonne static_mac existe deja dans la table interface")

        if "description" not in columns:
            try:
                cur.execute("ALTER TABLE interface ADD COLUMN description TEXT")
                conn.commit()
                logger.info("Colonne interface.description ajoutee")
            except Exception as alter_error:
                logger.warning(f"Impossible d'ajouter la colonne description: {alter_error}")
                conn.rollback()

        if "duplex" not in columns:
            try:
                cur.execute("ALTER TABLE interface ADD COLUMN duplex VARCHAR(32)")
                conn.commit()
                logger.info("Colonne interface.duplex ajoutee")
            except Exception as alter_error:
                logger.warning(f"Impossible d'ajouter la colonne duplex: {alter_error}")
                conn.rollback()
                
    except Exception as e:
        conn.rollback()
        logger.exception("Erreur lors de la verification du schema interface")
    finally:
        conn.close()


def is_table_empty():
    """Vérifie si la table interface est vide"""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM interface")
        count = cur.fetchone()[0]
        return count == 0
    except Exception as e:
        logger.exception("Erreur lors de la verification du contenu de la table")
        return True
    finally:
        conn.close()


def initialize_default_interfaces():
    """
    Parcourt tous les switchs et cree les interfaces par defaut pour ceux qui n'en ont pas.
    """
    ensure_interface_schema()
    
    conn = get_db_connection()
    inserted_count = 0

    try:
        cur = conn.cursor()
        
        cur.execute("SELECT id_switch, nom, nb_ports FROM switchs")
        switches = cur.fetchall()
        
        # Récupération des VLANs existants pour validation
        available_vlan_ids = fetch_existing_vlan_ids(cur)

        # Récupération du prochain ID d'interface disponible
        cur.execute("SELECT COALESCE(MAX(id_interface), 0) FROM interface")
        next_id = cur.fetchone()[0] + 1
        
        for sw_id, sw_nom, sw_nb_ports in switches:
            cur.execute("SELECT COUNT(*) FROM interface WHERE id_switch = %s", (sw_id,))
            if cur.fetchone()[0] > 0:
                continue 

            logger.info(f"Initialisation de {sw_nb_ports} ports pour le switch: {sw_nom}")
            
            switch_interfaces = generate_default_interfaces(sw_nb_ports or 24)

            for item in switch_interfaces:
                resolved_vlan_id = resolve_vlan_id(item["vlan_id"], available_vlan_ids)

                cur.execute("""
                    INSERT INTO interface (
                        id_interface, nom, ip, vlan_id, id_switch, equipement_id, status, mode, type,
                        speed, allowed_vlans, port_security, max_mac, violation_mode, bpdu_guard
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    next_id,
                    item["nom"],
                    item["ip"],
                    resolved_vlan_id,
                    sw_id,
                    None,  # Pas de terminal branché par défaut
                    item["status"],
                    item["mode"],
                    item["type"],
                    item["speed"],
                    item["allowed_vlans"],
                    item["port_security"],
                    item["max_mac"],
                    item["violation_mode"],
                    item["bpdu_guard"]
                ))
                next_id += 1
                inserted_count += 1

        conn.commit()
        logger.info("Initialisation terminee: %s interfaces inserees", inserted_count)
        logger.info("Les modifications peuvent maintenant etre faites via l'interface graphique")
        return inserted_count
    except Exception as e:
        conn.rollback()
        logger.exception("Erreur lors de l'initialisation des interfaces")
        raise
    finally:
        conn.close()


def row_to_interface(row):
    """Convertit une ligne de base de données en dictionnaire"""
    return {
        "id_interface": row["id_interface"],
        "nom": row["nom"],
        "ip": row["ip"],
        "vlan_id": row["vlan_id"],
        "id_switch": row.get("id_switch"),
        "equipement_id": row["equipement_id"],
        "status": row["status"],
        "mode": row["mode"],      # access ou trunk (configuration logicielle)
        "type": row["type"],       # access ou uplink (type physique)
        "speed": row["speed"],
        "duplex": row.get("duplex"),
        "description": row.get("description"),
        "allowed_vlans": row["allowed_vlans"],
        "port_security": row["port_security"],
        "max_mac": row["max_mac"],
        "violation_mode": row["violation_mode"],
        "bpdu_guard": row["bpdu_guard"],
    }


def normalize_interface_payload(data, forced_id=None, cur=None):
    """
    Valide et normalise les données d'une interface.
    Si cur est fourni, effectue les jointures avec switchs et vlan.
    ✅ Jointures correctes:
       - nom du switch (string) → table switchs → id_switch (int)
       - vlan_id (int) → table vlan → validation
    """
    if not isinstance(data, dict):
        raise ValueError("Le corps JSON est invalide")

    # id_interface est optionnel pour la création (POST), mais requis pour la mise à jour (PUT)
    id_interface = None
    if forced_id is not None: # C'est une mise à jour (PUT)
        id_interface = int(forced_id)
    elif data.get("id_interface") is not None:
        try:
            id_interface = int(data["id_interface"])
        except (TypeError, ValueError):
            raise ValueError("id_interface doit etre un entier")

    raw_vlan_id = data.get("vlan_id")
    vlan_id = None if raw_vlan_id in (None, "", "All") else raw_vlan_id
    if vlan_id is not None:
        try:
            vlan_id = int(vlan_id)
        except (TypeError, ValueError):
            raise ValueError("vlan_id doit etre un entier")

    # ✅ JOINTURE 1: Récupérer id_switch à partir du nom du switch
    raw_id_switch = data.get("id_switch")
    id_switch = None
    
    if raw_id_switch is not None:
        try:
            # Si c'est déjà un entier, utiliser directement
            id_switch = int(raw_id_switch)
        except (TypeError, ValueError):
            # Si c'est un string (nom du switch), faire la jointure
            if cur and isinstance(raw_id_switch, str):
                id_switch = get_switch_id_by_name(cur, raw_id_switch)
                if not id_switch:
                    raise ValueError(f"Le switch '{raw_id_switch}' n'existe pas en BDD")
            else:
                raise ValueError("id_switch doit etre un entier ou un nom de switch valide")

    raw_equipement_id = data.get("equipement_id")
    equipement_id = None if raw_equipement_id in (None, "") else raw_equipement_id
    if equipement_id is not None:
        try:
            equipement_id = int(equipement_id)
        except (TypeError, ValueError):
            raise ValueError("equipement_id doit etre un entier")

    raw_max_mac = data.get("max_mac", 1)
    max_mac = 1 if raw_max_mac in (None, "") else raw_max_mac
    try:
        max_mac = int(max_mac)
    except (TypeError, ValueError):
        raise ValueError("max_mac doit etre un entier")

    mode_value = str(data.get("mode", "access")).strip().lower()
    if mode_value not in ("access", "trunk"):
        raise ValueError("mode doit etre 'access' ou 'trunk'")

    type_value = str(data.get("type", "access")).strip().lower()
    if type_value not in ("access", "uplink"):
        raise ValueError("type doit etre 'access' (port cuivre) ou 'uplink' (port fibre SFP+)")

    payload = {
        "id_interface": id_interface, # Sera None pour les créations
        "nom": str(data.get("nom", "")).strip(),
        "ip": str(data.get("ip", "")).strip() or None,
        "vlan_id": vlan_id,
        "id_switch": id_switch,
        "equipement_id": equipement_id,
        "status": str(data.get("status", "DOWN")).strip().upper() or "DOWN",
        "mode": mode_value,
        "type": type_value,
        "speed": str(data.get("speed", "")).strip() or None,
        "duplex": str(data.get("duplex", "")).strip() or None,
        "description": str(data.get("description", "")).strip() or None,
        "allowed_vlans": str(data.get("allowed_vlans", "")).strip() or None,
        "port_security": bool(data.get("port_security", False)),
        "max_mac": max_mac,
        "violation_mode": str(data.get("violation_mode", "shutdown")).strip().lower() or "shutdown",
        "bpdu_guard": bool(data.get("bpdu_guard", False)),
    }

    if not payload["nom"]:
        raise ValueError("Le nom de l'interface est requis")
    if payload["status"] not in ("UP", "DOWN"):
        raise ValueError("status doit etre UP ou DOWN")
    if payload["max_mac"] < 1:
        raise ValueError("max_mac doit etre superieur ou egal a 1")

    return payload


# ==================== ROUTES API ====================

@interface_bp.route("/api/interface", methods=["GET"])
def get_interfaces():
    """
    Récupère toutes les interfaces avec JOIN sur les tables switchs et vlan.
    ✅ Jointures: interface → switchs (id_switch) → nom du switch
                  interface → vlan (vlan_id) → nom du vlan
    """
    id_switch = request.args.get('id_switch') or request.args.get('switch_id')
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # ✅ JOIN complet: interface + switchs + vlan
        query = """
            SELECT 
                i.id_interface, i.nom, i.ip, i.vlan_id, i.id_switch, i.equipement_id, 
                i.status, i.mode, i.type, i.speed, i.allowed_vlans, 
                i.port_security, i.max_mac, i.violation_mode, i.bpdu_guard,
                i.duplex, i.description,
                s.nom as switch_name, s.ip as switch_ip,
                v.nom as vlan_name, v.reseau as vlan_reseau
            FROM interface i
            LEFT JOIN switchs s ON i.id_switch = s.id_switch
            LEFT JOIN vlan v ON i.vlan_id = v.id_vlan
        """
        
        if id_switch:
            query += " WHERE i.id_switch = %s ORDER BY i.id_interface ASC"
            cur.execute(query, (id_switch,))
            logger.info(f"[API] GET interfaces pour switch_id={id_switch} (avec jointures)")
        else:
            query += " ORDER BY i.id_interface ASC"
            cur.execute(query)
            logger.info(f"[API] GET all interfaces (avec jointures)")
            
        rows = cur.fetchall()
        interfaces = [row_to_interface(row) for row in rows]
        logger.debug(f"[API] Retour: {len(interfaces)} interfaces avec infos switches/vlans")
        return jsonify({"success": True, "count": len(interfaces), "interfaces": interfaces})
    except Exception as e:
        logger.exception(f"[API] Erreur GET interfaces")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@interface_bp.route("/api/interface", methods=["POST"])
def create_interface():
    """
    Crée une nouvelle interface (via l'interface graphique).
    ✅ Jointure correcte: id_switch (int) → switchs table → vérification du switch
    """
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Normaliser avec le curseur pour les jointures
        payload = normalize_interface_payload(request.get_json(), cur=cur)
    except ValueError as e:
        logger.warning(f"[API] Erreur validation create_interface: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        if conn and not conn.closed:
            conn.close()

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Vérifier que le nom de l'interface n'existe pas déjà
        cur.execute("SELECT 1 FROM interface WHERE nom = %s", (payload["nom"],))
        if cur.fetchone():
            logger.warning(f"[API] Interface {payload['nom']} existe déjà")
            return jsonify({"success": False, "error": f"L'interface {payload['nom']} existe deja"}), 409

        # Valider le VLAN s'il est fourni
        validate_vlan_reference(cur, payload["vlan_id"])
        
        # Valider que le switch existe
        if payload["id_switch"]:
            cur.execute("SELECT 1 FROM switchs WHERE id_switch = %s", (payload["id_switch"],))
            if not cur.fetchone():
                logger.warning(f"[API] Switch id={payload['id_switch']} introuvable")
                return jsonify({"success": False, "error": f"Le switch {payload['id_switch']} n'existe pas"}), 404

        # Insérer l'interface
        cur.execute("""
            INSERT INTO interface (
                nom, ip, vlan_id, id_switch, equipement_id, status, mode, type,
                speed, duplex, description, allowed_vlans, port_security, max_mac, violation_mode, bpdu_guard
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_interface, nom, ip, vlan_id, id_switch, equipement_id, status, mode, type,
                      speed, duplex, description, allowed_vlans, port_security, max_mac, violation_mode, bpdu_guard
        """, (
            payload["nom"],
            payload["ip"],
            payload["vlan_id"],
            payload["id_switch"],
            payload["equipement_id"],
            payload["status"],
            payload["mode"],
            payload["type"],
            payload["speed"],
            payload["duplex"],
            payload["description"],
            payload["allowed_vlans"],
            payload["port_security"],
            payload["max_mac"],
            payload["violation_mode"],
            payload["bpdu_guard"]
        ))
        row = cur.fetchone()
        sync_vlan_ports_from_interfaces(cur, {payload["vlan_id"]})
        conn.commit()
        logger.info(f"[API] Interface {payload['nom']} créée avec succès (switch_id={payload['id_switch']})")
        
        return jsonify({
            "success": True,
            "message": "Interface creee avec succes",
            "interface": row_to_interface(row),
        }), 201
    except Exception as e:
        conn.rollback()
        logger.exception(f"[API] Erreur create_interface")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@interface_bp.route("/api/interface/<int:interface_id>", methods=["PUT"])
def update_interface(interface_id):
    """
    Met à jour une interface existante (via l'interface graphique).
    ✅ Jointure correcte: id_switch → switchs table
    """
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Normaliser avec le curseur pour les jointures
        payload = normalize_interface_payload(request.get_json() or {}, forced_id=interface_id, cur=cur)
    except ValueError as e:
        logger.warning(f"[API] Erreur validation update_interface: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        if conn and not conn.closed:
            conn.close()

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cur.execute("SELECT vlan_id FROM interface WHERE id_interface = %s", (interface_id,))
        previous_row = cur.fetchone()
        previous_vlan_id = previous_row["vlan_id"] if previous_row else None

        # Valider le VLAN
        validate_vlan_reference(cur, payload["vlan_id"])
        
        # Valider que le switch existe
        if payload["id_switch"]:
            cur.execute("SELECT 1 FROM switchs WHERE id_switch = %s", (payload["id_switch"],))
            if not cur.fetchone():
                logger.warning(f"[API] Switch id={payload['id_switch']} introuvable")
                return jsonify({"success": False, "error": f"Le switch {payload['id_switch']} n'existe pas"}), 404
        
        cur.execute("""
            UPDATE interface
            SET nom = %s,
                ip = %s,
                vlan_id = %s,
                id_switch = %s,
                equipement_id = %s,
                status = %s,
                mode = %s,
                type = %s,
                speed = %s,
                duplex = %s,
                description = %s,
                allowed_vlans = %s,
                port_security = %s,
                max_mac = %s,
                violation_mode = %s,
                bpdu_guard = %s
            WHERE id_interface = %s
            RETURNING id_interface, nom, ip, vlan_id, id_switch, equipement_id, status, mode, type,
                      speed, duplex, description, allowed_vlans, port_security, max_mac, violation_mode, bpdu_guard
        """, (
            payload["nom"],
            payload["ip"],
            payload["vlan_id"],
            payload["id_switch"],
            payload["equipement_id"],
            payload["status"],
            payload["mode"],
            payload["type"],
            payload["speed"],
            payload["duplex"],
            payload["description"],
            payload["allowed_vlans"],
            payload["port_security"],
            payload["max_mac"],
            payload["violation_mode"],
            payload["bpdu_guard"],
            interface_id,
        ))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            logger.warning(f"[API] Interface {interface_id} introuvable pour update")
            return jsonify({"success": False, "error": "Interface introuvable"}), 404

        sync_vlan_ports_from_interfaces(cur, {previous_vlan_id, payload["vlan_id"]})
        conn.commit()
        logger.info(f"[API] Interface {interface_id} mise à jour avec succès (switch_id={payload['id_switch']})")
        return jsonify({
            "success": True,
            "message": f"Interface {interface_id} mise a jour avec succes",
            "interface": row_to_interface(row),
        })
    except Exception as e:
        conn.rollback()
        logger.exception(f"[API] Erreur update_interface")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@interface_bp.route("/api/interface/<int:interface_id>", methods=["DELETE"])
def delete_interface(interface_id):
    """Supprime une interface (via l'interface graphique)"""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            DELETE FROM interface
            WHERE id_interface = %s
            RETURNING id_interface, nom, ip, vlan_id, equipement_id, status, mode, type,
                      speed, allowed_vlans, port_security, max_mac, violation_mode, bpdu_guard
        """, (interface_id,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            logger.warning(f"[API] Interface {interface_id} introuvable pour suppression")
            return jsonify({"success": False, "error": "Interface introuvable"}), 404

        sync_vlan_ports_from_interfaces(cur, {row["vlan_id"]})
        conn.commit()
        logger.info(f"[API] Interface {interface_id} supprimée avec succès")
        return jsonify({
            "success": True,
            "message": f"Interface {interface_id} supprimee avec succes",
            "interface": row_to_interface(row),
        })
    except Exception as e:
        conn.rollback()
        logger.exception(f"[API] Erreur delete_interface")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@interface_bp.route("/api/network/deploy-interface", methods=["POST"])
def deploy_interface():
    """
    Déploie la configuration d'une interface sur le switch via SSH.
    Récupère les credentials SSH depuis la table switchs (via id_switch).
    Si id_switch absent, fallback sur hosts.yaml.
    """
    data = request.get_json() or {}

    interface_name  = data.get("interface_name", "")
    mode            = data.get("mode", "access")
    vlan_id         = data.get("vlan_id")
    status          = data.get("status", "UP")
    port_security   = bool(data.get("port_security", False))
    max_mac         = int(data.get("max_mac", 1))
    violation_mode  = data.get("violation_mode", "shutdown")
    bpdu_guard      = bool(data.get("bpdu_guard", False))
    allowed_vlans   = data.get("allowed_vlans")
    description     = data.get("description")
    static_mac      = data.get("static_mac")
    id_switch       = data.get("id_switch")

    if not interface_name:
        return jsonify({"success": False, "error": "interface_name est requis"}), 400

    # ── Récupérer les credentials SSH depuis la table switchs ─────────────────
    switch_ip = switch_user = switch_password = None
    if id_switch:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT ip, username, password FROM switchs WHERE id_switch = %s",
                (int(id_switch),)
            )
            sw = cur.fetchone()
            if sw:
                switch_ip       = sw["ip"]
                switch_user     = sw["username"]
                switch_password = sw["password"]
                logger.info(f"[deploy-interface] Credentials trouvés pour switch {id_switch} → {switch_ip}")
            else:
                logger.warning(f"[deploy-interface] Switch id={id_switch} introuvable en BDD, fallback hosts.yaml")
        except Exception as e:
            logger.warning(f"[deploy-interface] Erreur récupération switch: {e}, fallback hosts.yaml")
        finally:
            conn.close()

    # ── Déploiement SSH ────────────────────────────────────────────────────────
    try:
        from network.interface_deploy import run_deploy
        result = run_deploy(
            interface_name  = interface_name,
            mode            = mode,
            vlan_id         = int(vlan_id) if vlan_id is not None else 1,
            status          = status,
            port_security   = port_security,
            max_mac         = max_mac,
            violation_mode  = violation_mode,
            bpdu_guard      = bpdu_guard,
            allowed_vlans   = allowed_vlans,
            description     = description,
            static_mac      = static_mac,
            switch_ip       = switch_ip,
            switch_user     = switch_user,
            switch_password = switch_password,
        )
    except ImportError:
        result = {"success": False, "error": "Module network.interface_deploy introuvable", "commands": []}
    except Exception as e:
        result = {"success": False, "error": str(e), "commands": []}

    status_code = 200 if result.get("success") else 500
    return jsonify(result), status_code


@interface_bp.route("/api/interface/reset", methods=["POST"])
def reset_interfaces():
    """Réinitialise les interfaces aux valeurs par défaut (uniquement si demandé explicitement)"""
    # Vérifier les droits admin
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        logger.warning(f"[API] Tentative de reset sans authentification")
        return jsonify({"success": False, "error": "Authentification requise"}), 401
    
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        # Vider la table
        cur.execute("TRUNCATE TABLE interface RESTART IDENTITY")
        conn.commit()
        logger.info("[API] Table interface vidée par demande explicite")
        
        # Fermer la connexion
        conn.close()
        
        # Réinitialiser avec les valeurs par défaut
        initialize_default_interfaces()
        logger.info("[API] Interfaces réinitialisées avec les valeurs par défaut")
        
        return jsonify({
            "success": True,
            "message": "Interfaces reinitialisees avec les valeurs par defaut"
        })
    except Exception as e:
        conn.rollback()
        logger.exception(f"[API] Erreur reset_interfaces")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if not conn.closed:
            conn.close()