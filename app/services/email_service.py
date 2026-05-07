from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr


def _env_bool(name: str, default: bool = False) -> bool:
    return str(os.getenv(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


def _base_url() -> str:
    return (os.getenv("BASE_URL") or "http://127.0.0.1:8000").rstrip("/")


def verification_url(token: str) -> str:
    return f"{_base_url()}/auth/verify-email?token={token}"


def send_email(*, to_email: str, subject: str, html: str, text: str | None = None) -> dict:
    """Send an email using SMTP.

    Environment variables:
    - SMTP_HOST
    - SMTP_PORT, default 587
    - SMTP_USER
    - SMTP_PASS
    - SMTP_USE_TLS, default true
    - FROM_EMAIL, default no-reply@go360.io
    - FROM_NAME, default go360

    In non-production, if SMTP is not configured, the email is logged and the app
    continues. In production, missing SMTP configuration raises RuntimeError so
    registrations cannot silently fail.
    """
    app_env = (os.getenv("APP_ENV") or "development").lower()
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_use_tls = _env_bool("SMTP_USE_TLS", True)
    from_email = os.getenv("FROM_EMAIL", "no-reply@go360.io")
    from_name = os.getenv("FROM_NAME", "go360")

    if not smtp_host:
        if app_env == "production":
            raise RuntimeError("SMTP_HOST is not configured. Email cannot be sent in production.")
        print("[email:dev] SMTP not configured; email not sent.")
        print(f"[email:dev] To: {to_email}")
        print(f"[email:dev] Subject: {subject}")
        print(f"[email:dev] Body: {text or html}")
        return {"sent": False, "dev_logged": True}

    msg = MIMEText(html if html else (text or ""), "html" if html else "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_email))
    msg["To"] = to_email

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
        if smtp_use_tls:
            server.starttls()
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    return {"sent": True, "dev_logged": False}


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
    result = send_email(to_email=to_email, subject="Verify your go360 account", html=html, text=text)
    result["verification_url"] = url
    return result
