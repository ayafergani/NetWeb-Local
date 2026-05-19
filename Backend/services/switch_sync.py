import logging
import os
import re
from datetime import datetime, timezone

import psycopg2.extras
from netmiko import ConnectHandler

from Database.db import get_db_connection
from dashboard_api import _SUMMARY_CACHE

logger = logging.getLogger(__name__)

SSH_TIMEOUT = int(os.getenv("NETMIKO_TIMEOUT", "12"))
SSH_AUTH_TIMEOUT = int(os.getenv("NETMIKO_AUTH_TIMEOUT", "10"))
SSH_BANNER_TIMEOUT = int(os.getenv("NETMIKO_BANNER_TIMEOUT", "8"))


def invalidate_dashboard_cache():
    _SUMMARY_CACHE["data"] = None
    _SUMMARY_CACHE["expires_at"] = 0


def ensure_switch_sync_schema(cur):
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'interface'
        """
    )
    interface_columns = {row[0] for row in cur.fetchall()}

    if "description" not in interface_columns:
        cur.execute("ALTER TABLE interface ADD COLUMN description TEXT")
    if "duplex" not in interface_columns:
        cur.execute("ALTER TABLE interface ADD COLUMN duplex VARCHAR(32)")

    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'switchs'
        """
    )
    switch_columns = {row[0] for row in cur.fetchall()}

    if "last_sync_at" not in switch_columns:
        cur.execute("ALTER TABLE switchs ADD COLUMN last_sync_at TIMESTAMPTZ")
    if "last_sync_error" not in switch_columns:
        cur.execute("ALTER TABLE switchs ADD COLUMN last_sync_error TEXT")


def _decode_secret(value):
    if value is None:
        return ""
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        return value.decode(errors="ignore")
    return str(value)


def _normalize_port_name(value):
    raw = str(value or "").strip()
    if not raw:
        return ""

    compact = re.sub(r"\s+", "", raw).lower()
    replacements = (
        ("gigabitethernet", "Gi"),
        ("tengigabitethernet", "Te"),
        ("fastethernet", "Fa"),
        ("ethernet", "Eth"),
        ("port-channel", "Po"),
    )
    for src, dst in replacements:
        if compact.startswith(src):
            return dst + raw[len(src):].strip()

    if compact.startswith("gi"):
        return "Gi" + raw[2:].strip()
    if compact.startswith("te"):
        return "Te" + raw[2:].strip()
    if compact.startswith("fa"):
        return "Fa" + raw[2:].strip()
    if compact.startswith("po"):
        return "Po" + raw[2:].strip()
    if compact.startswith("eth"):
        return "Eth" + raw[3:].strip()
    return raw


def _normalize_interface_status(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"connected", "up"}:
        return "UP"
    return "DOWN"


def _normalize_vlan_value(value):
    raw = str(value or "").strip()
    if not raw:
        return None, None
    if raw.isdigit():
        return int(raw), None
    return None, raw


def _guess_interface_type(interface_name):
    name = str(interface_name or "").lower()
    if name.startswith(("te", "po", "fo")):
        return "uplink"
    return "access"


def _guess_speed(interface_name, reported_speed):
    speed = str(reported_speed or "").strip()
    if speed and speed not in {"auto", "a-auto", "-", "--"}:
        return speed
    return "10Gb" if _guess_interface_type(interface_name) == "uplink" else "1Gb"


