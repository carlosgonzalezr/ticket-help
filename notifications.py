"""
notifications.py — Sistema de notificaciones por correo (SMTP)
"""

import smtplib
import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

log = logging.getLogger(__name__)

MAIL_SERVER   = os.environ.get("MAIL_SERVER",   "smtp.gmail.com")
MAIL_PORT     = int(os.environ.get("MAIL_PORT", 587))
MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
MAIL_USE_TLS  = os.environ.get("MAIL_USE_TLS",  "true").lower() == "true"


def send_notification(to_email: str, subject: str, body: str, html_body: str = None) -> bool:
    """
    Envía una notificación por correo electrónico.

    Args:
        to_email:  Dirección del destinatario.
        subject:   Asunto del mensaje.
        body:      Cuerpo en texto plano (fallback).
        html_body: Cuerpo en HTML (opcional, recomendado).

    Returns:
        True si se envió correctamente, False si hubo error.

    Ejemplo de uso:
        send_notification(
            to_email="usuario@empresa.com",
            subject="Tu ticket #42 fue actualizado",
            body="El ticket ha cambiado a estado: En Progreso",
            html_body="<p>El ticket <b>#42</b> cambió a <b>En Progreso</b></p>",
        )
    """
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        log.warning("Credenciales SMTP no configuradas. Saltando notificación a %s", to_email)
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = MAIL_USERNAME
        msg["To"]      = to_email

        # Parte texto plano (siempre presente como fallback)
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Parte HTML (opcional)
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as server:
            if MAIL_USE_TLS:
                server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.sendmail(MAIL_USERNAME, to_email, msg.as_string())

        log.info("Notificación enviada a %s | Asunto: %s", to_email, subject)
        return True

    except smtplib.SMTPAuthenticationError:
        log.error("Error de autenticación SMTP. Verifica MAIL_USERNAME y MAIL_PASSWORD.")
    except smtplib.SMTPException as exc:
        log.error("Error SMTP al enviar a %s: %s", to_email, exc)
    except Exception as exc:
        log.error("Error inesperado enviando correo a %s: %s", to_email, exc)

    return False


# ─── Notificaciones predefinidas ─────────────────────────────────────────────

def notify_ticket_created(ticket, creator_email: str):
    send_notification(
        to_email=creator_email,
        subject=f"[Ticket #{ticket.id}] Tu solicitud fue recibida",
        body=f"Hola,\n\nTu ticket '{ticket.title}' fue creado correctamente y está siendo revisado.\n\nGracias.",
        html_body=f"""
        <h2>Ticket #{ticket.id} creado</h2>
        <p><strong>Asunto:</strong> {ticket.title}</p>
        <p>Tu solicitud fue recibida y está siendo revisada por el equipo de <strong>{ticket.group.name}</strong>.</p>
        """,
    )


def notify_ticket_assigned(ticket, assignee_email: str):
    send_notification(
        to_email=assignee_email,
        subject=f"[Ticket #{ticket.id}] Se te asignó una solicitud",
        body=f"Se te asignó el ticket #{ticket.id}: '{ticket.title}'.\nPrioridad: {ticket.priority}.",
        html_body=f"""
        <h2>Ticket #{ticket.id} asignado</h2>
        <p><strong>Título:</strong> {ticket.title}</p>
        <p><strong>Prioridad:</strong> {ticket.priority}</p>
        <p>Por favor revísalo a la brevedad.</p>
        """,
    )


def notify_ticket_resolved(ticket, creator_email: str):
    send_notification(
        to_email=creator_email,
        subject=f"[Ticket #{ticket.id}] Tu solicitud fue resuelta",
        body=f"El ticket '{ticket.title}' ha sido marcado como resuelto.",
        html_body=f"""
        <h2>Ticket #{ticket.id} resuelto ✓</h2>
        <p>Tu solicitud <strong>'{ticket.title}'</strong> fue resuelta.</p>
        <p>Si el problema persiste, puedes abrir un nuevo ticket.</p>
        """,
    )
