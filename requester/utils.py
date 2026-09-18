import base64
import datetime

import requests as http_requests
from allauth.socialaccount.models import SocialToken
from decouple import config
from django.utils import timezone

SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token'
_EXPIRY_BUFFER = datetime.timedelta(seconds=60)

# Module-level cache for the app-level client credentials token
_app_token: str | None = None
_app_token_expires_at: datetime.datetime | None = None


def get_spotify_app_token() -> str:
    """
    Returns a valid Spotify access token using the Client Credentials flow.
    No user login required — uses CLIENT_ID + CLIENT_SECRET directly.
    Token is cached in memory and refreshed automatically when it expires.
    """
    global _app_token, _app_token_expires_at

    now = datetime.datetime.now(tz=datetime.timezone.utc)
    if _app_token and _app_token_expires_at and (_app_token_expires_at - _EXPIRY_BUFFER) > now:
        return _app_token

    client_id = config('SPOTIFY_CLIENT_ID')
    client_secret = config('SPOTIFY_CLIENT_SECRET')
    credentials = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()

    response = http_requests.post(
        SPOTIFY_TOKEN_URL,
        headers={
            'Authorization': f'Basic {credentials}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        data={'grant_type': 'client_credentials'},
        timeout=5,
    )

    if not response.ok:
        raise SpotifyTokenError('Spotify app token request failed. Check your CLIENT_ID and CLIENT_SECRET.')

    data = response.json()
    _app_token = data['access_token']
    _app_token_expires_at = now + datetime.timedelta(seconds=data.get('expires_in', 3600))
    return _app_token


class SpotifyTokenError(Exception):
    """Raised when a valid Spotify token cannot be obtained."""
    def __init__(self, message, reauth_required=False):
        super().__init__(message)
        self.reauth_required = reauth_required


def get_valid_spotify_token(user):
    """
    Returns a valid Spotify access token string for the given user.
    Silently refreshes the token if it is expired or about to expire.
    Raises SpotifyTokenError if no linked account exists or the refresh fails.
    """
    token_obj = (
        SocialToken.objects
        .select_related('account')
        .filter(account__user=user, account__provider='spotify')
        .first()
    )

    if not token_obj:
        raise SpotifyTokenError(
            'No Spotify account linked to this user.',
            reauth_required=True,
        )

    # Check expiry with a 60-second buffer so we refresh slightly early
    is_expired = (
        token_obj.expires_at is not None
        and token_obj.expires_at - _EXPIRY_BUFFER <= timezone.now()
    )

    if not is_expired:
        return token_obj.token

    if not token_obj.token_secret:
        raise SpotifyTokenError(
            'Spotify token expired and no refresh token is available. Please log in again.',
            reauth_required=True,
        )

    refreshed = _refresh_spotify_token(token_obj)
    return refreshed


def _refresh_spotify_token(token_obj):
    client_id = config('SPOTIFY_CLIENT_ID')
    client_secret = config('SPOTIFY_CLIENT_SECRET')
    credentials = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()

    response = http_requests.post(
        SPOTIFY_TOKEN_URL,
        headers={
            'Authorization': f'Basic {credentials}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        data={
            'grant_type': 'refresh_token',
            'refresh_token': token_obj.token_secret,
        },
        timeout=5,
    )

    if not response.ok:
        raise SpotifyTokenError(
            'Spotify token refresh failed. Please log in again.',
            reauth_required=True,
        )

    data = response.json()
    token_obj.token = data['access_token']
    token_obj.expires_at = timezone.now() + datetime.timedelta(seconds=data.get('expires_in', 3600))

    # Spotify sometimes rotates the refresh token — persist it if so
    if 'refresh_token' in data:
        token_obj.token_secret = data['refresh_token']

    token_obj.save(update_fields=['token', 'token_secret', 'expires_at'])
    return token_obj.token