def parse_show_interfaces_status(output):
    interfaces = []

    for raw_line in str(output or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("port "):
            continue
        if set(stripped) <= {"-"}:
            continue
        if not re.match(r"^[A-Za-z]+\S*", stripped):
            continue

        columns = re.split(r"\s{2,}", stripped)
        if len(columns) < 6:
            continue

        port = _normalize_port_name(columns[0])
        if not port:
            continue

        # Cisco "show interfaces status" is more stable when parsed from the right:
        # Port | [optional description...] | Status | Vlan | Duplex | Speed | Type
        fixed_tail = columns[-5:]
        description_parts = columns[1:-5] if len(columns) > 6 else []
        description = " ".join(part.strip() for part in description_parts if part.strip())
        oper_status, vlan_value, duplex, speed, _media_type = fixed_tail

        vlan_id, allowed_vlans = _normalize_vlan_value(vlan_value)
        mode = "trunk" if vlan_id is None and vlan_value.lower() in {"trunk", "routed"} else "access"

        interfaces.append(
            {
                "nom": port,
                "description": description or None,
                "status": _normalize_interface_status(oper_status),
                "vlan_id": vlan_id,
                "mode": mode,
                "allowed_vlans": allowed_vlans,
                "duplex": None if duplex in {"auto", "a-auto", "-", "--"} else duplex,
                "speed": _guess_speed(port, speed),
                "type": _guess_interface_type(port),
            }
        )

    return interfaces


def parse_show_vlan_brief(output):
    vlans = []
    current = None

    for raw_line in str(output or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith("vlan ") or lowered.startswith("----"):
            continue

        match = re.match(r"^(?P<id>\d+)\s+(?P<name>\S+)\s+(?P<status>\S+)(?:\s+(?P<ports>.+))?$", stripped)
        if match:
            ports = match.group("ports") or ""
            current = {
                "id_vlan": int(match.group("id")),
                "nom": match.group("name"),
                "status": match.group("status"),
                "ports": ports.strip(),
            }
            vlans.append(current)
            continue

        if current and re.match(r"^(?:Gi|Fa|Te|Eth|Po)\S+", stripped):
            current["ports"] = ", ".join(filter(None, [current["ports"], stripped]))

    for vlan in vlans:
        ports = [
            _normalize_port_name(item.strip())
            for item in vlan["ports"].split(",")
            if item.strip()
        ]
        vlan["ports"] = ", ".join(dict.fromkeys(ports))
        vlan["status"] = str(vlan["status"] or "active").upper()

    return vlans


def _build_switch_device(switch_row):
    return {
        "device_type": "cisco_ios",
        "host": switch_row["ip"],
        "username": switch_row["username"],
        "password": _decode_secret(switch_row["password"]),
        "session_timeout": SSH_TIMEOUT,
        "auth_timeout": SSH_AUTH_TIMEOUT,
        "banner_timeout": SSH_BANNER_TIMEOUT,
        "fast_cli": False,
    }


def _load_switch_row(cur, switch_id):
    with cur.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as dict_cur:
        dict_cur.execute(
            """
            SELECT id_switch, nom, ip, username, password, nb_ports
            FROM switchs
            WHERE id_switch = %s
            """,
            (switch_id,),
        )
        return dict_cur.fetchone()


def _update_switch_status(cur, switch_id, status, error_message=None):
    cur.execute(
        """
        UPDATE switchs
        SET status = %s,
            last_sync_at = %s,
            last_sync_error = %s
        WHERE id_switch = %s
        """,
        (
            status,
            datetime.now(timezone.utc),
            (str(error_message)[:1000] if error_message else None),
            switch_id,
        ),
    )


def _upsert_interfaces(cur, switch_row, interfaces):
    updated_count = 0
    deleted_count = 0
    touched_vlans = set()
    received_names = set()

    for item in interfaces:
        touched_vlans.add(item.get("vlan_id"))
        received_names.add(item["nom"])
        cur.execute(
            """
            SELECT id_interface
            FROM interface
            WHERE id_switch = %s AND nom = %s
            ORDER BY id_interface ASC
            LIMIT 1
            """,
            (switch_row["id_switch"], item["nom"]),
        )
        existing = cur.fetchone()

        if existing:
            cur.execute(
                """
                UPDATE interface
                SET status = %s,
                    vlan_id = %s,
                    mode = %s,
                    allowed_vlans = %s,
                    duplex = %s,
                    speed = %s,
                    description = %s,
                    type = %s
                WHERE id_interface = %s
                """,
                (
                    item["status"],
                    item["vlan_id"],
                    item["mode"],
                    item["allowed_vlans"],
                    item["duplex"],
                    item["speed"],
                    item["description"],
                    item["type"],
                    existing[0],
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO interface (
                    nom, ip, vlan_id, id_switch, equipement_id, status, mode, type,
                    speed, allowed_vlans, port_security, max_mac, violation_mode,
                    bpdu_guard, description, duplex
                )
                VALUES (%s, NULL, %s, %s, NULL, %s, %s, %s, %s, %s, FALSE, 1, 'shutdown', FALSE, %s, %s)
                """,
                (
                    item["nom"],
                    item["vlan_id"],
                    switch_row["id_switch"],
                    item["status"],
                    item["mode"],
                    item["type"],
                    item["speed"],
                    item["allowed_vlans"],
                    item["description"],
                    item["duplex"],
                ),
            )
        updated_count += 1

    for vlan_id in {value for value in touched_vlans if value is not None}:
        cur.execute(
            """
            SELECT COALESCE(STRING_AGG(nom, ', ' ORDER BY nom), '')
            FROM interface
            WHERE id_switch = %s
              AND vlan_id = %s
              AND COALESCE(mode, 'access') = 'access'
            """,
            (switch_row["id_switch"], vlan_id),
        )
        ports_value = cur.fetchone()[0] or ""
        cur.execute(
            """
            UPDATE vlan
            SET ports = %s,
                switch_name = %s,
                switch_ip = %s
            WHERE id_vlan = %s
            """,
            (ports_value, switch_row["nom"], switch_row["ip"], vlan_id),
        )

    if received_names:
        cur.execute(
            """
            DELETE FROM interface
            WHERE id_switch = %s
              AND nom <> ALL(%s)
            RETURNING vlan_id
            """,
            (switch_row["id_switch"], list(received_names)),
        )
        deleted_rows = cur.fetchall()
        deleted_count = len(deleted_rows)
        touched_vlans.update(row[0] for row in deleted_rows if row[0] is not None)
    else:
        cur.execute(
            """
            DELETE FROM interface
            WHERE id_switch = %s
            RETURNING vlan_id
            """,
            (switch_row["id_switch"],),
        )
        deleted_rows = cur.fetchall()
        deleted_count = len(deleted_rows)
        touched_vlans.update(row[0] for row in deleted_rows if row[0] is not None)

    for vlan_id in {value for value in touched_vlans if value is not None}:
        cur.execute(
            """
            SELECT COALESCE(STRING_AGG(nom, ', ' ORDER BY nom), '')
            FROM interface
            WHERE id_switch = %s
              AND vlan_id = %s
              AND COALESCE(mode, 'access') = 'access'
            """,
            (switch_row["id_switch"], vlan_id),
        )
        ports_value = cur.fetchone()[0] or ""
        cur.execute(
            """
            UPDATE vlan
            SET ports = %s,
                switch_name = %s,
                switch_ip = %s
            WHERE id_vlan = %s
            """,
            (ports_value, switch_row["nom"], switch_row["ip"], vlan_id),
        )

    return updated_count, deleted_count


def _upsert_vlans(cur, switch_row, vlans):
    updated_count = 0
    deleted_count = 0
    received_vlan_ids = set()

    for item in vlans:
        received_vlan_ids.add(item["id_vlan"])
        cur.execute(
            """
            SELECT COALESCE(STRING_AGG(nom, ', ' ORDER BY nom), '')
            FROM interface
            WHERE id_switch = %s
              AND vlan_id = %s
              AND COALESCE(mode, 'access') = 'access'
            """,
            (switch_row["id_switch"], item["id_vlan"]),
        )
        interface_ports = cur.fetchone()[0] or ""
        ports_value = interface_ports or item.get("ports") or ""

        cur.execute(
            """
            SELECT id_vlan
            FROM vlan
            WHERE id_vlan = %s
            LIMIT 1
            """,
            (item["id_vlan"],),
        )
        existing = cur.fetchone()

        if existing:
            cur.execute(
                """
                UPDATE vlan
                SET nom = %s,
                    status = %s,
                    ports = %s,
                    switch_name = %s,
                    switch_ip = %s
                WHERE id_vlan = %s
                """,
                (
                    item["nom"],
                    item["status"],
                    ports_value,
                    switch_row["nom"],
                    switch_row["ip"],
                    item["id_vlan"],
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO vlan (id_vlan, nom, reseau, gateway, type, ports, status, switch_name, switch_ip)
                VALUES (%s, %s, NULL, NULL, 'Data', %s, %s, %s, %s)
                """,
                (
                    item["id_vlan"],
                    item["nom"],
                    ports_value,
                    item["status"],
                    switch_row["nom"],
                    switch_row["ip"],
                ),
            )
        updated_count += 1

    if received_vlan_ids:
        cur.execute(
            """
            DELETE FROM vlan
            WHERE switch_ip = %s
              AND COALESCE(TRIM(switch_ip), '') <> ''
              AND id_vlan <> ALL(%s)
            """,
            (switch_row["ip"], list(received_vlan_ids)),
        )
        deleted_count = cur.rowcount
    else:
        cur.execute(
            """
            DELETE FROM vlan
            WHERE switch_ip = %s
              AND COALESCE(TRIM(switch_ip), '') <> ''
            """,
            (switch_row["ip"],),
        )
        deleted_count = cur.rowcount

    return updated_count, deleted_count


def sync_switch_state(switch_id):
    conn = get_db_connection()
    net_connect = None

    try:
        cur = conn.cursor()
        ensure_switch_sync_schema(cur)
        switch_row = _load_switch_row(cur, switch_id)
        if not switch_row:
            conn.rollback()
            return {"success": False, "error": "Switch introuvable", "status_code": 404}

        device = _build_switch_device(
            {
                "ip": switch_row["ip"],
                "username": switch_row["username"],
                "password": switch_row["password"],
            }
        )

        try:
            net_connect = ConnectHandler(**device)
            interface_output = net_connect.send_command("show interfaces status", read_timeout=20)
            vlan_output = net_connect.send_command("show vlan brief", read_timeout=20)
        except Exception as exc:
            logger.warning("Synchronisation SSH impossible pour switch %s: %s", switch_id, exc)
            _update_switch_status(cur, switch_id, "DOWN", str(exc))
            conn.commit()
            invalidate_dashboard_cache()
            return {
                "success": False,
                "switch_status": "offline",
                "status": "offline",
                "error": str(exc),
                "interfaces_updated": 0,
                "interfaces_deleted": 0,
                "vlans_updated": 0,
                "vlans_deleted": 0,
                "status_code": 200,
            }

        parsed_interfaces = parse_show_interfaces_status(interface_output)
        parsed_vlans = parse_show_vlan_brief(vlan_output)

        switch_info = {
            "id_switch": switch_row["id_switch"],
            "nom": switch_row["nom"],
            "ip": switch_row["ip"],
            "nb_ports": switch_row["nb_ports"],
        }

        interfaces_updated, interfaces_deleted = _upsert_interfaces(cur, switch_info, parsed_interfaces)
        vlans_updated, vlans_deleted = _upsert_vlans(cur, switch_info, parsed_vlans)
        _update_switch_status(cur, switch_id, "UP", None)
        conn.commit()
        invalidate_dashboard_cache()

        return {
            "success": True,
            "switch_status": "online",
            "status": "online",
            "interfaces_updated": interfaces_updated,
            "interfaces_deleted": interfaces_deleted,
            "vlans_updated": vlans_updated,
            "vlans_deleted": vlans_deleted,
            "interfaces_count": len(parsed_interfaces),
            "vlans_count": len(parsed_vlans),
        }
    except Exception as exc:
        conn.rollback()
        logger.exception("Erreur de synchronisation pour switch %s", switch_id)
        return {
            "success": False,
            "switch_status": "offline",
            "status": "offline",
            "error": str(exc),
            "interfaces_updated": 0,
            "interfaces_deleted": 0,
            "vlans_updated": 0,
            "vlans_deleted": 0,
            "status_code": 500,
        }
    finally:
        if net_connect is not None:
            try:
                net_connect.disconnect()
            except Exception:
                logger.debug("Fermeture Netmiko ignorée", exc_info=True)
        conn.close()