import logging
import re
from urllib.parse import urlencode

from allauth.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from decouple import config
from django.http import HttpResponseRedirect
from django.utils.text import slugify

from .models import UserProfile

logger = logging.getLogger(__name__)


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