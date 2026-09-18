from allauth.socialaccount.providers.oauth2.views import OAuth2CallbackView
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import include, path
from decouple import config
from rest_framework.authtoken.models import Token

from requester.adapters import NotesSpotifyOAuth2Adapter


def health(request):
    """
    Liveness probe for Render's health check.

    Touches the database, because a web process that is up but cannot reach
    Postgres is not actually serving. Unauthenticated by necessity - the probe
    has no credentials - so it returns nothing but a status.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        return JsonResponse({'status': 'error', 'database': 'unreachable'}, status=503)
    return JsonResponse({'status': 'ok'})


@login_required
def dashboard(request):
    token, _ = Token.objects.get_or_create(user=request.user)
    frontend_url = config('FRONTEND_URL', default='http://localhost:3000')
    profile = getattr(request.user, 'profile', None)
    display_name = profile.display_name if profile else request.user.username
    app_handle = profile.app_handle if profile else ''
    return_to = request.GET.get('return_to', '')
    redirect_base = (
        f'{frontend_url}{return_to}'
        if return_to.startswith('/request/')
        else f'{frontend_url}/dashboard'
    )

    return HttpResponseRedirect(
        f'{redirect_base}?token={token.key}'
        f'&display_name={display_name}'
        f'&app_handle={app_handle}'
    )


urlpatterns = [
    path('health/', health, name='health'),
    path('admin/', admin.site.urls),

    # Our Spotify callback must be registered BEFORE allauth's, because Django
    # resolves URLs in order and the first match wins.
    #
    # Swapping the provider's `oauth2_adapter_class` does not work here:
    # allauth binds the callback view at import time with
    # `OAuth2CallbackView.adapter_view(SpotifyOAuth2Adapter)`, and that closure
    # keeps the original class no matter what the provider says later. Claiming
    # the route is the only injection point that actually takes effect.
    #
    # The path is identical to allauth's, so `reverse('spotify_callback')`
    # produces the same redirect URI either way.
    path(
        'accounts/spotify/login/callback/',
        OAuth2CallbackView.adapter_view(NotesSpotifyOAuth2Adapter),
        name='spotify_callback',
    ),
    path('accounts/', include('allauth.urls')),
    path('api/', include('requester.urls')),
    path('dashboard/', dashboard, name='dashboard'),
]
