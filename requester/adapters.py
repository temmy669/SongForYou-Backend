import logging
import re
from urllib.parse import urlencode

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter, get_adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Error
from allauth.socialaccount.providers.spotify.views import SpotifyOAuth2Adapter
from decouple import config
from django.http import HttpResponseRedirect
from django.utils.text import slugify

from .models import UserProfile

logger = logging.getLogger(__name__)


class NotesSpotifyOAuth2Adapter(SpotifyOAuth2Adapter):
    """
    Replaces allauth's Spotify profile fetch, which fails in two ways.

    1. It sends the access token as a QUERY PARAMETER
       (`params={"access_token": ...}`). Spotify's Web API expects it in an
       Authorization header, exactly as requester/utils.py already does for
       track search.

    2. It never checks the response status. It calls .json() on whatever comes
       back and hands it to extract_uid(), which does data["id"] — so any error
       body from Spotify surfaces as `KeyError: 'id'` and a 500, with the real
       reason discarded.

    Spotify returns a perfectly good explanation in that body. A new app is in
    Development Mode and only admits accounts listed under User Management;
    everyone else gets 403 "User not registered in the Developer Dashboard".
    That is a configuration problem the DJ can fix, so it needs to reach them
    as a message rather than a stack trace.
    """

    def complete_login(self, request, app, token, **kwargs):
        with get_adapter().get_requests_session() as sess:
            resp = sess.get(
                self.profile_url,
                headers={'Authorization': f'Bearer {token.token}'},
                timeout=10,
            )

        if not resp.ok:
            detail = ''
            try:
                payload = resp.json()
                error = payload.get('error')
                if isinstance(error, dict):
                    detail = error.get('message', '')
                elif isinstance(error, str):
                    detail = error
            except ValueError:
                detail = resp.text[:200]

            logger.error(
                'Spotify profile request failed: status=%s detail=%s',
                resp.status_code,
                detail or '(no message)',
            )
            raise OAuth2Error(detail or f'Spotify returned {resp.status_code}.')

        extra_data = resp.json()
        if 'id' not in extra_data:
            logger.error('Spotify profile had no id. keys=%s', sorted(extra_data))
            raise OAuth2Error('Spotify did not return an account id.')

        return self.get_provider().sociallogin_from_response(request, extra_data)


class SpotifySocialAdapter(DefaultSocialAccountAdapter):

    def on_authentication_error(
        self,
        request,
        provider,
        error=None,
        exception=None,
        extra_context=None,
    ):
        """
        Send a failed Spotify login back to the frontend, and log why.

        allauth's own behaviour is to render a bare 'Third-Party Login Failure'
        page with no explanation and no way back into the app. There is no
        setting that changes this — SOCIALACCOUNT_AUTHENTICATION_ERROR_URL is
        not a real allauth setting, despite reading like one — so the redirect
        has to happen here.

        The full error is logged server-side; only the short OAuth error code
        travels to the frontend, since `extra_context` can carry request and
        token details that should not end up in a URL or browser history.
        """
        logger.error(
            'Spotify login failed: provider=%s error=%s exception=%r context=%s',
            getattr(provider, 'id', provider),
            error,
            exception,
            extra_context,
        )

        frontend_url = config('FRONTEND_URL', default='http://localhost:3000')
        params = {'auth_error': error or 'unknown'}
        raise ImmediateHttpResponse(
            HttpResponseRedirect(f'{frontend_url}/?{urlencode(params)}')
        )


    def save_user(self, request, sociallogin, form=None):
        """
        This method is called when a user logs in via Spotify for the first time.
        It saves the base User and then generates our custom UserProfile.
        """
        # 1. Save the default user instance first
        user = super().save_user(request, sociallogin, form)
        
        # 2. Extract raw data provided by the Spotify API
        extra_data = sociallogin.account.extra_data
        spotify_id = extra_data.get('id')
        display_name = extra_data.get('display_name', 'dancefloor_user')
        
        # Extract profile image if available
        images = extra_data.get('images', [])
        profile_image = images[0].get('url') if images else None

        # 3. Clean and generate the custom Instagram-style handle
        # Turn "Dancefloor Sam!" into "dancefloor_sam"
        base_handle = slugify(display_name).replace('-', '_')
        if not base_handle:
            base_handle = "user"
            
        # 4. Fallback Loop: If 'dancefloor_sam' exists, try 'dancefloor_sam_1', 'dancefloor_sam_2'
        unique_handle = base_handle
        counter = 1
        while UserProfile.objects.filter(app_handle=unique_handle).exists():
            unique_handle = f"{base_handle}_{counter}"
            counter += 1

        # 5. Save the data to our custom UserProfile table
        UserProfile.objects.create(
            user=user,
            spotify_id=spotify_id,
            display_name=display_name,
            profile_image_url=profile_image,
            app_handle=unique_handle
        )
        
        return user