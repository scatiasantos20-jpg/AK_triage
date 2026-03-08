from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

from email_triage_bot.clients.gmail.client import GmailClient
from email_triage_bot.clients.gmail.parser import extract_bodies, get_header
from email_triage_bot.config import Settings
from email_triage_bot.core.normalization import html_to_text, strip_quoted_replies
from email_triage_bot.logging_conf import setup_logging
from email_triage_bot.profiles import get_profile


def _to_iso_utc(ts_ms: int) -> str:
    if not ts_ms:
        return ""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.isoformat()


def _rows_from_messages(gmail: GmailClient, listed: list, include_quoted: bool) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in listed:
        try:
            msg = gmail.get_message_full(item.message_id)
            payload = msg.get("payload", {}) or {}
            headers = payload.get("headers", []) or []

            subject = get_header(headers, "Subject") or item.subject or ""
            from_hdr = get_header(headers, "From") or item.from_address or ""
            text_plain, text_html = extract_bodies(payload)
            body = text_plain or (html_to_text(text_html) if text_html else "")
            if not include_quoted:
                body = strip_quoted_replies(body)
            body = (body or "").strip()

            rows.append([
                item.message_id,
                item.thread_id,
                _to_iso_utc(item.internal_ts_ms),
                from_hdr,
                subject,
                body,
                f"https://mail.google.com/mail/u/0/#inbox/{item.message_id}",
            ])
        except Exception as ex:
            print(f"WARNING: failed to read message {item.message_id}: {type(ex).__name__}: {ex}")
    return rows


def _save_as_csv(path: Path, rows: list[list[str]]) -> Path:
    if path.suffix.lower() == ".xlsx":
        path = path.with_suffix(".csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["message_id", "thread_id", "date_utc", "from", "subject", "body", "gmail_link"])
        w.writerows(rows)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Export sender/subject/body to an Excel file for triage analysis.")
    ap.add_argument("--profile", type=str, default="", help="Profile name from profiles.json")
    ap.add_argument("--limit", type=int, default=None, help="Override BATCH_LIMIT")
    ap.add_argument("--query", type=str, default=None, help="Override GMAIL_QUERY")
    ap.add_argument("--output", type=str, default="triage_export.xlsx", help="Output .xlsx path")
    ap.add_argument("--include-quoted", action="store_true", help="Keep quoted replies in body")
    args = ap.parse_args()

    settings = Settings()
    setup_logging(settings.log_level)

    profile_name = args.profile.strip() or settings.default_profile
    profile = get_profile(settings.profiles_path, profile_name)

    credentials_path = profile.credentials_path if profile else settings.gmail_credentials_path
    token_path = profile.token_path if profile else settings.gmail_token_path
    query = args.query.strip() if args.query else (profile.gmail_query if profile and profile.gmail_query else settings.gmail_query)
    limit = int(args.limit if args.limit is not None else (profile.batch_limit if profile and profile.batch_limit else settings.batch_limit))

    try:
        gmail = GmailClient(
            credentials_path=credentials_path,
            token_path=token_path,
            include_compose_scope=False,
            include_modify_scope=False,
        )
    except FileNotFoundError as ex:
        raise SystemExit(
            "Missing Gmail credentials for export. "
            f"Expected file: {credentials_path}. "
            "Set GMAIL_CREDENTIALS_PATH / PROFILES_PATH correctly or place credentials.json in the project root."
        ) from ex
    except PermissionError as ex:
        raise SystemExit(
            "Gmail token does not have required read access for export. "
            "Re-authenticate the profile (for example with gm_list) and try again."
        ) from ex

    try:
        listed = gmail.list_messages(query=query, limit=limit)
    except Exception as ex:
        raise SystemExit(f"Failed to list Gmail messages for export: {type(ex).__name__}: {ex}") from ex

    rows = _rows_from_messages(gmail, listed, include_quoted=args.include_quoted)

    out = Path(args.output)
    try:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "emails"
        ws.append(["message_id", "thread_id", "date_utc", "from", "subject", "body", "gmail_link"])
        for row in rows:
            ws.append(row)
        out.parent.mkdir(parents=True, exist_ok=True)
        wb.save(out)
    except ModuleNotFoundError:
        out = _save_as_csv(out, rows)
        print("WARNING: openpyxl is not installed. Exported CSV instead of XLSX.")
        print("Tip: install openpyxl with 'pip install openpyxl' to export .xlsx files.")

    print(f"account: {profile_name} | exported: {len(listed)} | query: {query} | output: {out}")


if __name__ == "__main__":
    main()
