from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from email.message import EmailMessage
import base64
import re


SCOPES_READ = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPES_COMPOSE = ["https://www.googleapis.com/auth/gmail.compose"]
SCOPES_MODIFY = ["https://www.googleapis.com/auth/gmail.modify"]


def _scopes(include_compose: bool, include_modify: bool) -> list[str]:
    scopes = list(SCOPES_READ)
    if include_compose:
        scopes.extend(SCOPES_COMPOSE)
    if include_modify:
        scopes.extend(SCOPES_MODIFY)
    out = []
    seen = set()
    for s in scopes:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


@dataclass(frozen=True)
class ListedMessage:
    message_id: str
    thread_id: str
    internal_ts_ms: int
    from_address: str
    subject: str
    label_ids: list[str]


class GmailClient:
    def __init__(
        self,
        credentials_path: str,
        token_path: str,
        include_compose_scope: bool,
        include_modify_scope: bool = False,
    ):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.include_compose_scope = include_compose_scope
        self.include_modify_scope = include_modify_scope
        self._svc = self._build_service()

    def _build_service(self):
        scopes = _scopes(self.include_compose_scope, self.include_modify_scope)
        creds: Optional[Credentials] = None

        if os.path.exists(self.token_path):
            try:
                creds = Credentials.from_authorized_user_file(self.token_path, scopes=scopes)
            except Exception:
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(
                        f"Missing credentials file: {self.credentials_path}. Put credentials.json in the project root."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, scopes=scopes)
                creds = flow.run_local_server(
                    port=0,
                    prompt="consent",
                    access_type="offline",
                    include_granted_scopes="true",
                )

            with open(self.token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

        granted = set((creds.scopes or []))
        if self.include_compose_scope and not set(SCOPES_COMPOSE).issubset(granted):
            raise PermissionError(
                "Token does not include gmail.compose. Delete the token file for this profile and re-run gm_list."
            )
        if self.include_modify_scope and not set(SCOPES_MODIFY).issubset(granted):
            raise PermissionError(
                "Token does not include gmail.modify. Delete the token file for this profile and re-run gm_list."
            )

        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    @property
    def svc(self):
        return self._svc

    def list_messages(self, query: str, limit: int) -> list[ListedMessage]:
        res = self.svc.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        ids = [m["id"] for m in (res.get("messages") or [])]

        out: list[ListedMessage] = []
        for mid in ids:
            m = self.svc.users().messages().get(
                userId="me",
                id=mid,
                format="metadata",
                metadataHeaders=["From", "Subject", "Date", "Message-ID"],
            ).execute()

            payload = m.get("payload", {}) or {}
            headers = payload.get("headers", []) or []
            from_hdr = _header(headers, "From") or ""
            subject = _header(headers, "Subject") or ""

            out.append(ListedMessage(
                message_id=m.get("id") or mid,
                thread_id=m.get("threadId") or mid,
                internal_ts_ms=int(m.get("internalDate") or 0),
                from_address=from_hdr,
                subject=subject,
                label_ids=list(m.get("labelIds", []) or []),
            ))

        out.sort(key=lambda x: x.internal_ts_ms, reverse=True)
        return out

    def get_message_full(self, message_id: str) -> dict:
        return self.svc.users().messages().get(userId="me", id=message_id, format="full").execute()

    def get_thread_full(self, thread_id: str) -> dict:
        return self.svc.users().threads().get(userId="me", id=thread_id, format="full").execute()

    def mark_as_read(self, message_id: str) -> dict:
        body = {"removeLabelIds": ["UNREAD"]}
        return self.svc.users().messages().modify(userId="me", id=message_id, body=body).execute()

    def create_reply_draft(self, message_id: str, draft_text: str) -> dict:
        msg = self.get_message_full(message_id)
        thread_id = msg.get("threadId") or ""

        payload = msg.get("payload", {}) or {}
        headers = payload.get("headers", []) or []
        from_hdr = _header(headers, "From") or ""
        subject = _header(headers, "Subject") or ""
        msgid = _header(headers, "Message-ID") or _header(headers, "Message-Id") or ""

        to_email = _extract_email(from_hdr)

        em = EmailMessage()
        em["To"] = to_email or from_hdr
        em["Subject"] = _reply_subject(subject)
        if msgid:
            em["In-Reply-To"] = msgid
            em["References"] = msgid
        em.set_content((draft_text or "").rstrip() + "\n")

        raw = base64.urlsafe_b64encode(em.as_bytes()).decode("utf-8")
        body = {"message": {"raw": raw}}
        if thread_id:
            body["message"]["threadId"] = thread_id

        return self.svc.users().drafts().create(userId="me", body=body).execute()


def _header(headers: list[dict], name: str) -> str | None:
    n = name.lower()
    for h in headers or []:
        if (h.get("name") or "").lower() == n:
            return h.get("value")
    return None


_EMAIL_RE = re.compile(r"<([^>]+)>")


def _extract_email(from_hdr: str) -> str:
    if not from_hdr:
        return ""
    m = _EMAIL_RE.search(from_hdr)
    if m:
        return (m.group(1) or "").strip()
    if "@" in from_hdr and " " not in from_hdr:
        return from_hdr.strip()
    return ""


def _reply_subject(subject: str) -> str:
    s = (subject or "").strip()
    if not s:
        return "Re:"
    if s.lower().startswith("re:"):
        return s
    return "Re: " + s
