from __future__ import annotations

import argparse

from email_triage_bot.clients.gmail.client import GmailClient
from email_triage_bot.config import Settings
from email_triage_bot.logging_conf import setup_logging
from email_triage_bot.profiles import get_profile


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", type=str, default="", help="Profile name from profiles.json")
    ap.add_argument("--limit", type=int, default=None, help="Override limit")
    ap.add_argument("--query", type=str, default=None, help="Override Gmail query")
    ap.add_argument(
        "--auth-upgrade-scopes",
        action="store_true",
        help="Request compose/modify scopes (needed for draft creation and mark-as-read).",
    )
    args = ap.parse_args()

    settings = Settings()
    setup_logging(settings.log_level)

    profile_name = args.profile.strip() or settings.default_profile
    profile = get_profile(settings.profiles_path, profile_name)

    credentials_path = profile.credentials_path if profile else settings.gmail_credentials_path
    token_path = profile.token_path if profile else settings.gmail_token_path
    query = args.query.strip() if args.query else (profile.gmail_query if profile and profile.gmail_query else settings.gmail_query)
    limit = int(args.limit if args.limit is not None else (profile.batch_limit if profile and profile.batch_limit else settings.batch_limit))

    # Least privilege by default: listing only needs read scope.
    gmail = GmailClient(
        credentials_path=credentials_path,
        token_path=token_path,
        include_compose_scope=bool(args.auth_upgrade_scopes),
        include_modify_scope=bool(args.auth_upgrade_scopes),
    )

    msgs = gmail.list_messages(query=query, limit=limit)
    print(f"account: {profile_name} | found: {len(msgs)} | query: {query} | auth_upgrade_scopes: {bool(args.auth_upgrade_scopes)}")
    print("-" * 60)
    for message in msgs:
        link = f"https://mail.google.com/mail/u/0/#inbox/{message.message_id}"
        print(f"id: {message.message_id}\nfrom: {message.from_address}\nsubject: {message.subject}\nlink: {link}")
        print("-" * 60)


if __name__ == "__main__":
    main()
