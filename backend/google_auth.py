"""
Google Sign-In, the free way: the frontend uses Google Identity Services
(a plain <script> tag, no npm package) to get a signed ID token straight
from Google, then sends that token here. We verify its signature and
audience with Google's own library - no OAuth redirect dance, no client
secret needed on this flow, no cost.

Setup: create an OAuth Client ID (type "Web application") in Google Cloud
Console -> APIs & Services -> Credentials, add your site's origin under
"Authorized JavaScript origins", and put the Client ID in .env as
GOOGLE_CLIENT_ID.
"""
import os

from fastapi import HTTPException

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def google_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID)


def verify_google_token(credential: str) -> dict:
    """Verify a Google Identity Services credential (ID token JWT).

    Returns {"google_id", "email", "name", "picture"} on success.
    Raises HTTPException(401) on any invalid/expired/mismatched token.
    """
    if not google_configured():
        raise HTTPException(
            503,
            "Google sign-in isn't set up on this server yet. "
            "Add GOOGLE_CLIENT_ID to backend/.env (see .env.example).",
        )
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError:
        raise HTTPException(
            500,
            "Google sign-in needs the 'google-auth' package. "
            "Run: pip install -r requirements.txt",
        )

    try:
        payload = id_token.verify_oauth2_token(
            credential, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(401, "Invalid or expired Google sign-in token")

    if payload.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise HTTPException(401, "Invalid Google sign-in token issuer")
    if not payload.get("email_verified", False):
        raise HTTPException(401, "That Google account's email isn't verified")

    return {
        "google_id": payload["sub"],
        "email": payload.get("email", ""),
        "name": payload.get("name") or payload.get("email", "").split("@")[0],
        "picture": payload.get("picture", ""),
    }
