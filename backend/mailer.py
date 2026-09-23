"""
Sends real emails (verification links) over plain SMTP - not Google Cloud
Console, not a paid API. The easiest free option is Gmail:

  1. Turn on 2-Step Verification on the Gmail account: myaccount.google.com/security
  2. Create an "App Password": myaccount.google.com/apppasswords
     (pick app "Mail", device "Other" -> name it "What Can I Cook")
  3. Put that 16-character app password (NOT your normal Gmail password) in
     .env as SMTP_PASSWORD, and the Gmail address as SMTP_USER.

Any other SMTP provider (Outlook, Zoho Mail, a transactional-email service
with a free tier, your own mail server, etc.) works too - just change
SMTP_HOST/SMTP_PORT.

If SMTP isn't configured, we don't fail registration - we just print the
verification link to the server console, so local development works with
zero setup.
"""
import os
import smtplib
import ssl
from email.mime.text import MIMEText

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
FROM_EMAIL = (os.environ.get("FROM_EMAIL", "").strip() or SMTP_USER)
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")


def mail_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def verification_link(token: str) -> str:
    return f"{APP_BASE_URL}/api/auth/verify-email?token={token}"


def send_verification_email(to_email: str, token: str) -> bool:
    """Sends the email. Returns True if actually sent over SMTP, False if it
    just logged the link to the console (SMTP not configured)."""
    link = verification_link(token)
    if not mail_configured():
        print(f"[mailer] SMTP not configured - verification link for {to_email}:\n  {link}")
        return False

    msg = MIMEText(
        "Hi!\n\n"
        "Confirm your email for What Can I Cook by opening this link:\n"
        f"{link}\n\n"
        "This link expires in 24 hours. If you didn't sign up, you can ignore this email.\n"
    )
    msg["Subject"] = "Verify your email - What Can I Cook"
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
    return True
