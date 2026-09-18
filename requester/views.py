import uuid

import requests as http_requests
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification, Request, Session, UserProfile
from .serializers import (
    DJRequestSerializer,
    DJStatusUpdateSerializer,
    NotificationInboxSerializer,
    PublicRequestSerializer,
    RecipientRequestSerializer,
    SessionSerializer,
    SongRequestSerializer,
    UserProfileSerializer,
)
from .utils import SpotifyTokenError, get_spotify_app_token, get_valid_spotify_token

SPOTIFY_SEARCH_URL = 'https://api.spotify.com/v1/search'


def _pick_album_art(images):
    """Smallest image at or under 300px, else the first available, else None."""
    if not images:
        return None
    return next(
        (img['url'] for img in images if (img.get('height') or 0) <= 300),
        images[0]['url'],
    )


def _get_session_or_error(session_id):
    """
    Resolve a ?session_id= query param to a Session.

    Returns (session, error_response); exactly one is None. Query params are
    not run through a URL converter, so an unvalidated value would reach the
    UUIDField and raise a bare Django ValidationError (a 500, since DRF only
    translates Http404/PermissionDenied/APIException). Validate it here first.
    """
    if not session_id:
        return None, Response(
            {'error': 'A ?session_id= query parameter is required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        uuid.UUID(str(session_id))
    except (ValueError, AttributeError, TypeError):
        return None, Response(
            {'error': 'session_id must be a valid UUID.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return get_object_or_404(Session, id=session_id), None


class SessionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sessions = Session.objects.filter(dj=request.user).order_by('-created_at')
        return Response(SessionSerializer(sessions, many=True).data)

    def post(self, request):
        serializer = SessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        session = serializer.save(dj=request.user)
        return Response(SessionSerializer(session).data, status=status.HTTP_201_CREATED)


class SessionDetailView(APIView):
    """Retrieve or delete a single session. Owner-only."""

    permission_classes = [IsAuthenticated]

    def _get_owned_session(self, request, id):
        session = get_object_or_404(Session, id=id)
        if session.dj != request.user:
            return None, Response(
                {'error': 'You do not own this session.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return session, None

    def get(self, request, id):
        session, error = self._get_owned_session(request, id)
        if error:
            return error
        return Response(SessionSerializer(session).data)

    def delete(self, request, id):
        session, error = self._get_owned_session(request, id)
        if error:
            return error
        session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


_TRIGGER_MESSAGES = {
    'thought': "Someone listened to '{song_title}' by {artist_name} and thought about you",
    'reminded': "'{song_title}' by {artist_name} reminded someone of you",
}

class SetInactiveSessionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, id):
        return self.patch(request, id)

    def patch(self, request, id):
        session = get_object_or_404(Session, id=id)
        if session.dj != request.user:
            return Response(
                {'error': 'You do not own this session.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        session.is_active = False
        session.save()
        return Response({'status': 'Session marked as inactive'}, status=status.HTTP_200_OK)

class SongRequestView(APIView):
    def post(self, request):
        serializer = SongRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        song_request = serializer.save()

        # A dedication to someone who isn't on the app has no one to notify.
        # It still reaches the DJ's queue and still gets announced and played.
        if song_request.recipient_user_id is not None:
            template = _TRIGGER_MESSAGES.get(song_request.trigger_type, "Someone requested a song for you")
            message = template.format(
                song_title=song_request.song_title,
                artist_name=song_request.artist_name,
            )
            Notification.objects.create(
                request=song_request,
                recipient_tag=song_request.recipient_tag,
                message_text=message,
            )

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class UserNotificationInboxView(APIView):
    """
    The caller's own inbox.

    The recipient is taken from the authenticated user, never from a query
    parameter: a handle is public (it is how people are addressed, and it is
    discoverable through user search), so trusting a caller-supplied ?tag= let
    anyone read anyone else's dedications — and, because reading marks them
    read, quietly consumed them before the real recipient ever saw them.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Match on the dedication's resolved account rather than the tag string,
        # so a later handle change or reissue can never redirect an inbox.
        notifications = (
            Notification.objects
            .filter(request__recipient_user=request.user)
            .select_related('request')
            .order_by('-created_at')
        )

        # Serialize before marking read, so is_read reflects the state the
        # caller is actually being shown.
        data = NotificationInboxSerializer(notifications, many=True).data
        Notification.objects.filter(
            request__recipient_user=request.user, is_read=False
        ).update(is_read=True)
        return Response(data)


class SpotifyTrackSearchView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        q = request.query_params.get('q', '').strip()
        if not q:
            return Response(
                {'error': 'A ?q= query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            access_token = get_spotify_app_token()
        except SpotifyTokenError as e:
            return Response({'error': str(e)}, status=status.HTTP_502_BAD_GATEWAY)

        spotify_response = http_requests.get(
            SPOTIFY_SEARCH_URL,
            headers={'Authorization': f'Bearer {access_token}'},
            params={'q': q, 'type': 'track', 'limit': 5},
            timeout=5,
        )

        if not spotify_response.ok:
            return Response(
                {'error': 'Spotify search failed. Please try again.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        tracks = spotify_response.json().get('tracks', {}).get('items', [])
        results = [
            {
                'track_id': t['id'],
                'track_name': t['name'],
                'artist_name': ', '.join(a['name'] for a in t['artists']),
                'album_name': t['album']['name'],
                # Spotify may omit 'images' entirely, and may return height: null
                # on an image — treat a null height as 0 rather than comparing it.
                'album_art_url': _pick_album_art(t['album'].get('images') or []),
            }
            for t in tracks
        ]
        return Response(results)


class DJQueueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        session, error = _get_session_or_error(request.query_params.get('session_id'))
        if error:
            return error

        if session.dj != request.user:
            return Response(
                {'error': 'You do not own this session.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        queue = (
            Request.objects
            .filter(session=session, status='pending')
            .order_by('created_at')
        )
        serializer = DJRequestSerializer(queue, many=True)
        return Response(serializer.data)


class VenueFeedView(APIView):
    """
    The wall display for one session. Owner-only, and live sessions only.

    Session IDs are handed to every attendee so they can send requests, so an
    unauthenticated feed keyed on the session ID was readable by anyone holding
    the QR code — from anywhere, and forever, since nothing expired it. It names
    recipients, which under free-text dedications can be a physical description
    of someone in the room. The big screen renders through the DJ's session
    instead, and the feed goes dark when the night ends.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        session, error = _get_session_or_error(request.query_params.get('session_id'))
        if error:
            return error

        if session.dj != request.user:
            return Response(
                {'error': 'You do not own this session.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not session.is_active:
            return Response(
                {'error': 'This session has ended.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        feed = (
            Request.objects
            .filter(session=session)
            .order_by('-created_at')[:20]
        )
        serializer = PublicRequestSerializer(feed, many=True)
        return Response(serializer.data)


class DJRequestUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, id):
        song_request = get_object_or_404(Request, id=id)
        if song_request.session.dj != request.user:
            return Response(
                {'error': 'You do not own the session this request belongs to.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = DJStatusUpdateSerializer(song_request, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


class UserSearchView(APIView):
    def get(self, request):
        q = request.query_params.get('q', '').strip()
        if not q:
            return Response(
                {'error': 'A ?q= query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        handle_query = q.lstrip('@')

        profiles = (
            UserProfile.objects
            .filter(Q(display_name__icontains=q) | Q(app_handle__icontains=handle_query))
            .order_by('display_name')[:5]
        )
        serializer = UserProfileSerializer(profiles, many=True)
        return Response(serializer.data)


class MyRequestsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        requests_qs = (
            Request.objects
            .filter(recipient_user=request.user)
            .order_by('-created_at')
        )
        return Response(RecipientRequestSerializer(requests_qs, many=True).data)


SPOTIFY_PLAY_URL = 'https://api.spotify.com/v1/me/player/play'


class DJPlayView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        request_id = request.data.get('request_id')
        if not request_id:
            return Response(
                {'error': 'request_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        song_request = get_object_or_404(Request, id=request_id)

        if song_request.session.dj != request.user:
            return Response(
                {'error': 'You do not own the session this request belongs to.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not song_request.spotify_track_id:
            return Response(
                {'error': 'This request has no Spotify track ID — it cannot be played directly.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            access_token = get_valid_spotify_token(request.user)
        except SpotifyTokenError as e:
            return Response({'error': str(e)}, status=status.HTTP_401_UNAUTHORIZED)

        spotify_response = http_requests.put(
            SPOTIFY_PLAY_URL,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
            },
            json={'uris': [f'spotify:track:{song_request.spotify_track_id}']},
            timeout=5,
        )

        if spotify_response.status_code == 204:
            song_request.status = 'played'
            song_request.save(update_fields=['status'])
            return Response({'status': 'Playback started.'}, status=status.HTTP_200_OK)

        if spotify_response.status_code == 404:
            return Response(
                {'error': 'No active Spotify device found. Open Spotify on your device first.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if spotify_response.status_code == 403:
            return Response(
                {'error': 'Spotify Premium is required to control playback.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(
            {'error': 'Spotify playback request failed. Please try again.'},
            status=status.HTTP_502_BAD_GATEWAY,
        )
