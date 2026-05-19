from flask import Blueprint, jsonify, request
from Database.db import get_db_connection
import ipaddress
import logging
import os
import psycopg2.extras
import re
import threading
import yaml

vlan_bp = Blueprint("vlan", __name__)
logger = logging.getLogger(__name__)
HOSTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "network", "hosts.yaml")
_VLAN_COLUMNS = None
_VLAN_COLUMNS_LOCK = threading.Lock()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def get_vlan_columns(conn):
    global _VLAN_COLUMNS

    if _VLAN_COLUMNS is not None:
        return _VLAN_COLUMNS

    with _VLAN_COLUMNS_LOCK:
        if _VLAN_COLUMNS is not None:
            return _VLAN_COLUMNS

    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'vlan'
        """)
        _VLAN_COLUMNS = {row[0] for row in cur.fetchall()}
        return _VLAN_COLUMNS
    finally:
        cur.close()


def derive_network_from_gateway(gateway):
    if not gateway:
        return ""
    try:
        return str(ipaddress.ip_network(f"{gateway}/24", strict=False))
    except ValueError:
        return ""


def split_port_list(ports):
    if not ports:
        return []
    return [item.strip() for item in str(ports).split(",") if item.strip()]


def normalize_interface_name(name):
    value = re.sub(r"\s+", "", str(name or "")).lower()

    gig_match = re.match(r"^(gigabitethernet|gig|gi|g)(\d+/\d+/\d+)$", value)
    if gig_match:
        return f"gi{gig_match.group(2)}"

    ten_gig_match = re.match(r"^(tengigabitethernet|tengig|te|t)(\d+/\d+/\d+)$", value)
    if ten_gig_match:
        return f"te{ten_gig_match.group(2)}"

    fast_match = re.match(r"^(fastethernet|fa|f)(\d+/\d+)$", value)
    if fast_match:
        return f"fa{fast_match.group(2)}"

    return value


def resolve_switch_id(cur, payload):
    if payload.get("id_switch") is not None:
        return payload["id_switch"]

    switch_name = (payload.get("switch_name") or "").strip()
    if not switch_name:
        return None

    cur.execute("SELECT id_switch FROM switchs WHERE nom = %s", (switch_name,))
    row = cur.fetchone()
    if not row:
        return None
    return row.get("id_switch") if hasattr(row, "get") else row[0]


def validate_port_assignments(cur, ports, id_switch=None):
    requested_ports = split_port_list(ports)
    if not requested_ports:
        return ""

    if id_switch is not None:
        cur.execute("SELECT nom FROM interface WHERE id_switch = %s", (id_switch,))
    else:
        cur.execute("SELECT nom FROM interface")

    rows = cur.fetchall()
    existing_ports = {}
    for row in rows:
        interface_name = row.get("nom") if hasattr(row, "get") else row[0]
        existing_ports[normalize_interface_name(interface_name)] = interface_name

    if not existing_ports:
        scope = f" pour le switch {id_switch}" if id_switch is not None else ""
        raise ValueError(f"Aucune interface n'existe dans la table interface{scope}.")

    invalid_ports = [
        port for port in requested_ports
        if normalize_interface_name(port) not in existing_ports
    ]
    if invalid_ports:
        raise ValueError(
            "Interface(s) inexistante(s) dans la table interface : "
            + ", ".join(invalid_ports)
        )

    return ", ".join(
        existing_ports[normalize_interface_name(port)]
        for port in requested_ports
    )


def clear_dashboard_cache():
    try:
        from dashboard_api import clear_dashboard_cache as clear_cache
        clear_cache()
    except Exception:
        logger.debug("Cache dashboard non invalide", exc_info=True)


def get_fallback_vlan_id(cur, excluded_vlan_id=None):
    if excluded_vlan_id is None:
        cur.execute("""
            SELECT id_vlan
            FROM vlan
            ORDER BY CASE WHEN id_vlan = 1 THEN 0 ELSE 1 END, id_vlan
            LIMIT 1
        """)
    else:
        cur.execute("""
            SELECT id_vlan
            FROM vlan
            WHERE id_vlan <> %s
            ORDER BY CASE WHEN id_vlan = 1 THEN 0 ELSE 1 END, id_vlan
            LIMIT 1
        """, (excluded_vlan_id,))

    row = cur.fetchone()
    return row.get("id_vlan") if hasattr(row, "get") else (row[0] if row else None)


def sync_interfaces_with_vlan_ports(cur, vlan_id, ports, id_switch=None):
    """
    Propage vlan.ports vers la table interface.
    Les ports listes recoivent ce VLAN; les anciens ports lies a ce VLAN
    sont remis sur un VLAN de repli (VLAN 1 si disponible).
    """
    requested_ports = {
        normalize_interface_name(port): port
        for port in split_port_list(ports)
    }
    fallback_vlan_id = get_fallback_vlan_id(cur, excluded_vlan_id=vlan_id)

    if id_switch is not None:
        cur.execute("""
            SELECT id_interface, nom, vlan_id, id_switch
            FROM interface
            WHERE id_switch = %s OR vlan_id = %s
        """, (id_switch, vlan_id))
    else:
        cur.execute("""
            SELECT id_interface, nom, vlan_id, id_switch
            FROM interface
            WHERE vlan_id = %s
        """, (vlan_id,))

    for row in cur.fetchall():
        interface_id = row["id_interface"]
        normalized_name = normalize_interface_name(row["nom"])
        same_switch = id_switch is None or row.get("id_switch") == id_switch
        should_assign = same_switch and normalized_name in requested_ports

        if should_assign:
            cur.execute("""
                UPDATE interface
                SET vlan_id = %s,
                    mode = 'access',
                    allowed_vlans = NULL
                WHERE id_interface = %s
            """, (vlan_id, interface_id))
        elif row["vlan_id"] == vlan_id:
            cur.execute("""
                UPDATE interface
                SET vlan_id = %s,
                    allowed_vlans = CASE
                        WHEN COALESCE(mode, 'access') = 'trunk' THEN allowed_vlans
                        ELSE NULL
                    END
                WHERE id_interface = %s
            """, (fallback_vlan_id, interface_id))


def sync_vlan_table_from_interfaces(cur, vlan_ids=None):
    """
    Recalcule vlan.ports depuis la table interface pour corriger aussi les
    anciennes donnees deja presentes en base.
    """
    normalized_vlan_ids = {
        int(vlan_id) for vlan_id in (vlan_ids or [])
        if vlan_id not in (None, "", "All")
    }

    if normalized_vlan_ids:
        cur.execute("""
            SELECT id_vlan
            FROM vlan
            WHERE id_vlan = ANY(%s)
            ORDER BY id_vlan ASC
        """, (list(normalized_vlan_ids),))
    else:
        cur.execute("""
            SELECT id_vlan
            FROM vlan
            ORDER BY id_vlan ASC
        """)

    vlan_rows = cur.fetchall()
    for row in vlan_rows:
        current_vlan_id = row.get("id_vlan") if hasattr(row, "get") else row[0]
        cur.execute("""
            SELECT COALESCE(STRING_AGG(nom, ', ' ORDER BY id_interface), '') AS ports_value
            FROM interface
            WHERE vlan_id = %s
              AND COALESCE(mode, 'access') = 'access'
        """, (current_vlan_id,))
        ports_value = cur.fetchone()
        ports_value = (ports_value.get("ports_value") if hasattr(ports_value, "get") else ports_value[0]) or ""

        cur.execute("""
            UPDATE vlan
            SET ports = %s
            WHERE id_vlan = %s
        """, (ports_value, current_vlan_id))


def build_vlan_response(row):
    gateway = row.get("gateway") or ""
    device_count = row.get("device_count")
    if device_count is None:
        device_count = row.get("devices")
    if device_count is None:
        device_count = 0

    return {
        "id_vlan":    row.get("id_vlan"),
        "id":         row.get("id_vlan"),
        "nom":        row.get("nom"),
        "name":       row.get("nom"),
        "reseau":     row.get("reseau") or "",
        "gateway":    gateway,
        "vlanIp":     gateway or "--",
        "type":       row.get("type") or "Data",
        "ports":      row.get("ports_display") or row.get("ports") or "",
        "status":     row.get("status") or "Active",
        "switchName": row.get("switch_name") or "",
        "switchIp":   row.get("switch_ip") or "",
        "devices":    device_count,
    }


def normalize_mac_address(mac):
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", str(mac or ""))
    if len(cleaned) != 12:
        return ""
    cleaned = cleaned.upper()
    return ":".join(cleaned[i:i + 2] for i in range(0, 12, 2))


def parse_mac_table_output(output, vlan_id):
    devices = []
    seen_macs = set()
    vlan_text = str(vlan_id)
    vlan_prefix_re = re.compile(rf"^\*?\s*{re.escape(vlan_text)}\s+", re.IGNORECASE)
    interface_re = re.compile(r"\b((?:Gi|GigabitEthernet|Fa|FastEthernet|Te|TenGigabitEthernet|Eth|Ethernet|Po|Port-channel)\S+)\b", re.IGNORECASE)

    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if not line or not vlan_prefix_re.search(line):
            continue

        mac_match = re.search(r"([0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}|([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}|([0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}", line)
        if not mac_match:
            continue

        mac_address = normalize_mac_address(mac_match.group(0))
        if not mac_address or mac_address in seen_macs:
            continue

        interface_match = interface_re.search(line)
        interface_name = interface_match.group(1) if interface_match else ""
        if not interface_name:
            continue

        seen_macs.add(mac_address)
        devices.append({
            "nom": f"Device {mac_address[-5:].replace(':', '')}",
            "type": "Unknown",
            "adresse_mac": mac_address,
            "utilisateur": "",
            "interface": interface_name,
            "source": "switch-scan",
        })

    return devices


def get_switch_credentials_for_vlan(cur, id_vlan):
    cur.execute("""
        SELECT
            v.id_vlan,
            v.nom AS vlan_nom,
            v.switch_name,
            s.id_switch,
            s.nom AS switch_nom,
            s.ip,
            s.username,
            s.password
        FROM vlan v
        LEFT JOIN switchs s
            ON s.nom = v.switch_name
        WHERE v.id_vlan = %s
    """, (id_vlan,))
    return cur.fetchone()


def load_switch_profiles_from_hosts():
    if not os.path.exists(HOSTS_FILE):
        return []

    with open(HOSTS_FILE, "r", encoding="utf-8") as file_handle:
        hosts_data = yaml.safe_load(file_handle) or {}

    profiles = []
    for host_key, host_val in hosts_data.items():
        extras = ((host_val.get("connection_options") or {}).get("netmiko") or {}).get("extras", {})
        profiles.append({
            "profile_name": host_key,
            "host": host_val.get("hostname") or host_val.get("host") or "",
            "username": host_val.get("username") or "",
            "password": host_val.get("password") or "",
            "secret": extras.get("secret", "") or "",
            "port": int(host_val.get("port", 22) or 22),
            "device_type": "cisco_ios",
            "source": "hosts.yaml",
        })
    return profiles


def build_switch_connection_profiles(switch_row):
    profiles = []

    password = switch_row.get("password") or ""
    if isinstance(password, memoryview):
        password = password.tobytes().decode(errors="ignore")
    elif isinstance(password, bytes):
        password = password.decode(errors="ignore")

    if switch_row.get("ip") and switch_row.get("username"):
        profiles.append({
            "profile_name": switch_row.get("switch_nom") or switch_row.get("switch_name") or "switch-db",
            "host": switch_row["ip"],
            "username": switch_row["username"],
            "password": password,
            "secret": "",
            "port": 22,
            "device_type": "cisco_ios",
            "source": "database",
        })

    for host_profile in load_switch_profiles_from_hosts():
        if switch_row.get("ip") and host_profile["host"] != switch_row.get("ip"):
            continue
        if not any(
            existing["host"] == host_profile["host"] and existing["username"] == host_profile["username"]
            for existing in profiles
        ):
            profiles.append(host_profile)

    return profiles


def scan_vlan_devices_from_switch(switch_row, vlan_id):
    try:
        from netmiko import ConnectHandler
    except ImportError as exc:
        raise RuntimeError("Netmiko n'est pas installe sur le backend.") from exc

    if not switch_row:
        raise RuntimeError("VLAN introuvable.")
    if not switch_row.get("ip") or not switch_row.get("username"):
        raise RuntimeError("Les informations SSH du switch sont incomplètes.")

    password = switch_row.get("password") or ""
    if isinstance(password, memoryview):
        password = password.tobytes().decode(errors="ignore")
    elif isinstance(password, bytes):
        password = password.decode(errors="ignore")

    device = {
        "device_type": "cisco_ios",
        "host": switch_row["ip"],
        "username": switch_row["username"],
        "password": password,
        "port": 22,
    }

    net_connect = ConnectHandler(**device)
    try:
        output = net_connect.send_command(f"show mac address-table vlan {vlan_id}")
    finally:
        net_connect.disconnect()

    return parse_mac_table_output(output, vlan_id)


def scan_vlan_devices_from_switch(switch_row, vlan_id):
    try:
        from netmiko import ConnectHandler
    except ImportError as exc:
        raise RuntimeError("Netmiko n'est pas installe sur le backend.") from exc

    if not switch_row:
        raise RuntimeError("VLAN introuvable.")

    profiles = build_switch_connection_profiles(switch_row)
    if not profiles:
        raise RuntimeError("Aucun profil SSH exploitable n'a ete trouve pour ce switch.")

    commands = [
        f"show mac address-table vlan {vlan_id}",
        f"show mac address-table dynamic vlan {vlan_id}",
    ]
    errors = []
    last_output = ""

    for profile in profiles:
        device = {
            "device_type": profile["device_type"],
            "host": profile["host"],
            "username": profile["username"],
            "password": profile["password"],
            "port": profile["port"],
            "secret": profile.get("secret", ""),
        }

        try:
            net_connect = ConnectHandler(**device)
            try:
                if device.get("secret"):
                    net_connect.enable()

                for command in commands:
                    output = net_connect.send_command(command)
                    last_output = output
                    devices = parse_mac_table_output(output, vlan_id)
                    if devices:
                        return devices
            finally:
                net_connect.disconnect()
        except Exception as exc:
            errors.append(f"{profile['source']}:{profile['host']} -> {exc}")

    if errors:
        raise RuntimeError(" | ".join(errors))

    output_tail = " ".join(str(last_output or "").splitlines()[-4:]).strip()
    if output_tail:
        raise RuntimeError(f"Aucune MAC detectee pour le VLAN {vlan_id}. Sortie switch: {output_tail}")
    raise RuntimeError(f"Aucune MAC detectee pour le VLAN {vlan_id}.")


def scan_vlan_devices_from_switch(switch_row, vlan_id):
    commands = [
        f"show mac address-table vlan {vlan_id}",
        f"show mac address-table dynamic vlan {vlan_id}",
    ]

    nornir_errors = []
    last_output = ""

    try:
        from network.deploy_vlan import build_nornir

        nr = build_nornir()
        for host in nr.inventory.hosts.values():
            try:
                conn = host.get_connection("netmiko", nr.config)
                for command in commands:
                    output = conn.send_command(command)
                    last_output = output
                    devices = parse_mac_table_output(output, vlan_id)
                    if devices:
                        return devices
            except Exception as exc:
                nornir_errors.append(f"nornir:{host.name} -> {exc}")
    except Exception as exc:
        nornir_errors.append(f"nornir-init -> {exc}")

    try:
        from netmiko import ConnectHandler
    except ImportError as exc:
        raise RuntimeError("Netmiko n'est pas installe sur le backend.") from exc

    if not switch_row:
        raise RuntimeError("VLAN introuvable.")

    profiles = build_switch_connection_profiles(switch_row)
    if not profiles and nornir_errors:
        raise RuntimeError(" | ".join(nornir_errors))
    if not profiles:
        raise RuntimeError("Aucun profil SSH exploitable n'a ete trouve pour ce switch.")

    profile_errors = []

    for profile in profiles:
        device = {
            "device_type": profile["device_type"],
            "host": profile["host"],
            "username": profile["username"],
            "password": profile["password"],
            "port": profile["port"],
            "secret": profile.get("secret", ""),
        }

        try:
            net_connect = ConnectHandler(**device)
            try:
                if device.get("secret"):
                    net_connect.enable()

                for command in commands:
                    output = net_connect.send_command(command)
                    last_output = output
                    devices = parse_mac_table_output(output, vlan_id)
                    if devices:
                        return devices
            finally:
                net_connect.disconnect()
        except Exception as exc:
            profile_errors.append(f"{profile['source']}:{profile['host']} -> {exc}")

    all_errors = [*nornir_errors, *profile_errors]
    if all_errors:
        raise RuntimeError(" | ".join(all_errors))

    output_tail = " ".join(str(last_output or "").splitlines()[-4:]).strip()
    if output_tail:
        raise RuntimeError(f"Aucune MAC detectee pour le VLAN {vlan_id}. Sortie switch: {output_tail}")
    raise RuntimeError(f"Aucune MAC detectee pour le VLAN {vlan_id}.")


def sync_scanned_devices(cur, vlan_id, devices):
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'equipement'
    """)
    equipement_columns = {
        row["column_name"] if hasattr(row, "keys") else row[0]
        for row in cur.fetchall()
    }
    required_columns = {"nom", "type", "vlan_id", "utilisateur", "adresse_mac"}
    if not required_columns.issubset(equipement_columns):
        missing_columns = ", ".join(sorted(required_columns - equipement_columns))
        raise RuntimeError(f"Colonnes manquantes dans equipement : {missing_columns}")

    synced_devices = []
    for device in devices:
        mac_address = device.get("adresse_mac")
        scanned_name = (device.get("nom") or "").strip()
        scanned_type = (device.get("type") or "").strip()
        cur.execute("""
            SELECT id_eq, nom, type, vlan_id, utilisateur, adresse_mac
            FROM equipement
            WHERE vlan_id = %s AND adresse_mac = %s
            LIMIT 1
        """, (vlan_id, mac_address))
        existing = cur.fetchone()

        if existing:
            final_name = existing["nom"] or (scanned_name if scanned_name and scanned_name.lower() != "device inconnu" else "")
            final_type = existing["type"] or (scanned_type if scanned_type and scanned_type.lower() != "unknown" else "")
            cur.execute("""
                UPDATE equipement
                SET nom = COALESCE(NULLIF(%s, ''), nom),
                    type = COALESCE(NULLIF(%s, ''), type),
                    vlan_id = %s
                WHERE id_eq = %s
                RETURNING id_eq, nom, type, vlan_id, utilisateur, adresse_mac
            """, (
                final_name or "",
                final_type or "",
                vlan_id,
                existing["id_eq"],
            ))
            row = cur.fetchone()
        else:
            final_name = scanned_name if scanned_name and scanned_name.lower() != "device inconnu" else f"Device {mac_address[-5:].replace(':', '')}"
            final_type = scanned_type if scanned_type and scanned_type.lower() != "unknown" else "Unknown"
            cur.execute("""
                INSERT INTO equipement (nom, type, vlan_id, utilisateur, adresse_mac)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id_eq, nom, type, vlan_id, utilisateur, adresse_mac
            """, (
                final_name,
                final_type,
                vlan_id,
                device.get("utilisateur") or None,
                mac_address,
            ))
            row = cur.fetchone()

        synced_devices.append({
            "id_eq": row["id_eq"],
            "nom": row["nom"],
            "type": row["type"],
            "vlan_id": row["vlan_id"],
            "utilisateur": row.get("utilisateur") or "",
            "adresse_mac": row["adresse_mac"],
            "interface": device.get("interface") or "",
            "source": device.get("source") or "database",
        })

    return synced_devices


