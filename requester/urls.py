from django.urls import path
from .views import (
    DJPlayView,
    DJQueueView,
    DJRequestUpdateView,
    MyRequestsView,
    VenueFeedView,
    SessionDetailView,
    SessionView,
    SetInactiveSessionView,
    SongRequestView,
    SpotifyTrackSearchView,
    UserNotificationInboxView,
    UserSearchView,
)

urlpatterns = [
    path('sessions/', SessionView.as_view(), name='sessions'),
    path('sessions/<uuid:id>/deactivate/', SetInactiveSessionView.as_view(), name='deactivate-session'),
    path('sessions/<uuid:id>/', SessionDetailView.as_view(), name='session-detail'),
    path('requests/', SongRequestView.as_view(), name='song-request'),
    path('requests/me/', MyRequestsView.as_view(), name='my-requests'),
    path('requests/<uuid:id>/', DJRequestUpdateView.as_view(), name='dj-request-update'),
    path('notifications/', UserNotificationInboxView.as_view(), name='notifications'),
    path('users/search/', UserSearchView.as_view(), name='user-search'),
    path('search/', SpotifyTrackSearchView.as_view(), name='spotify-track-search'),
    path('dj/queue/', DJQueueView.as_view(), name='dj-queue'),
    path('dj/play/', DJPlayView.as_view(), name='dj-play'),
    path('venue/feed/', VenueFeedView.as_view(), name='venue-feed'),
]
