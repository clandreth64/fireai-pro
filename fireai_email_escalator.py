"""
FireAI Pro — Email Escalator  (Python)
=======================================
Drop at repo root. Auto-detects provider from recipient domain.

Provider resolution order:
  1. FIREAI_EMAIL_PROVIDER env var  ('gmail' | 'outlook' | 'smtp')
  2. Auto-detect from FIREAI_ESCALATION_EMAIL domain
  3. SMTP fallback

Environment variables:

  Common:
    FIREAI_EMAIL_PROVIDER      — gmail | outlook | smtp | auto
    FIREAI_ESCALATION_EMAIL    — recipient address
    FIREAI_FROM_EMAIL          — sender address
    FIREAI_FROM_NAME           — display name (default: FireAI Pro)

  Gmail:
    GMAIL_CLIENT_ID
    GMAIL_CLIENT_SECRET
    GMAIL_REFRESH_TOKEN
    GMAIL_USER_EMAIL

  Outlook / Office 365:
    OUTLOOK_TENANT_ID
    OUTLOOK_CLIENT_ID
    OUTLOOK_CLIENT_SECRET
    OUTLOOK_USER_EMAIL

  SMTP (Sendgrid / Postmark / any):
    SMTP_HOST     (default: smtp.gmail.com)
    SMTP_PORT     (default: 587)
    SMTP_USER
    SMTP_PASS
    SMTP_USE_TLS  (default: true)
"""

import asyncio
import base64
import json
import logging
import os
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart   import MIMEMultipart
from email.mime.text        import MIMEText
from typing import Any

import httpx

log = logging.getLogger("fireai.email")

GMAIL_DOMAINS   = {"gmail.com", "googlemail.com"}
OUTLOOK_DOMAINS = {"outlook.com", "hotmail.com", "live.com", "msn.com",
                   "office365.com", "microsoft.com"}


def _detect_provider(to_email: str = "") -> str:
    configured = os.getenv("FIREAI_EMAIL_PROVIDER", "auto").lower()
    if configured != "auto":
        return configured

    domain = to_email.split("@")[-1].lower() if "@" in to_email else ""
    if domain in GMAIL_DOMAINS:
        return "gmail"
    if domain in OUTLOOK_DOMAINS:
        return "outlook"

    # Fallback: check sender domain
    from_domain = os.getenv("FIREAI_FROM_EMAIL", "").split("@")[-1].lower()
    if from_domain in GMAIL_DOMAINS:
        return "gmail"
    if from_domain in OUTLOOK_DOMAINS:
        return "outlook"

    return "smtp"


# ── Gmail sender ───────────────────────────────────────────────────────────────

class GmailSender:
    def __init__(self):
        self.client_id     = os.getenv("GMAIL_CLIENT_ID", "")
        self.client_secret = os.getenv("GMAIL_CLIENT_SECRET", "")
        self.refresh_token = os.getenv("GMAIL_REFRESH_TOKEN", "")
        self.user_email    = os.getenv("GMAIL_USER_EMAIL") or os.getenv("FIREAI_FROM_EMAIL", "")

    async def _get_access_token(self) -> str:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id":     self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type":    "refresh_token",
                },
            )
            resp.raise_for_status()
            return resp.json()["access_token"]

    def _build_mime(self, to, subject, body, from_name, from_email, attachments):
        if attachments:
            msg = MIMEMultipart()
            msg.attach(MIMEText(body, "plain"))
            for att in attachments:
                part = MIMEApplication(att["content"].encode() if isinstance(att["content"], str) else att["content"])
                part.add_header("Content-Disposition", "attachment", filename=att["filename"])
                msg.attach(part)
        else:
            msg = MIMEText(body, "plain")

        msg["From"]    = f'"{from_name}" <{from_email}>'
        msg["To"]      = to
        msg["Subject"] = subject
        return base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")

    async def send(self, to, subject, body, from_name, from_email, attachments):
        token = await self._get_access_token()
        raw   = self._build_mime(to, subject, body, from_name, from_email, attachments)
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"raw": raw},
            )
            resp.raise_for_status()
            return {"provider": "gmail", "message_id": resp.json().get("id")}


# ── Outlook / Microsoft Graph sender ─────────────────────────────────────────

