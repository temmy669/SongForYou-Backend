from rest_framework import serializers
from .models import Notification, Request, Session, UserProfile


class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Session
        fields = ['id', 'dj_name', 'venue_name', 'is_active', 'created_at']
        read_only_fields = ['id', 'is_active', 'created_at']


class PublicSessionSerializer(serializers.ModelSerializer):
    """
    What an attendee is allowed to know about a session before sending anything.

    Deliberately minimal: enough to confirm they scanned a real, live code and
    to name the room back to them. No DJ account, no request history.
    """

    class Meta:
        model = Session
        fields = ['id', 'dj_name', 'venue_name', 'is_active']
        read_only_fields = fields


class SongRequestSerializer(serializers.ModelSerializer):
    """
    Two recipient paths, chosen by the sender via `knows_handle`.

    knows_handle=True  -> recipient_tag is an app handle. It must resolve to a
                          real account here, at creation time, or the request is
                          rejected. The dedication is linked to that user and a
                          notification is delivered.
    knows_handle=False -> recipient_tag is free text naming someone who may not
                          use the app at all. Nothing is resolved, no
                          notification is delivered, and the DJ simply announces
                          the name before playing.
    """

    knows_handle = serializers.BooleanField(write_only=True)

    class Meta:
        model = Request
        fields = [
            'id', 'session', 'song_title', 'artist_name', 'album_art_url',
            'spotify_track_id', 'is_anonymous', 'recipient_tag', 'knows_handle',
            'custom_message', 'trigger_type', 'status', 'created_at',
        ]
        # recipient_user is resolved server-side from the handle; a client must
        # never be able to address a dedication at an account directly.
        read_only_fields = ['id', 'status', 'created_at']

    def validate_session(self, value):
        if not value.is_active:
            raise serializers.ValidationError("This session is no longer active.")
        return value

    def validate_recipient_tag(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A recipient is required.")
        return value

    def validate(self, attrs):
        """Resolve the recipient once, here, so create() cannot diverge from it."""
        tag = attrs.get('recipient_tag', '')

        if not attrs.pop('knows_handle'):
            # Free-text recipient: no account, no notification.
            attrs['recipient_user'] = None
            return attrs

        handle = tag.lstrip('@')
        profile = (
            UserProfile.objects
            .select_related('user')
            .filter(app_handle__iexact=handle)
            .first()
        )
        if profile is None:
            raise serializers.ValidationError({
                'recipient_tag': (
                    f"No user with the handle '@{handle}' exists. If you do not "
                    f"know their handle, send it with knows_handle=false and "
                    f"name them instead."
                )
            })

        # Normalise to the stored casing so the DJ announces the real handle.
        attrs['recipient_tag'] = f'@{profile.app_handle}'
        attrs['recipient_user'] = profile.user
        return attrs


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'request', 'recipient_tag', 'message_text', 'is_read', 'created_at']
        read_only_fields = ['id', 'created_at']


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ['display_name', 'app_handle', 'profile_image_url']


class NotificationInboxSerializer(serializers.ModelSerializer):
    song_title = serializers.CharField(source='request.song_title', read_only=True)
    artist_name = serializers.CharField(source='request.artist_name', read_only=True)
    album_art_url = serializers.URLField(source='request.album_art_url', read_only=True)

    class Meta:
        model = Notification
        fields = ['id', 'message_text', 'song_title', 'artist_name', 'album_art_url', 'is_read', 'created_at']


class DJRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Request
        fields = '__all__'


class DJStatusUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Request
        fields = ['id', 'song_title', 'artist_name', 'album_art_url', 'recipient_tag', 'trigger_type', 'is_anonymous', 'status', 'created_at']
        read_only_fields = ['id', 'song_title', 'artist_name', 'album_art_url', 'recipient_tag', 'trigger_type', 'is_anonymous', 'created_at']

    def validate_status(self, value):
        if value == 'pending':
            raise serializers.ValidationError("Status cannot be reset to pending.")
        return value


class PublicRequestSerializer(serializers.ModelSerializer):
    sender = serializers.SerializerMethodField()

    class Meta:
        model = Request
        # is_anonymous is intentionally excluded — the flag itself is internal
        fields = ['id', 'song_title', 'artist_name', 'recipient_tag', 'trigger_type', 'status', 'sender', 'created_at']

    def get_sender(self, obj):
        return 'Anonymous' if obj.is_anonymous else 'A User'


class RecipientRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Request
        fields = [
            'id', 'song_title', 'artist_name', 'album_art_url',
            'custom_message', 'trigger_type', 'status', 'created_at',
        ]
