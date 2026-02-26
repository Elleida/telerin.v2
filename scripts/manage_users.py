#!/usr/bin/env python3
"""
Gestión de usuarios de TeleRín desde línea de comandos.

Uso:
  python scripts/manage_users.py list
  python scripts/manage_users.py create <username> <password> [--email EMAIL] [--role user|admin]
  python scripts/manage_users.py delete <username>
  python scripts/manage_users.py reset-password <username> <new_password>

Requiere ejecutar desde la raíz del proyecto con el venv activado:
  source venv/bin/activate
  python scripts/manage_users.py ...
"""
from __future__ import annotations

import argparse
import sys
import os

# Añadir raíz del proyecto y backend al path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, "backend"))

from services.auth import (
    create_user,
    delete_user,
    ensure_users_table,
    list_users,
    get_user_by_username,
    update_password,
)


def cmd_list(_args):
    users = list_users()
    if not users:
        print("No hay usuarios registrados.")
        return
    fmt = "{:<36}  {:<20}  {:<30}  {:<8}  {}"
    print(fmt.format("ID", "Usuario", "Email", "Rol", "Creado"))
    print("-" * 110)
    for u in users:
        print(fmt.format(
            u.get("id", ""),
            u.get("username", ""),
            u.get("email", "") or "",
            u.get("role", ""),
            str(u.get("created_at", ""))[:19],
        ))


def cmd_create(args):
    try:
        user = create_user(args.username, args.password, args.email, args.role)
        print(f"✅ Usuario '{user['username']}' creado con rol '{user['role']}'.")
    except ValueError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_delete(args):
    user = get_user_by_username(args.username)
    if not user:
        print(f"❌ Usuario '{args.username}' no encontrado.", file=sys.stderr)
        sys.exit(1)
    ok = delete_user(user["id"])
    if ok:
        print(f"✅ Usuario '{args.username}' eliminado.")
    else:
        print(f"❌ No se pudo eliminar '{args.username}'.", file=sys.stderr)
        sys.exit(1)


def cmd_reset_password(args):
    user = get_user_by_username(args.username)
    if not user:
        print(f"❌ Usuario '{args.username}' no encontrado.", file=sys.stderr)
        sys.exit(1)
    ok = update_password(user["id"], args.new_password)
    if ok:
        print(f"✅ Contraseña de '{args.username}' actualizada.")
    else:
        print(f"❌ No se pudo actualizar la contraseña.", file=sys.stderr)
        sys.exit(1)


def main():
    ensure_users_table()

    parser = argparse.ArgumentParser(
        description="Gestión de usuarios TeleRín",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    sub.add_parser("list", help="Listar todos los usuarios")

    # create
    p_create = sub.add_parser("create", help="Crear un nuevo usuario")
    p_create.add_argument("username")
    p_create.add_argument("password")
    p_create.add_argument("--email", default="", help="Email del usuario")
    p_create.add_argument("--role", choices=["user", "admin"], default="user")

    # delete
    p_delete = sub.add_parser("delete", help="Eliminar un usuario por username")
    p_delete.add_argument("username")

    # reset-password
    p_reset = sub.add_parser("reset-password", help="Cambiar contraseña de un usuario")
    p_reset.add_argument("username")
    p_reset.add_argument("new_password")

    args = parser.parse_args()

    dispatch = {
        "list": cmd_list,
        "create": cmd_create,
        "delete": cmd_delete,
        "reset-password": cmd_reset_password,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
