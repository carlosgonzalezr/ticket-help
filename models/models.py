"""
models.py — Definición de tablas con SQLAlchemy
Tablas: Users, Groups, Tickets, Comments
"""

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt

db = SQLAlchemy()
bcrypt = Bcrypt()

# ─────────────────────────────────────────────
# Roles disponibles en el sistema
# ─────────────────────────────────────────────
class Role:
    SUPERADMIN  = "superadmin"   # Ve y gestiona todo
    ADMIN       = "admin"        # Admin de su propio grupo
    RESOLUTOR   = "resolutor"    # Puede resolver tickets de su grupo
    SOLICITANTE = "solicitante"  # Solo crea y ve sus propios tickets


# ─────────────────────────────────────────────
# Tabla: groups
# ─────────────────────────────────────────────
class Group(db.Model):
    __tablename__ = "groups"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False, unique=True)  # Ej: "TI", "RRHH"
    description = db.Column(db.String(255), nullable=True)
    email       = db.Column(db.String(150), unique=True, nullable=True)   # ti@prueba.com
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    # Relaciones
    users   = db.relationship("User",   back_populates="group", lazy="dynamic")
    tickets = db.relationship("Ticket", back_populates="group", lazy="dynamic")

    def __repr__(self):
        return f"<Group {self.name}>"


# ─────────────────────────────────────────────
# Tabla: users
# ─────────────────────────────────────────────
class User(db.Model):
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(150), nullable=False)
    email         = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role          = db.Column(db.String(50),  nullable=False, default=Role.SOLICITANTE)
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    # FK: un usuario pertenece a un grupo (salvo superadmin que puede ser NULL)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=True)

    # Relaciones
    group            = db.relationship("Group",   back_populates="users")
    tickets_created  = db.relationship("Ticket",  foreign_keys="Ticket.created_by_id",  back_populates="creator",   lazy="dynamic")
    tickets_assigned = db.relationship("Ticket",  foreign_keys="Ticket.assigned_to_id", back_populates="assignee",  lazy="dynamic")
    comments         = db.relationship("Comment", back_populates="author", lazy="dynamic")

    # ── Flask-Login interface ──
    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)

    # ── Helpers de contraseña ──
    def set_password(self, plain_password: str):
        self.password_hash = bcrypt.generate_password_hash(plain_password).decode("utf-8")

    def check_password(self, plain_password: str) -> bool:
        return bcrypt.check_password_hash(self.password_hash, plain_password)

    # ── Helpers de permisos ──
    def is_superadmin(self) -> bool:
        return self.role == Role.SUPERADMIN

    def is_admin(self) -> bool:
        return self.role in (Role.SUPERADMIN, Role.ADMIN)

    def can_resolve(self) -> bool:
        return self.role in (Role.SUPERADMIN, Role.ADMIN, Role.RESOLUTOR)

    def __repr__(self):
        return f"<User {self.email} [{self.role}]>"


# ─────────────────────────────────────────────
# Tabla: tickets
# ─────────────────────────────────────────────
class TicketStatus:
    OPEN        = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED    = "resolved"
    CLOSED      = "closed"

class TicketPriority:
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"
    URGENT = "urgent"

class Ticket(db.Model):
    __tablename__ = "tickets"

    id          = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status      = db.Column(db.String(50),  nullable=False, default=TicketStatus.OPEN)
    priority    = db.Column(db.String(50),  nullable=False, default=TicketPriority.MEDIUM)
    source      = db.Column(db.String(50),  default="web")   # "web" | "email"
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at   = db.Column(db.DateTime, nullable=True)

    # FK: grupo al que pertenece el ticket (AISLAMIENTO CLAVE)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False)

    # FK: quién lo creó y a quién está asignado
    created_by_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Relaciones
    group    = db.relationship("Group", back_populates="tickets")
    creator  = db.relationship("User",  foreign_keys=[created_by_id],  back_populates="tickets_created")
    assignee = db.relationship("User",  foreign_keys=[assigned_to_id], back_populates="tickets_assigned")
    comments = db.relationship("Comment", back_populates="ticket", lazy="dynamic", cascade="all, delete-orphan")

    def close(self):
        self.status    = TicketStatus.CLOSED
        self.closed_at = datetime.utcnow()

    def __repr__(self):
        return f"<Ticket #{self.id} [{self.status}] grupo={self.group_id}>"


# ─────────────────────────────────────────────
# Tabla: comments
# ─────────────────────────────────────────────
class Comment(db.Model):
    __tablename__ = "comments"

    id         = db.Column(db.Integer, primary_key=True)
    body       = db.Column(db.Text, nullable=False)
    is_internal= db.Column(db.Boolean, default=False)  # Nota interna (no visible al solicitante)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # FK
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"),   nullable=False)

    # Relaciones
    ticket = db.relationship("Ticket", back_populates="comments")
    author = db.relationship("User",   back_populates="comments")

    def __repr__(self):
        return f"<Comment #{self.id} ticket={self.ticket_id}>"
