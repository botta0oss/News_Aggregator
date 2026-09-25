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
from backend.i18n import tr


def _ask_password() -> str:
    first = getpass.getpass(tr("Password (minimo 12 caratteri): ", "Password (at least 12 characters): "))
    if first != getpass.getpass(tr("Ripeti la password: ", "Repeat the password: ")):
        sys.exit(tr("Le password non coincidono.", "The passwords do not match."))
    return first


async def _run(args) -> None:
    await init_db()
    try:
        async with SessionLocal() as db:
            if args.command == "create-user":
                user = await service.create_user(db, args.username, _ask_password(), role=args.role)
                print(tr(f"Creato {user.username} ({user.role}).", f"Created {user.username} ({user.role})."))
            elif args.command == "list-users":
                users = (await db.execute(select(User).order_by(User.username))).scalars().all()
                for u in users:
                    last = u.last_login_at.isoformat(timespec="minutes") if u.last_login_at else tr("mai", "never")
                    print(f"{u.username:<32} {u.role:<7} {tr('attivo', 'active') if u.is_active else tr('disattivato', 'disabled'):<12} " + tr('ultimo accesso', 'last sign-in') + f": {last}")
                if not users:
                    print(tr("Nessun utente.", "No users."))
            else:
                user = await service.get_user(db, args.username)
                if user is None:
                    sys.exit(tr(f"Utente {args.username} non trovato.", f"User {args.username} not found."))
                if args.command == "set-password":
                    await service.set_password(db, user, _ask_password())
                    print(tr("Password aggiornata. Tutte le sessioni dell'utente sono state chiuse.", "Password updated. All the user's sessions have been closed."))
                elif args.command == "disable":
                    user.is_active = False
                    await db.commit()
                    await service.revoke_user_sessions(db, user)
                    print(tr(f"{user.username} disattivato e disconnesso.", f"{user.username} disabled and signed out."))
                elif args.command == "enable":
                    user.is_active = True
                    await db.commit()
                    print(tr(f"{user.username} riattivato.", f"{user.username} enabled again."))
                elif args.command == "revoke-sessions":
                    n = await service.revoke_user_sessions(db, user)
                    print(tr(f"Chiuse {n} sessioni.", f"Closed {n} sessions."))
    except ValueError as e:
        sys.exit(str(e))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m backend.auth.cli", description="Dashboard user management")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help=tr("crea un utente (chiede la password)", "create a user (asks for the password)"))
    create.add_argument("username")
    create.add_argument("--role", choices=service.ROLES, default="viewer")
    sub.add_parser("list-users", help=tr("elenca gli utenti", "list the users"))
    for name, help_text in (("set-password", tr("imposta una nuova password", "set a new password")), ("disable", tr("disattiva e disconnette", "disable and sign out")),
                            ("enable", tr("riattiva", "enable again")), ("revoke-sessions", tr("chiude tutte le sessioni", "close all sessions"))):
        sub.add_parser(name, help=help_text).add_argument("username")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
