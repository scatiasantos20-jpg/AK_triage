from __future__ import annotations

import argparse
import re

from email_triage_bot.config import Settings
from email_triage_bot.logging_conf import setup_logging
from email_triage_bot.clients.gmail.client import GmailClient
from email_triage_bot.clients.gmail.parser import extract_bodies, get_header
from email_triage_bot.core.normalization import html_to_text, strip_quoted_replies
from email_triage_bot.core.thread_context import parse_thread, build_thread_context
from email_triage_bot.core.prompt_builder import DEFAULT_MASTER_PROMPT, PromptParts, build_prompt
from email_triage_bot.clients.gemini.client import GeminiClient, GeminiConfig, GeminiRateLimitExceeded
from email_triage_bot.profiles import get_profile


def _contains_name_keyword(text: str, keywords_csv: str) -> bool:
    text = (text or "")
    kws = [k.strip() for k in (keywords_csv or "").split(",") if k.strip()]
    if not kws:
        return True
    for k in kws:
        if re.search(rf"\b{re.escape(k)}\b", text, flags=re.IGNORECASE):
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", type=str, default="", help="Profile name from profiles.json")
    ap.add_argument("--id", required=True, help="Gmail message id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = Settings()
    setup_logging(s.log_level)

    if not s.gemini_api_key:
        raise SystemExit("Missing GEMINI_API_KEY in .env")

    profile_name = args.profile.strip() or s.default_profile
    prof = get_profile(s.profiles_path, profile_name)

    credentials_path = (prof.credentials_path if prof else s.gmail_credentials_path)
    token_path = (prof.token_path if prof else s.gmail_token_path)
    signature = (prof.draft_signature if prof and prof.draft_signature is not None else s.draft_signature)

    gmail = GmailClient(credentials_path=credentials_path, token_path=token_path, include_compose_scope=not args.dry_run)

    msg = gmail.get_message_full(args.id)
    payload = msg.get("payload", {}) or {}
    headers = payload.get("headers", []) or []
    subject = get_header(headers, "Subject") or ""
    from_hdr = get_header(headers, "From") or ""

    text_plain, text_html = extract_bodies(payload)
    body_text = text_plain or (html_to_text(text_html) if text_html else "")
    body_text = strip_quoted_replies(body_text).strip()

    if s.require_name_mention:
        hay = f"{subject}\n{body_text}"
        if not _contains_name_keyword(hay, s.name_keywords):
            raise SystemExit("SKIP: latest email does not mention required name keywords (REQUIRE_NAME_MENTION=true).")

    thread_context = ""
    try:
        thread_id = msg.get("threadId") or ""
        if thread_id:
            thread = gmail.get_thread_full(thread_id)
            items = parse_thread(thread)
            thread_context = build_thread_context(items, max_items=6, max_chars=2200)
    except Exception:
        thread_context = ""

    latest_block = f"From: {from_hdr}\nSubject: {subject}\n\n{body_text}"
    prompt = build_prompt(PromptParts(master=DEFAULT_MASTER_PROMPT, thread_context=thread_context, latest_email=latest_block))

    with GeminiClient(GeminiConfig(
        api_key=s.gemini_api_key,
        model=s.gemini_model,
        api_version=s.gemini_api_version,
        timeout_s=s.gemini_timeout_s,
    )) as llm:
        try:
            draft_body = llm.generate(prompt)
        except GeminiRateLimitExceeded as ex:
            raise SystemExit(f"WARNING: Gemini free-tier limit reached (429 RESOURCE_EXHAUSTED). Stop now. Details: {ex}")

    if signature and signature.strip():
        draft_body = draft_body.rstrip() + "\n\n" + signature.strip() + "\n"

    print("----- DRAFT PREVIEW -----")
    print(draft_body)

    if args.dry_run:
        print("[DRY RUN] Not creating Gmail draft.")
        return

    gmail.create_reply_draft(message_id=args.id, draft_text=draft_body)
    print(f"Draft created for message_id={args.id}")


if __name__ == "__main__":
    main()
