# 🎫 ticket-help

Sistema de tickets helpdesk con Flask + PostgreSQL + Docker.

## 🚀 Inicio rápido

```bash
# 1. Clonar o descomprimir el proyecto
cd ticket-help

# 2. Levantar los contenedores
docker-compose up --build -d

# 3. Inicializar la BD (tablas + datos de ejemplo)
docker-compose exec app flask init-db

# 4. Abrir en el navegador
open http://localhost:5000
```

Credenciales por defecto:
- **Email:** `admin@ticket-help.com`
- **Contraseña:** `Admin1234!`

---

## 📁 Estructura

```
ticket-help/
├── app.py                  # Flask app principal + rutas
├── mail_checker.py         # Lector IMAP → Tickets
├── notifications.py        # SMTP send_notification()
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── models/
│   └── models.py           # SQLAlchemy: Users, Groups, Tickets, Comments
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── tickets/
│   │   ├── list.html
│   │   ├── detail.html
│   │   └── create.html
│   ├── admin/
│   │   ├── users.html
│   │   └── groups.html
│   └── errors/
│       ├── 403.html
│       └── 404.html
└── static/
    ├── css/main.css
    └── js/main.js
```

---

## 👥 Roles y permisos

| Rol          | Ve tickets                    | Crea | Resuelve | Gestiona usuarios |
|--------------|-------------------------------|------|----------|-------------------|
| Superadmin   | Todos los grupos              | ✅   | ✅       | ✅ (todos)        |
| Admin        | Solo su grupo                 | ✅   | ✅       | ✅ (su grupo)     |
| Resolutor    | Solo su grupo                 | ✅   | ✅       | ❌                |
| Solicitante  | Solo sus propios tickets      | ✅   | ❌       | ❌                |

### Aislamiento por grupo (código clave en `app.py`)

```python
def get_tickets_for_current_user():
    if current_user.role == Role.SUPERADMIN:
        return Ticket.query.all()
    elif current_user.role in (Role.ADMIN, Role.RESOLUTOR):
        return Ticket.query.filter(Ticket.group_id == current_user.group_id).all()
    else:
        return Ticket.query.filter(Ticket.created_by_id == current_user.id).all()
```

---

## 📧 Configuración de correo

### Salida (SMTP) — `notifications.py`

Edita en `docker-compose.yml`:
```yaml
MAIL_SERVER: smtp.gmail.com
MAIL_PORT: 587
MAIL_USERNAME: tu_correo@gmail.com
MAIL_PASSWORD: tu_app_password   # Contraseña de aplicación de Google
```

### Entrada (IMAP) — `mail_checker.py`

```yaml
IMAP_SERVER: imap.gmail.com
IMAP_PORT: 993
MAIL_GROUP_MAP: "ti@prueba.com:TI,rrhh@prueba.com:RRHH"
```

Ejecutar el lector de correos:
```bash
# Una sola vez
docker-compose exec app python mail_checker.py

# En bucle continuo (cada 60s)
docker-compose exec app python mail_checker.py --loop
```

---

## 🔧 Variables de entorno

| Variable          | Descripción                              | Default              |
|-------------------|------------------------------------------|----------------------|
| `DATABASE_URL`    | URL de conexión PostgreSQL               | (ver docker-compose) |
| `SECRET_KEY`      | Clave Flask para sesiones                | Cambiar en producción|
| `MAIL_SERVER`     | Servidor SMTP                            | smtp.gmail.com       |
| `MAIL_USERNAME`   | Correo remitente                         | —                    |
| `MAIL_PASSWORD`   | Contraseña o App Password                | —                    |
| `IMAP_SERVER`     | Servidor IMAP para lectura               | imap.gmail.com       |
| `MAIL_GROUP_MAP`  | Mapeo `correo:grupo` separado por comas  | —                    |
| `MAIL_POLL_INTERVAL` | Segundos entre ciclos IMAP (--loop)  | 60                   |
