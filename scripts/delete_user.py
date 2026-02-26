#!/usr/bin/env python3
"""
Eliminar un usuario por `username` usando las utilidades del backend.

Usage:
  python scripts/delete_user.py admin
  python scripts/delete_user.py admin --yes
"""
import os
import sys

THIS_DIR = os.path.abspath(os.path.dirname(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")

# Asegurar que el paquete `services` del backend sea importable
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
except Exception:
    pass

from services.auth import get_user_by_username, delete_user


def main():
    import argparse

    p = argparse.ArgumentParser(description="Eliminar usuario por username usando backend/services.auth")
    p.add_argument("username", help="Nombre de usuario a eliminar")
    p.add_argument("--yes", "-y", action="store_true", help="No pedir confirmación")
    args = p.parse_args()

    user = get_user_by_username(args.username)
    if not user:
        print(f"Usuario no encontrado: {args.username}")
        return

    print("Usuario encontrado:")
    print(f"  id: {user.get('id')}")
    print(f"  username: {user.get('username')}")
    print(f"  email: {user.get('email')}")

    if not args.yes:
        confirm = input(f"Eliminar usuario '{args.username}'? [y/N]: ")
        if confirm.lower() != "y":
            print("Cancelado")
            return

    ok = delete_user(user.get("id"))
    if ok:
        print("Usuario eliminado correctamente")
    else:
        print("Error al eliminar usuario")


if __name__ == "__main__":
    main()
