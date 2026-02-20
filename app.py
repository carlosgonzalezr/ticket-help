"""
app.py — Aplicación principal Flask para ticket-help
Incluye rutas de autenticación, gestión de tickets con filtrado por grupo,
y ejemplos de todas las rutas protegidas por rol.
"""

import os
from functools import wraps
from flask import (
    Flask, render_template, redirect, url_for,
    request, flash, jsonify, abort, session
)
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)

from models.models import db, bcrypt, User, Group, Ticket, Comment, Role, TicketStatus, TicketPriority
from notifications import notify_ticket_created, notify_ticket_assigned, notify_ticket_resolved

# ─── Inicialización ───────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"]        = os.environ.get("SECRET_KEY", "dev-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "postgresql://ticketuser:ticketpass@localhost:5432/ticketdb")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
bcrypt.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Debes iniciar sesión para acceder."

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ─── Decoradores de permisos ──────────────────────────────────────────────────
def role_required(*roles):
    """Decorador que restringe acceso a uno o más roles."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def superadmin_required(f):
    return role_required(Role.SUPERADMIN)(f)

def admin_required(f):
    return role_required(Role.SUPERADMIN, Role.ADMIN)(f)

def resolver_required(f):
    return role_required(Role.SUPERADMIN, Role.ADMIN, Role.RESOLUTOR)(f)


# ─── Helpers de aislamiento por grupo ────────────────────────────────────────
def get_tickets_for_current_user():
    """
    Filtra tickets según el rol y grupo del usuario autenticado.

    LÓGICA DE AISLAMIENTO:
    - Superadmin  → Ve TODOS los tickets de TODOS los grupos.
    - Admin/Resolutor → Ve solo los tickets de su grupo.
    - Solicitante → Ve solo los tickets que él mismo creó.
    """
    if current_user.role == Role.SUPERADMIN:
        # Sin filtro: acceso total
        return Ticket.query.order_by(Ticket.created_at.desc()).all()

    elif current_user.role in (Role.ADMIN, Role.RESOLUTOR):
        # Filtro por group_id ← CLAVE DEL AISLAMIENTO
        return (
            Ticket.query
            .filter(Ticket.group_id == current_user.group_id)
            .order_by(Ticket.created_at.desc())
            .all()
        )

    else:  # SOLICITANTE
        # Solo sus propios tickets
        return (
            Ticket.query
            .filter(Ticket.created_by_id == current_user.id)
            .order_by(Ticket.created_at.desc())
            .all()
        )


def can_access_ticket(ticket: Ticket) -> bool:
    """Verifica si el usuario actual puede ver/editar un ticket específico."""
    if current_user.role == Role.SUPERADMIN:
        return True
    if current_user.role in (Role.ADMIN, Role.RESOLUTOR):
        return ticket.group_id == current_user.group_id
    # Solicitante: solo los suyos
    return ticket.created_by_id == current_user.id


# ─── Rutas de autenticación ───────────────────────────────────────────────────
@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    tickets = get_tickets_for_current_user()
    stats = {
        "total":       len(tickets),
        "open":        sum(1 for t in tickets if t.status == "open"),
        "in_progress": sum(1 for t in tickets if t.status == "in_progress"),
        "resolved":    sum(1 for t in tickets if t.status in ("resolved", "closed")),
    }
    recent_tickets = sorted(tickets, key=lambda t: t.created_at, reverse=True)[:10]
    return render_template("dashboard.html", stats=stats, recent_tickets=recent_tickets)


@app.route("/reports")
@login_required
@resolver_required
def reports():
    return render_template("reports.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("tickets_list"))

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user     = User.query.filter_by(email=email).first()

        if user and user.is_active and user.check_password(password):
            login_user(user, remember=True)
            flash(f"Bienvenido, {user.name}!", "success")
            return redirect(request.args.get("next") or url_for("tickets_list"))

        flash("Credenciales incorrectas.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sesión cerrada.", "info")
    return redirect(url_for("login"))


# ─── Rutas de Tickets ─────────────────────────────────────────────────────────
@app.route("/tickets")
@login_required
def tickets_list():
    """
    Lista de tickets FILTRADA según el rol y grupo del usuario.
    Demostración del aislamiento por group_id.
    """
    status_filter   = request.args.get("status",   "")
    priority_filter = request.args.get("priority", "")

    tickets = get_tickets_for_current_user()

    # Filtros opcionales adicionales (sobre los ya aislados por grupo)
    if status_filter:
        tickets = [t for t in tickets if t.status == status_filter]
    if priority_filter:
        tickets = [t for t in tickets if t.priority == priority_filter]

    return render_template(
        "tickets/list.html",
        tickets=tickets,
        statuses=vars(TicketStatus),
        priorities=vars(TicketPriority),
        current_status=status_filter,
        current_priority=priority_filter,
    )


@app.route("/tickets/new", methods=["GET", "POST"])
@login_required
def ticket_create():
    groups = []
    if current_user.is_superadmin():
        groups = Group.query.all()
    elif current_user.group_id:
        groups = [current_user.group]

    if request.method == "POST":
        title       = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        priority    = request.form.get("priority", TicketPriority.MEDIUM)
        group_id    = request.form.get("group_id", type=int)

        # Validación básica
        if not title or not description:
            flash("Título y descripción son obligatorios.", "warning")
            return render_template("tickets/create.html", groups=groups)

        # Solicitante solo puede crear en su grupo
        if not current_user.is_superadmin():
            group_id = current_user.group_id

        if not group_id:
            flash("No tienes un grupo asignado.", "danger")
            return render_template("tickets/create.html", groups=groups)

        ticket = Ticket(
            title=title,
            description=description,
            priority=priority,
            group_id=group_id,
            created_by_id=current_user.id,
            source="web",
        )
        db.session.add(ticket)
        db.session.commit()

        notify_ticket_created(ticket, current_user.email)
        flash(f"Ticket #{ticket.id} creado exitosamente.", "success")
        return redirect(url_for("ticket_detail", ticket_id=ticket.id))

    return render_template("tickets/create.html", groups=groups, priorities=vars(TicketPriority))


@app.route("/tickets/<int:ticket_id>")
@login_required
def ticket_detail(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket or not can_access_ticket(ticket):
        abort(404)

    # Resolutores del mismo grupo para asignación
    resolvers = []
    if current_user.can_resolve():
        resolvers = User.query.filter(
            User.group_id == ticket.group_id,
            User.role.in_([Role.ADMIN, Role.RESOLUTOR]),
        ).all()

    comments = ticket.comments.order_by(Comment.created_at.asc()).all()
    # Solicitante no ve notas internas
    if current_user.role == Role.SOLICITANTE:
        comments = [c for c in comments if not c.is_internal]

    return render_template(
        "tickets/detail.html",
        ticket=ticket,
        resolvers=resolvers,
        comments=comments,
        can_resolve=current_user.can_resolve(),
    )


@app.route("/tickets/<int:ticket_id>/update", methods=["POST"])
@login_required
@resolver_required
def ticket_update(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket or not can_access_ticket(ticket):
        abort(404)

    new_status      = request.form.get("status")
    new_assigned_id = request.form.get("assigned_to_id", type=int)
    new_priority    = request.form.get("priority")

    if new_status:
        ticket.status = new_status
        if new_status == TicketStatus.RESOLVED:
            ticket.close()
            notify_ticket_resolved(ticket, ticket.creator.email)

    if new_assigned_id:
        ticket.assigned_to_id = new_assigned_id
        assignee = db.session.get(User, new_assigned_id)
        if assignee:
            notify_ticket_assigned(ticket, assignee.email)

    if new_priority:
        ticket.priority = new_priority

    db.session.commit()
    flash("Ticket actualizado.", "success")
    return redirect(url_for("ticket_detail", ticket_id=ticket.id))


@app.route("/tickets/<int:ticket_id>/comment", methods=["POST"])
@login_required
def ticket_comment(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket or not can_access_ticket(ticket):
        abort(404)

    body        = request.form.get("body", "").strip()
    is_internal = request.form.get("is_internal") == "on" and current_user.can_resolve()

    if not body:
        flash("El comentario no puede estar vacío.", "warning")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    comment = Comment(
        body=body,
        is_internal=is_internal,
        ticket_id=ticket.id,
        author_id=current_user.id,
    )
    db.session.add(comment)
    db.session.commit()
    flash("Comentario agregado.", "success")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))
#--------agregue  ruta de eliminacion----------------------
@app.route("/tickets/<int:ticket_id>/delete", methods=["POST"])
@login_required
@admin_required
def ticket_delete(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        abort(404)
    # Admin solo puede eliminar tickets de su grupo
    if not current_user.is_superadmin() and ticket.group_id != current_user.group_id:
        abort(403)
    db.session.delete(ticket)
    db.session.commit()
    flash(f"Ticket #{ticket_id} eliminado.", "success")
    return redirect(url_for("dashboard"))


# ─── Rutas de Administración ──────────────────────────────────────────────────
@app.route("/admin/users")
@login_required
@admin_required
def admin_users():
    """Superadmin ve todos; Admin de grupo ve solo los de su grupo."""
    if current_user.is_superadmin():
        users = User.query.all()
        groups = Group.query.all()
    else:
        users = User.query.filter_by(group_id=current_user.group_id).all()
        groups = [current_user.group]
    return render_template("admin/users.html", users=users, groups=groups)


@app.route("/admin/users/create", methods=["POST"])
@login_required
@admin_required
def admin_user_create():
    name     = request.form.get("name", "").strip()
    email    = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "").strip()
    role     = request.form.get("role", Role.SOLICITANTE)
    group_id = request.form.get("group_id", type=int)

    if not name or not email or not password:
        flash("Nombre, email y contraseña son obligatorios.", "warning")
        return redirect(url_for("admin_users"))

    if User.query.filter_by(email=email).first():
        flash("Ya existe un usuario con ese correo.", "danger")
        return redirect(url_for("admin_users"))

    # Admin solo puede crear usuarios en su propio grupo
    if not current_user.is_superadmin():
        group_id = current_user.group_id
        # Admin no puede crear superadmin ni admin
        if role in (Role.SUPERADMIN, Role.ADMIN):
            role = Role.RESOLUTOR

    user = User(name=name, email=email, role=role, group_id=group_id)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f"Usuario {name} creado correctamente.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@admin_required
def admin_user_toggle(user_id):
    """Activar o desactivar un usuario."""
    user = db.session.get(User, user_id)
    if not user or user.id == current_user.id:
        abort(404)
    # Admin solo puede gestionar usuarios de su grupo
    if not current_user.is_superadmin() and user.group_id != current_user.group_id:
        abort(403)
    user.is_active = not user.is_active
    db.session.commit()
    estado = "activado" if user.is_active else "desactivado"
    flash(f"Usuario {user.name} {estado}.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/groups")
@login_required
@superadmin_required
def admin_groups():
    groups = Group.query.all()
    return render_template("admin/groups.html", groups=groups)


# ─── API JSON (opcional, para integración futura) ─────────────────────────────
@app.route("/api/tickets")
@login_required
def api_tickets():
    """Endpoint JSON que devuelve tickets filtrados por grupo."""
    tickets = get_tickets_for_current_user()
    return jsonify([
        {
            "id":         t.id,
            "title":      t.title,
            "status":     t.status,
            "priority":   t.priority,
            "group":      t.group.name,
            "created_at": t.created_at.isoformat(),
        }
        for t in tickets
    ])


# ─── Manejo de errores ────────────────────────────────────────────────────────
@app.errorhandler(403)
def forbidden(e):
    return render_template("errors/403.html"), 403

@app.errorhandler(404)
def not_found(e):
    return render_template("errors/404.html"), 404


# ─── CLI: inicializar BD con datos de ejemplo ─────────────────────────────────
@app.cli.command("init-db")
def init_db():
    """Crea las tablas y carga datos iniciales. Ejecutar: flask init-db"""
    db.create_all()

    # Grupos por defecto
    for group_data in [
        {"name": "TI",          "email": "ti@prueba.com",          "description": "Tecnología de la Información"},
        {"name": "RRHH",        "email": "rrhh@prueba.com",        "description": "Recursos Humanos"},
        {"name": "Call Center", "email": "callcenter@prueba.com",  "description": "Atención al Cliente"},
    ]:
        if not Group.query.filter_by(name=group_data["name"]).first():
            db.session.add(Group(**group_data))

    db.session.flush()

    # Usuario superadmin
    if not User.query.filter_by(email="admin@ticket-help.com").first():
        su = User(name="Super Admin", email="admin@ticket-help.com", role=Role.SUPERADMIN)
        su.set_password("Admin1234!")
        db.session.add(su)

    db.session.commit()
    print("✓ Base de datos inicializada con grupos y superadmin.")
    print("  Email: admin@ticket-help.com | Pass: Admin1234!")


if __name__ == "__main__":
    app.run(debug=True)


@app.template_filter("nl2br")
def nl2br_filter(s):
    from markupsafe import Markup
    return Markup(s.replace("\n", "<br>")) if s else ""