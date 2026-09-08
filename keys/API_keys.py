"""Backwards-compatible shim.

Secrets now live in ``.env`` (git-ignored). This module keeps the old
``from keys.API_keys import X`` imports working by reading them from the
environment via :mod:`config`.
"""
from config import secret

ChatgptAPI = secret("OPENAI_API_KEY")

tapo_user = secret("TAPO_USER")
tapo_password = secret("TAPO_PASSWORD")

EMAIL_ADDRESS = secret("EMAIL_ADDRESS")
EMAIL_PASSWORD = secret("EMAIL_PASSWORD")
SMTP_SERVER = secret("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(secret("SMTP_PORT", "587") or 587)
RECEIVER = secret("RECEIVER")

WeatherAPIKey = secret("WEATHERAPI_KEY")

appleid1 = secret("APPLE_ID_1")
applewachtwoord1 = secret("APPLE_PASSWORD_1")
appleid2 = secret("APPLE_ID_2")
applewachtwoord2 = secret("APPLE_PASSWORD_2")

spotify_client_id = secret("SPOTIFY_CLIENT_ID")
spotify_client_secret = secret("SPOTIFY_CLIENT_SECRET")
spotify_redirect_uri = secret("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/callback")

SERPER_API_KEY = secret("SERPER_API_KEY")
