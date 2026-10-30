"""Operator-only invitation lifecycle. No public signup or HTTP admin endpoint."""

import argparse
from datetime import timedelta
import os
import secrets

from sqlalchemy import delete, select

from app.beta_models import BetaSession, BetaUser, Workspace
from app.beta_security import digest, utcnow
from app.database import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    invite = actions.add_parser("invite")
    invite.add_argument("--email", required=True)
    target = invite.add_mutually_exclusive_group(required=True)
    target.add_argument("--workspace-name")
    target.add_argument("--workspace-id", type=int)
    invite.add_argument("--admin", action="store_true")
    revoke = actions.add_parser("revoke")
    revoke.add_argument("--email", required=True)
    reinvite = actions.add_parser("reinvite")
    reinvite.add_argument("--email", required=True)
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL"):
        parser.error("Set an explicit DATABASE_URL for the intended environment first.")
    email = args.email.strip().casefold()
    if len(email) > 254 or "@" not in email:
        parser.error("Supply a valid email address.")
    with SessionLocal() as db:
        user = db.scalar(select(BetaUser).where(BetaUser.email == email))
        if args.action == "revoke":
            if user is None:
                parser.error("User not found.")
            user.is_active = False
            user.invite_hash = None
            db.execute(delete(BetaSession).where(BetaSession.user_id == user.id))
            db.commit()
            print("Account and sessions revoked.")
            return
        if args.action == "reinvite":
            if user is None:
                parser.error("User not found.")
            token = secrets.token_urlsafe(32)
            user.is_active = True
            user.password_hash = None
            user.invite_hash = digest(token)
            user.invite_expires_at = utcnow() + timedelta(hours=48)
            db.execute(delete(BetaSession).where(BetaSession.user_id == user.id))
            db.commit()
            print(
                "Previous credentials revoked. Share the replacement one-use invitation privately:"
            )
            print(token)
            return
        if user is not None:
            parser.error(
                "Account already exists. Revoke access if needed; do not silently reassign ownership."
            )
        if args.workspace_id:
            workspace = db.get(Workspace, args.workspace_id)
            if workspace is None:
                parser.error("Workspace not found.")
        else:
            workspace = Workspace(name=args.workspace_name)
            db.add(workspace)
            db.flush()
        token = secrets.token_urlsafe(32)
        db.add(
            BetaUser(
                workspace_id=workspace.id,
                email=email,
                is_active=True,
                is_admin=args.admin,
                invite_hash=digest(token),
                invite_expires_at=utcnow() + timedelta(hours=48),
            )
        )
        db.commit()
        print(f"Workspace ID: {workspace.id}")
        print(
            "Open /accept-invite and share this one-use invitation privately (expires in 48 hours):"
        )
        print(token)


if __name__ == "__main__":
    main()