def load_devices_from_database(cur, vlan_id):
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'equipement'
    """)
    equipement_columns = {
        row["column_name"] if hasattr(row, "keys") else row[0]
        for row in cur.fetchall()
    }
    required_columns = {"id_eq", "nom", "type", "vlan_id", "utilisateur", "adresse_mac"}
    if not required_columns.issubset(equipement_columns):
        return []

    cur.execute("""
        SELECT id_eq, nom, type, vlan_id, utilisateur, adresse_mac
        FROM equipement
        WHERE vlan_id = %s
        ORDER BY id_eq ASC
    """, (vlan_id,))
    rows = cur.fetchall()
    return [
        {
            "id_eq": row["id_eq"],
            "nom": row["nom"],
            "type": row["type"],
            "vlan_id": row["vlan_id"],
            "utilisateur": row.get("utilisateur") or "",
            "adresse_mac": row["adresse_mac"],
            "interface": "",
            "source": "database",
        }
        for row in rows
    ]


def normalize_vlan_payload(data, forced_vlan_id=None):
    if not isinstance(data, dict):
        raise ValueError("Le corps JSON est invalide")

    raw_id = forced_vlan_id if forced_vlan_id is not None else data.get("id_vlan", data.get("id", data.get("vlan_id")))
    try:
        id_vlan = int(raw_id)
    except (TypeError, ValueError):
        raise ValueError("id_vlan doit être un entier")

    gateway = str(data.get("gateway", data.get("vlanIp", data.get("vlan_ip", "")))).strip()
    reseau  = str(data.get("reseau", "")).strip()
    if not reseau and gateway:
        reseau = derive_network_from_gateway(gateway)

    raw_id_switch = data.get("id_switch", data.get("switch_id"))
    id_switch = None if raw_id_switch in (None, "", "all") else raw_id_switch
    if id_switch is not None:
        try:
            id_switch = int(id_switch)
        except (TypeError, ValueError):
            raise ValueError("id_switch doit etre un entier")

    payload = {
        "id_vlan":     id_vlan,
        "nom":         str(data.get("nom", data.get("name", data.get("vlan_name", "")))).strip(),
        "reseau":      reseau,
        "gateway":     gateway,
        "type":        str(data.get("type", "Data")).strip() or "Data",
        "ports":       str(data.get("ports", "")).strip(),
        "status":      str(data.get("status", "Active")).strip() or "Active",
        "switch_name": str(data.get("switchName", data.get("switch_name", ""))).strip(),
        "switch_ip":   str(data.get("switchIp",   data.get("switch_ip",   ""))).strip(),
        "id_switch":   id_switch,
    }

    if not payload["nom"]:
        raise ValueError("Le nom du VLAN est requis")

    if payload["gateway"]:
        try:
            ipaddress.ip_address(payload["gateway"])
        except ValueError:
            raise ValueError("gateway doit être une adresse IP valide")

    if payload["reseau"]:
        try:
            ipaddress.ip_network(payload["reseau"], strict=False)
        except ValueError:
            raise ValueError("reseau doit être au format CIDR, ex: 192.168.10.0/24")

    return payload


def get_returning_fields(columns):
    fields = ["id_vlan", "nom", "reseau", "gateway", "type", "ports", "status"]
    fields.append("switch_name" if "switch_name" in columns else "NULL AS switch_name")
    fields.append("switch_ip"   if "switch_ip"   in columns else "NULL AS switch_ip")
    return fields


# ─── Routes CRUD ─────────────────────────────────────────────────────────────

@vlan_bp.route("/api/vlan", methods=["GET"])
@vlan_bp.route("/vlan",     methods=["GET"])
def get_vlans():
    filter_switch_name = request.args.get("switch_name", "").strip()
    filter_switch_id   = request.args.get("switch_id", "").strip()

    conn = get_db_connection()
    try:
        columns = get_vlan_columns(conn)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        where_clauses = []
        params = []

        if filter_switch_name:
            where_clauses.append("LOWER(TRIM(COALESCE(switch_name, ''))) = LOWER(TRIM(%s))")
            params.append(filter_switch_name)
        elif filter_switch_id:
            cur2 = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur2.execute("SELECT nom FROM switchs WHERE id_switch = %s", (filter_switch_id,))
            sw_row = cur2.fetchone()
            cur2.close()
            if sw_row:
                switch_name = (sw_row.get("nom") or "").strip()
                if switch_name:
                    where_clauses.append("LOWER(TRIM(COALESCE(switch_name, ''))) = LOWER(TRIM(%s))")
                    params.append(switch_name)
                else:
                    where_clauses.append("1 = 0")
            else:
                where_clauses.append("1 = 0")

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        cur.execute(f"""
            SELECT
                {", ".join(get_returning_fields(columns))},
                COALESCE((
                    SELECT STRING_AGG(port_rows.nom, ', ' ORDER BY port_rows.nom)
                    FROM (
                        SELECT DISTINCT i.nom
                        FROM interface i
                        LEFT JOIN switchs s_if ON s_if.id_switch = i.id_switch
                        WHERE i.vlan_id = vlan.id_vlan
                          AND COALESCE(i.mode, 'access') = 'access'
                          AND (
                              COALESCE(TRIM(vlan.switch_name), '') = ''
                              OR LOWER(TRIM(COALESCE(s_if.nom, ''))) = LOWER(TRIM(COALESCE(vlan.switch_name, '')))
                          )
                    ) port_rows
                ), vlan.ports, '') AS ports_display,
                COALESCE(eq.device_count, 0) AS device_count
            FROM vlan
            LEFT JOIN (
                SELECT vlan_id, COUNT(*) AS device_count
                FROM equipement
                GROUP BY vlan_id
            ) eq ON eq.vlan_id = vlan.id_vlan
            {where_sql}
            ORDER BY id_vlan ASC
        """, params)
        rows  = cur.fetchall()
        vlans = [build_vlan_response(row) for row in rows]
        return jsonify({"success": True, "count": len(vlans), "vlans": vlans})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@vlan_bp.route("/api/switchs", methods=["GET"])
@vlan_bp.route("/switchs",     methods=["GET"])
def get_switchs():
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id_switch, nom, ip, status
            FROM switchs
            ORDER BY nom ASC
        """)
        rows = cur.fetchall()
        switchs = [
            {
                "id":     row["id_switch"],
                "nom":    row["nom"],
                "ip":     row["ip"],
                "status": row.get("status") or "Active",
            }
            for row in rows
        ]
        return jsonify({"success": True, "switchs": switchs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


# ─── Create VLAN (BDD + déploiement SSH automatique) ─────────────────────────

@vlan_bp.route("/api/vlan/<int:id_vlan>/devices", methods=["GET"])
@vlan_bp.route("/vlan/<int:id_vlan>/devices",     methods=["GET"])
def get_vlan_devices(id_vlan):
    refresh = request.args.get("refresh", "1").strip().lower() not in {"0", "false", "no"}

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        switch_row = get_switch_credentials_for_vlan(cur, id_vlan)
        if not switch_row:
            return jsonify({"success": False, "error": "VLAN introuvable"}), 404

        devices = []
        scanned_devices = []
        source = "database"
        warning = None

        if refresh:
            try:
                scanned_devices = scan_vlan_devices_from_switch(switch_row, id_vlan)
                devices = scanned_devices
                source = "switch-scan"
                try:
                    synced_devices = sync_scanned_devices(cur, id_vlan, scanned_devices)
                    conn.commit()
                    if synced_devices:
                        devices = synced_devices
                        source = "switch-scan+database"
                except Exception as sync_error:
                    conn.rollback()
                    warning = f"Scan reussi, mais synchro base impossible: {sync_error}"
                source = "switch-scan"
            except Exception as scan_error:
                conn.rollback()
                logger.warning("Scan du VLAN %s impossible: %s", id_vlan, scan_error)
                warning = str(scan_error)

        if not devices:
            devices = load_devices_from_database(cur, id_vlan)

        response = {
            "success": True,
            "vlan_id": id_vlan,
            "vlan_name": switch_row.get("vlan_nom") or "",
            "switch_name": switch_row.get("switch_nom") or switch_row.get("switch_name") or "",
            "devices": devices,
            "count": len(devices),
            "source": source if devices else "database",
        }
        if warning:
            response["warning"] = warning
        return jsonify(response)
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@vlan_bp.route("/api/vlan",                    methods=["POST"])
@vlan_bp.route("/vlan",                        methods=["POST"])
@vlan_bp.route("/api/network/deploy-vlan",     methods=["POST"])
def create_vlan():
    """
    Crée un VLAN en base de données ET le déploie sur le switch via SSH.
    Ordre : SSH d'abord → BDD ensuite (atomique).
    Si le déploiement SSH échoue, la BDD n'est PAS modifiée.
    """
    try:
        payload = normalize_vlan_payload(request.get_json())
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400

    # ── Pré-validation BDD (sans commit) ─────────────────────────────────────
    # On valide les données et prépare l'INSERT AVANT d'aller sur le switch.
    # Le commit n'aura lieu qu'après le succès SSH.
    conn = get_db_connection()
    try:
        columns = get_vlan_columns(conn)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("SELECT 1 FROM vlan WHERE id_vlan = %s", (payload["id_vlan"],))
        if cur.fetchone():
            return jsonify({"success": False, "error": f"Le VLAN {payload['id_vlan']} existe déjà"}), 409

        try:
            payload["ports"] = validate_port_assignments(
                cur,
                payload["ports"],
                resolve_switch_id(cur, payload),
            )
        except ValueError as e:
            conn.rollback()
            return jsonify({"success": False, "error": str(e)}), 400

        insert_columns = ["id_vlan", "nom", "reseau", "gateway", "type", "ports", "status"]
        insert_values  = [
            payload["id_vlan"],
            payload["nom"],
            payload["reseau"] or None,
            payload["gateway"] or None,
            payload["type"],
            payload["ports"],
            payload["status"],
        ]

        if "switch_name" in columns:
            insert_columns.append("switch_name")
            insert_values.append(payload["switch_name"])
        if "switch_ip" in columns:
            insert_columns.append("switch_ip")
            insert_values.append(payload["switch_ip"])

        # ── 1. Déploiement SSH sur le switch (AVANT le commit BDD) ───────────
        deploy_result = {"success": False, "error": "Déploiement non tenté"}
        try:
            from network.deploy_vlan import run_deploy
            deploy_result = run_deploy(payload["id_vlan"], payload["nom"])
        except ImportError:
            deploy_result = {"success": False, "error": "Module network.deploy_vlan introuvable"}
        except Exception as e:
            deploy_result = {"success": False, "error": str(e)}

        # Si le switch a échoué → rollback, rien en BDD
        if not deploy_result.get("success"):
            conn.rollback()
            return jsonify({
                "success":    False,
                "error":      "Le VLAN n'a pas été créé : le déploiement SSH a échoué.",
                "ssh_deploy": deploy_result,
            }), 502

        # ── 2. SSH réussi → INSERT en BDD puis commit ─────────────────────────
        placeholders = ", ".join(["%s"] * len(insert_columns))
        cur.execute(f"""
            INSERT INTO vlan ({", ".join(insert_columns)})
            VALUES ({placeholders})
            RETURNING {", ".join(get_returning_fields(columns))}
        """, insert_values)
        row = cur.fetchone()
        sync_interfaces_with_vlan_ports(
            cur,
            payload["id_vlan"],
            payload["ports"],
            resolve_switch_id(cur, payload),
        )
        conn.commit()
        clear_dashboard_cache()

    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

    # ── 3. Réponse unifiée (les deux ont réussi) ──────────────────────────────
    return jsonify({
        "success":    True,
        "message":    f"VLAN {payload['id_vlan']} '{payload['nom']}' créé sur le switch et enregistré en base de données.",
        "vlan":       build_vlan_response(row),
        "ssh_deploy": deploy_result,
    }), 201


# ─── PUT ──────────────────────────────────────────────────────────────────────

@vlan_bp.route("/api/vlan/<int:id_vlan>", methods=["PUT"])
@vlan_bp.route("/vlan/<int:id_vlan>",     methods=["PUT"])
def update_vlan(id_vlan):
    try:
        payload = normalize_vlan_payload(request.get_json() or {}, forced_vlan_id=id_vlan)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400

    conn = get_db_connection()
    try:
        columns = get_vlan_columns(conn)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        try:
            payload["ports"] = validate_port_assignments(
                cur,
                payload["ports"],
                resolve_switch_id(cur, payload),
            )
        except ValueError as e:
            conn.rollback()
            return jsonify({"success": False, "error": str(e)}), 400

        set_clauses = ["nom = %s", "reseau = %s", "gateway = %s", "type = %s", "ports = %s", "status = %s"]
        values      = [payload["nom"], payload["reseau"] or None, payload["gateway"] or None,
                       payload["type"], payload["ports"], payload["status"]]

        if "switch_name" in columns:
            set_clauses.append("switch_name = %s")
            values.append(payload["switch_name"])
        if "switch_ip" in columns:
            set_clauses.append("switch_ip = %s")
            values.append(payload["switch_ip"])

        values.append(id_vlan)
        cur.execute(f"""
            UPDATE vlan
            SET {", ".join(set_clauses)}
            WHERE id_vlan = %s
            RETURNING {", ".join(get_returning_fields(columns))}
        """, values)
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return jsonify({"success": False, "error": "VLAN introuvable"}), 404

        sync_interfaces_with_vlan_ports(
            cur,
            id_vlan,
            payload["ports"],
            resolve_switch_id(cur, payload),
        )
        conn.commit()
        clear_dashboard_cache()
        return jsonify({"success": True, "message": f"VLAN {id_vlan} mis à jour.", "vlan": build_vlan_response(row)})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


# ─── DELETE (BDD + suppression SSH sur le switch) ────────────────────────────

@vlan_bp.route("/api/vlan/<int:id_vlan>", methods=["DELETE"])
@vlan_bp.route("/vlan/<int:id_vlan>",     methods=["DELETE"])
def delete_vlan(id_vlan):
    """
    Supprime le VLAN de la base de données ET l'efface du switch via SSH.
    Commandes envoyées sur le switch :
        conf t
        no vlan <id>
        no interface vlan <id>    (supprime le SVI / gateway)
        end
        write memory
    """
    conn = get_db_connection()
    try:
        columns = get_vlan_columns(conn)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"""
            DELETE FROM vlan WHERE id_vlan = %s
            RETURNING {", ".join(get_returning_fields(columns))}
        """, (id_vlan,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return jsonify({"success": False, "error": "VLAN introuvable"}), 404

        sync_interfaces_with_vlan_ports(cur, id_vlan, "", None)
        conn.commit()
        clear_dashboard_cache()
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

    vlan_data = build_vlan_response(row)

    # ── Suppression SSH sur le switch ─────────────────────────────────────────
    ssh_result = {"success": False, "error": "Suppression SSH non tentée"}
    try:
        from network.deploy_vlan import run_delete
        ssh_result = run_delete(id_vlan)
    except ImportError:
        # Fallback : si run_delete n'existe pas, on tente directement via Netmiko
        try:
            ssh_result = _ssh_delete_vlan(id_vlan)
        except Exception as e2:
            ssh_result = {"success": False, "error": str(e2)}
    except Exception as e:
        ssh_result = {"success": False, "error": str(e)}

    response = {
        "success":    True,
        "message":    f"VLAN {id_vlan} supprimé de la base de données.",
        "vlan":       vlan_data,
        "ssh_delete": ssh_result,
    }

    if not ssh_result.get("success"):
        response["warning"] = (
            f"VLAN supprimé en BDD mais la suppression SSH a échoué : "
            f"{ssh_result.get('error', 'Erreur inconnue')}"
        )

    return jsonify(response)


def _ssh_delete_vlan(id_vlan: int) -> dict:
    """
    Connexion SSH directe (Netmiko) pour supprimer un VLAN du switch.
    Lit les credentials depuis network/hosts.yaml (même logique que deploy_vlan).
    """
    import yaml, os
    from netmiko import ConnectHandler

    # Chercher hosts.yaml dans les chemins courants
    base_dir = os.path.dirname(os.path.abspath(__file__))
    hosts_path = None
    for candidate in [
        os.path.join(base_dir, "network", "hosts.yaml"),
        os.path.join(base_dir, "hosts.yaml"),
        os.path.join(os.getcwd(), "network", "hosts.yaml"),
    ]:
        if os.path.exists(candidate):
            hosts_path = candidate
            break

    if not hosts_path:
        return {"success": False, "error": "hosts.yaml introuvable — impossible de se connecter au switch"}

    with open(hosts_path, "r") as f:
        hosts_data = yaml.safe_load(f)

    # Supports both list and dict formats
    if isinstance(hosts_data, list):
        host_cfg = hosts_data[0]
    elif isinstance(hosts_data, dict):
        # Format: {switch_cible: {...}} or direct dict
        first_key = next(iter(hosts_data))
        host_cfg = hosts_data[first_key] if isinstance(hosts_data[first_key], dict) else hosts_data
    else:
        return {"success": False, "error": "Format hosts.yaml invalide"}

    device = {
        "device_type": host_cfg.get("device_type", "cisco_ios"),
        "host":        host_cfg.get("hostname") or host_cfg.get("host"),
        "username":    host_cfg.get("username"),
        "password":    host_cfg.get("password"),
        "secret":      host_cfg.get("secret", ""),
        "port":        int(host_cfg.get("port", 22)),
    }

    if not device["host"] or not device["username"]:
        return {"success": False, "error": "Credentials SSH incomplets dans hosts.yaml"}

    commands = [
        "conf t",
        f"no vlan {id_vlan}",
        f"no interface vlan {id_vlan}",
        "end",
        "write memory",
    ]

    net_connect = ConnectHandler(**device)
    try:
        if device["secret"]:
            net_connect.enable()
        output = net_connect.send_config_set(commands[1:-1])  # entre conf t et end
        net_connect.send_command("end")
        net_connect.send_command("write memory")
    finally:
        net_connect.disconnect()

    return {
        "success":  True,
        "message":  f"VLAN {id_vlan} supprimé du switch avec succès.",
        "commands": commands,
        "output":   output,
    }
