"""User management from the command line.

    python -m backend.auth.cli create-user alice --role admin
    python -m backend.auth.cli set-password alice
    python -m backend.auth.cli list-users
    python -m backend.auth.cli disable alice      # also ends all of alice's sessions
    python -m backend.auth.cli enable alice
    python -m backend.auth.cli revoke-sessions alice
"""
import argparse
import asyncio
import getpass
import sys
from sqlalchemy import select
from backend.auth import service
from backend.db.database import SessionLocal, engine, init_db
from backend.db.models import User


def _ask_password() -> str:
    first = getpass.getpass("Password (minimo 12 caratteri): ")
    if first != getpass.getpass("Ripeti la password: "):
        sys.exit("Le password non coincidono.")
    return first


async def _run(args) -> None:
    await init_db()
    try:
        async with SessionLocal() as db:
            if args.command == "create-user":
                user = await service.create_user(db, args.username, _ask_password(), role=args.role)
                print(f"Creato {user.username} ({user.role}).")
            elif args.command == "list-users":
                users = (await db.execute(select(User).order_by(User.username))).scalars().all()
                for u in users:
                    last = u.last_login_at.isoformat(timespec="minutes") if u.last_login_at else "mai"
                    print(f"{u.username:<32} {u.role:<7} {'attivo' if u.is_active else 'disattivato':<12} ultimo accesso: {last}")
                if not users:
                    print("Nessun utente.")
            else:
                user = await service.get_user(db, args.username)
                if user is None:
                    sys.exit(f"Utente {args.username} non trovato.")
                if args.command == "set-password":
                    await service.set_password(db, user, _ask_password())
                    print("Password aggiornata. Tutte le sessioni dell'utente sono state chiuse.")
                elif args.command == "disable":
                    user.is_active = False
                    await db.commit()
                    await service.revoke_user_sessions(db, user)
                    print(f"{user.username} disattivato e disconnesso.")
                elif args.command == "enable":
                    user.is_active = True
                    await db.commit()
                    print(f"{user.username} riattivato.")
                elif args.command == "revoke-sessions":
                    n = await service.revoke_user_sessions(db, user)
                    print(f"Chiuse {n} sessioni.")
    except ValueError as e:
        sys.exit(str(e))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m backend.auth.cli", description="Gestione utenti della dashboard")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help="crea un utente (chiede la password)")
    create.add_argument("username")
    create.add_argument("--role", choices=service.ROLES, default="viewer")
    sub.add_parser("list-users", help="elenca gli utenti")
    for name, help_text in (("set-password", "imposta una nuova password"), ("disable", "disattiva e disconnette"),
                            ("enable", "riattiva"), ("revoke-sessions", "chiude tutte le sessioni")):
        sub.add_parser(name, help=help_text).add_argument("username")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
