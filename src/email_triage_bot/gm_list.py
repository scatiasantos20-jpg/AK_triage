from __future__ import annotations

import argparse

from email_triage_bot.config import Settings
from email_triage_bot.logging_conf import setup_logging
from email_triage_bot.clients.gmail.client import GmailClient
from email_triage_bot.profiles import get_profile


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", type=str, default="", help="Profile name from profiles.json")
    ap.add_argument("--limit", type=int, default=None, help="Override limit")
    ap.add_argument("--query", type=str, default=None, help="Override Gmail query")
    args = ap.parse_args()

    s = Settings()
    setup_logging(s.log_level)

    profile_name = args.profile.strip() or s.default_profile
    prof = get_profile(s.profiles_path, profile_name)

    credentials_path = prof.credentials_path if prof else s.gmail_credentials_path
    token_path = prof.token_path if prof else s.gmail_token_path
    query = (args.query.strip() if args.query else (prof.gmail_query if prof and prof.gmail_query else s.gmail_query))
    limit = int(args.limit if args.limit is not None else (prof.batch_limit if prof and prof.batch_limit else s.batch_limit))

    gmail = GmailClient(
        credentials_path=credentials_path,
        token_path=token_path,
        include_compose_scope=True,
        include_modify_scope=True,
    )

    msgs = gmail.list_messages(query=query, limit=limit)
    print(f"account: {profile_name} | found: {len(msgs)} | query: {query}")
    print("-" * 60)
    for m in msgs:
        link = f"https://mail.google.com/mail/u/0/#inbox/{m.message_id}"
        print(f"id: {m.message_id}\nfrom: {m.from_address}\nsubject: {m.subject}\nlink: {link}")
        print("-" * 60)


if __name__ == "__main__":
    main()
