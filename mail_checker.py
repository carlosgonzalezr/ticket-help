#!/usr/bin/env python3
"""
mail_checker.py — Lector de correos vía IMAP
Lee mensajes no leídos y los inserta como nuevos Tickets en la BD,
asignando el grupo según la dirección de destino del correo.

Uso:
    python mail_checker.py           # Ejecución única
    python mail_checker.py --loop    # Bucle cada N segundos (útil en cron o como servicio)

Variables de entorno requeridas (ver docker-compose.yml):
    DATABASE_URL, IMAP_SERVER, IMAP_PORT,
    MAIL_USERNAME, MAIL_PASSWORD, MAIL_GROUP_MAP
"""

import imaplib
import email
import os
import time
import logging
import argparse
from email.header import decode_header
from datetime import datetime

# SQLAlchemy independiente (sin Flask app context)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ajusta el path si ejecutas desde fuera del contenedor
import sys
sys.path.insert(0, os.path.dirname(__file__))
from models.models import Ticket, Group, User, TicketStatus, TicketPriority

# ─── Configuración ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mail_checker")

DATABASE_URL  = os.environ.get("DATABASE_URL", "postgresql://ticketuser:ticketpass@localhost:5432/ticketdb")
IMAP_SERVER   = os.environ.get("IMAP_SERVER",  "imap.gmail.com")
IMAP_PORT     = int(os.environ.get("IMAP_PORT", 993))
MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
POLL_INTERVAL = int(os.environ.get("MAIL_POLL_INTERVAL", 60))  # segundos

# Mapeo: "ti@prueba.com:TI,rrhh@prueba.com:RRHH"
MAIL_GROUP_MAP: dict[str, str] = {}
raw_map = os.environ.get("MAIL_GROUP_MAP", "")
for pair in raw_map.split(","):
    pair = pair.strip()
    if ":" in pair:
        addr, group_name = pair.split(":", 1)
        MAIL_GROUP_MAP[addr.strip().lower()] = group_name.strip()

log.info("Mapeo correo→grupo: %s", MAIL_GROUP_MAP)

# ─── Base de datos ────────────────────────────────────────────────────────────
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine)


# ─── Helpers ──────────────────────────────────────────────────────────────────
def decode_mime_header(header_value: str) -> str:
    """Decodifica cabeceras MIME (ej: =?utf-8?b?...?=)."""
    parts = decode_header(header_value or "")
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def get_body(msg: email.message.Message) -> str:
    """Extrae el cuerpo en texto plano del mensaje."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                charset = part.get_content_charset() or "utf-8"
                body = part.get_payload(decode=True).decode(charset, errors="replace")
                break
    else:
        charset = msg.get_content_charset() or "utf-8"
        body = msg.get_payload(decode=True).decode(charset, errors="replace")
    return body.strip()


def get_group_by_recipient(to_address: str, session) -> Group | None:
    """
    Dado el correo destino (ej: ti@prueba.com),
    busca el nombre de grupo en el mapa y lo consulta en la BD.
    """
    to_lower = to_address.lower().strip()

    # Buscar coincidencia exacta primero
    group_name = MAIL_GROUP_MAP.get(to_lower)

    # Si no hay exacta, buscar por subconjunto del campo To (puede traer "Nombre <addr>")
    if not group_name:
        for addr, name in MAIL_GROUP_MAP.items():
            if addr in to_lower:
                group_name = name
                break

    if not group_name:
        log.warning("No se encontró grupo para el destinatario: %s", to_address)
        return None

    group = session.query(Group).filter_by(name=group_name).first()
    if not group:
        log.warning("Grupo '%s' no existe en la BD.", group_name)
    return group


def find_or_create_email_user(from_email: str, from_name: str, session) -> User:
    """
    Busca un usuario por email. Si no existe, crea uno tipo 'solicitante'
    sin grupo asignado (correo externo).
    """
    user = session.query(User).filter_by(email=from_email).first()
    if not user:
        log.info("Creando usuario externo: %s <%s>", from_name, from_email)
        user = User(
            name=from_name or from_email.split("@")[0],
            email=from_email,
            role="solicitante",
        )
        user.set_password(os.urandom(16).hex())  # Contraseña aleatoria inutilizable
        session.add(user)
        session.flush()  # Obtener ID sin commit completo
    return user


# ─── Lógica principal ─────────────────────────────────────────────────────────
def check_mail():
    """Conecta al servidor IMAP, lee no leídos y crea tickets."""
    log.info("Conectando a %s:%d …", IMAP_SERVER, IMAP_PORT)

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(MAIL_USERNAME, MAIL_PASSWORD)
    except Exception as exc:
        log.error("Error de conexión IMAP: %s", exc)
        return

    mail.select("INBOX")

    # Buscar mensajes no leídos (UNSEEN)
    status, data = mail.search(None, "UNSEEN")
    if status != "OK" or not data[0]:
        log.info("No hay mensajes nuevos.")
        mail.logout()
        return

    message_ids = data[0].split()
    log.info("Mensajes no leídos: %d", len(message_ids))

    session = Session()
    tickets_created = 0

    try:
        for num in message_ids:
            # Descargar mensaje completo
            _, msg_data = mail.fetch(num, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            # Extraer campos
            subject  = decode_mime_header(msg.get("Subject", "(Sin asunto)"))
            from_raw = decode_mime_header(msg.get("From", ""))
            to_raw   = decode_mime_header(msg.get("To", ""))
            body     = get_body(msg)

            # Parsear remitente
            from_name, from_email = email.utils.parseaddr(from_raw)
            from_email = from_email.lower()

            log.info("Procesando: De=%s | Para=%s | Asunto=%s", from_email, to_raw, subject)

            # Determinar grupo según destinatario
            group = get_group_by_recipient(to_raw, session)
            if not group:
                # Marcar como leído y saltar (no sabemos a qué grupo asignar)
                mail.store(num, "+FLAGS", "\\Seen")
                continue

            # Obtener o crear usuario remitente
            creator = find_or_create_email_user(from_email, from_name, session)

            # Crear el ticket
            ticket = Ticket(
                title=subject[:200],
                description=body or "(Sin cuerpo)",
                status=TicketStatus.OPEN,
                priority=TicketPriority.MEDIUM,
                source="email",
                group_id=group.id,
                created_by_id=creator.id,
            )
            session.add(ticket)
            tickets_created += 1

            # Marcar como leído para no procesarlo de nuevo
            mail.store(num, "+FLAGS", "\\Seen")

        session.commit()
        log.info("Tickets creados: %d", tickets_created)

    except Exception as exc:
        session.rollback()
        log.error("Error procesando mensajes: %s", exc, exc_info=True)
    finally:
        session.close()
        mail.logout()


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lector IMAP → Tickets")
    parser.add_argument("--loop", action="store_true", help="Ejecutar en bucle continuo")
    args = parser.parse_args()

    if args.loop:
        log.info("Modo bucle activo. Intervalo: %ds", POLL_INTERVAL)
        while True:
            check_mail()
            log.info("Esperando %ds para el próximo ciclo…", POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)
    else:
        check_mail()