class OutlookSender:
    def __init__(self):
        self.tenant_id     = os.getenv("OUTLOOK_TENANT_ID", "")
        self.client_id     = os.getenv("OUTLOOK_CLIENT_ID", "")
        self.client_secret = os.getenv("OUTLOOK_CLIENT_SECRET", "")
        self.user_email    = os.getenv("OUTLOOK_USER_EMAIL") or os.getenv("FIREAI_FROM_EMAIL", "")

    async def _get_access_token(self) -> str:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "client_id":     self.client_id,
                    "client_secret": self.client_secret,
                    "scope":         "https://graph.microsoft.com/.default",
                    "grant_type":    "client_credentials",
                },
            )
            resp.raise_for_status()
            return resp.json()["access_token"]

    async def send(self, to, subject, body, from_name, from_email, attachments):
        token   = await self._get_access_token()
        payload = {
            "message": {
                "subject": subject,
                "body":    {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": to}}],
                "from": {"emailAddress": {"name": from_name, "address": self.user_email}},
                "attachments": [
                    {
                        "@odata.type":  "#microsoft.graph.fileAttachment",
                        "name":         att["filename"],
                        "contentType":  "application/octet-stream",
                        "contentBytes": base64.b64encode(
                            att["content"].encode() if isinstance(att["content"], str) else att["content"]
                        ).decode(),
                    }
                    for att in (attachments or [])
                ],
            },
            "saveToSentItems": True,
        }
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"https://graph.microsoft.com/v1.0/users/{self.user_email}/sendMail",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
        return {"provider": "outlook", "message_id": f"graph-{id(payload)}"}


# ── SMTP fallback ─────────────────────────────────────────────────────────────

class SMTPSender:
    def __init__(self):
        self.host    = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.port    = int(os.getenv("SMTP_PORT", "587"))
        self.user    = os.getenv("SMTP_USER", "")
        self.passwd  = os.getenv("SMTP_PASS", "")
        self.use_tls = os.getenv("SMTP_USE_TLS", "true").lower() != "false"

    async def send(self, to, subject, body, from_name, from_email, attachments):
        msg = MIMEMultipart()
        msg["From"]    = f'"{from_name}" <{from_email}>'
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        for att in (attachments or []):
            part = MIMEApplication(
                att["content"].encode() if isinstance(att["content"], str) else att["content"]
            )
            part.add_header("Content-Disposition", "attachment", filename=att["filename"])
            msg.attach(part)

        def _send():
            ctx = ssl.create_default_context()
            with smtplib.SMTP(self.host, self.port) as smtp:
                if self.use_tls:
                    smtp.starttls(context=ctx)
                if self.user:
                    smtp.login(self.user, self.passwd)
                smtp.sendmail(from_email, to, msg.as_string())

        await asyncio.to_thread(_send)
        return {"provider": "smtp", "message_id": f"smtp-{id(msg)}"}


# ── Public API ────────────────────────────────────────────────────────────────

class EmailEscalator:
    def __init__(self):
        self.from_email = (
            os.getenv("FIREAI_FROM_EMAIL")
            or os.getenv("GMAIL_USER_EMAIL")
            or os.getenv("OUTLOOK_USER_EMAIL", "")
        )
        self.from_name = os.getenv("FIREAI_FROM_NAME", "FireAI Pro")

    def _get_sender(self, provider: str):
        if provider == "gmail":   return GmailSender()
        if provider == "outlook": return OutlookSender()
        return SMTPSender()

    async def send(
        self,
        to:          str,
        subject:     str,
        body:        str,
        attachments: list[dict] | None = None,
    ) -> dict:
        """
        Send an escalation email. Provider is auto-detected from recipient domain
        unless FIREAI_EMAIL_PROVIDER is explicitly set.

        Args:
            to:          Recipient email address.
            subject:     Email subject line.
            body:        Plain text body.
            attachments: List of {"filename": str, "content": str|bytes} dicts.
        """
        if not to:
            raise ValueError("FIREAI_ESCALATION_EMAIL is not set")

        provider = _detect_provider(to)
        sender   = self._get_sender(provider)
        log.info(f"Sending escalation via {provider} → {to}")

        result = await sender.send(
            to=to,
            subject=subject,
            body=body,
            from_name=self.from_name,
            from_email=self.from_email,
            attachments=attachments or [],
        )
        log.info(f"Sent — provider: {result['provider']}, id: {result.get('message_id')}")
        return result
