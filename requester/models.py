import uuid
from django.contrib.auth.models import User
from django.db import models


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    spotify_id = models.CharField(max_length=255, unique=True)
    display_name = models.CharField(max_length=255)
    profile_image_url = models.URLField(blank=True, null=True)
    
    # Instagram-style handle (e.g., dancefloor_sam) saved WITHOUT the '@' symbol in the DB
    app_handle = models.CharField(max_length=50, unique=True, db_index=True)

    def __str__(self):
        return f"{self.display_name} (@{self.app_handle})"


class Session(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dj = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions')
    dj_name = models.CharField(max_length=100)
    venue_name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.dj_name} at {self.venue_name} ({self.created_at.strftime('%Y-%m-%d')})"


class Request(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('played', 'Played'),
        ('rejected', 'Rejected'),
    ]

    TRIGGER_CHOICES = [
        ('thought', 'Someone listened to this song and thought about you'),
        ('reminded', 'This song reminded someone of you'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='requests')
    song_title = models.CharField(max_length=255)
    artist_name = models.CharField(max_length=255)
    album_art_url = models.URLField(blank=True, null=True)
    spotify_track_id = models.CharField(max_length=100, blank=True, null=True)

    is_anonymous = models.BooleanField(default=True)

    # Display string the DJ announces. Either a handle ('@st3m') when the sender
    # knew one, or free text ('Sarah in the red dress') when they did not.
    recipient_tag = models.CharField(max_length=100, db_index=True)

    # Set only when the sender supplied a handle that resolved to a real account
    # at creation time. NULL means the dedication is for someone not on the app:
    # no notification is delivered, the DJ still announces and plays it.
    #
    # Resolution happens once, at creation. A free-text recipient is never
    # matched against accounts later, so a handle freed up and reissued to a new
    # user can't hand them an older dedication meant for someone else.
    recipient_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_requests',
    )
    custom_message = models.TextField(blank=True, null=True)
    trigger_type = models.CharField(max_length=20, choices=TRIGGER_CHOICES)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        display_name = "Anonymous" if self.is_anonymous else "A User"
        return f"{display_name} requested '{self.song_title}' for {self.recipient_tag}"


class Notification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name='notifications')
    recipient_tag = models.CharField(max_length=100, db_index=True)
    message_text = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notification for {self.recipient_tag} - Read: {self.is_read}"
