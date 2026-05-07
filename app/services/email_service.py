from __future__ import annotations

import os
import requests
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr


def _env_bool(name: str, default: bool = False) -> bool:
    return str(os.getenv(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


def _base_url() -> str:
    return (os.getenv("BASE_URL") or "http://127.0.0.1:8000").rstrip("/")


def verification_url(token: str) -> str:
    return f"{_base_url()}/auth/verify-email?token={token}"


RESEND_API_KEY = os.getenv("RESEND_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "no-reply@mail.go360.io")
FROM_NAME = os.getenv("FROM_NAME", "go360")

def send_email(to_email: str, subject: str, html: str):
    if not RESEND_API_KEY:
        raise Exception("RESEND_API_KEY not configured")

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": f"{FROM_NAME} <{FROM_EMAIL}>",
            "to": [to_email],
            "subject": subject,
            "html": html,
        },
    )

    if response.status_code >= 300:
        raise Exception(f"Email send failed: {response.text}")

def send_verification_email(*, to_email: str, full_name: str | None, token: str) -> dict:
    url = verification_url(token)
    name = (full_name or "there").strip() or "there"
    html = f"""
    <div style=\"font-family:Arial,sans-serif;line-height:1.55;color:#10201b;max-width:620px;margin:0 auto;padding:24px;\">
      <h2 style=\"margin:0 0 12px;color:#10201b;\">Verify your go360 email</h2>
      <p>Hi {name},</p>
      <p>Thanks for creating your go360 account. Please verify your email address before signing in.</p>
      <p style=\"margin:24px 0;\">
        <a href=\"{url}\" style=\"display:inline-block;background:#1ea672;color:#fff;text-decoration:none;padding:12px 18px;border-radius:10px;font-weight:700;\">Verify email</a>
      </p>
      <p>If the button does not work, copy and paste this link into your browser:</p>
      <p style=\"word-break:break-all;color:#5f6f68;\">{url}</p>
      <p>This link expires in 24 hours.</p>
      <p style=\"color:#5f6f68;font-size:13px;\">If you did not create a go360 account, you can safely ignore this email.</p>
    </div>
    """
    text = f"Verify your go360 email: {url}"
    send_email(to_email=to_email, subject="Verify your go360 account", html=html)
    return {"verification_url": url}
