"""Tests for recipient resolution and dedication privacy."""

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import Notification, Request, Session, UserProfile


def make_user(username, handle):
    user = User.objects.create_user(username=username, password='pw')
    UserProfile.objects.create(
        user=user, spotify_id=f'sp_{username}',
        display_name=username.title(), app_handle=handle,
    )
    return user


class RecipientResolutionTests(APITestCase):
    """A dedication either resolves to an account at creation, or never does."""

    def setUp(self):
        self.dj = make_user('dj', 'dj_sam')
        self.sarah = make_user('sarah', 'sarah')
        self.session = Session.objects.create(
            dj=self.dj, dj_name='Sam', venue_name='The Loft'
        )

    def _payload(self, **over):
        base = {
            'session': str(self.session.id),
            'song_title': 'Starboy',
            'artist_name': 'The Weeknd',
            'trigger_type': 'reminded',
            'recipient_tag': '@sarah',
            'knows_handle': True,
        }
        base.update(over)
        return base

    def test_known_handle_links_account_and_notifies(self):
        res = self.client.post('/api/requests/', self._payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        req = Request.objects.get(id=res.data['id'])
        self.assertEqual(req.recipient_user, self.sarah)
        self.assertEqual(Notification.objects.filter(request=req).count(), 1)

    def test_handle_is_normalised_to_stored_casing(self):
        res = self.client.post(
            '/api/requests/', self._payload(recipient_tag='@SARAH'), format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Request.objects.get(id=res.data['id']).recipient_tag, '@sarah')

    def test_unknown_handle_is_rejected(self):
        res = self.client.post(
            '/api/requests/', self._payload(recipient_tag='@ghost'), format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Request.objects.count(), 0)

    def test_free_text_recipient_is_accepted_without_notification(self):
        """Someone not on the app can still be dedicated to."""
        res = self.client.post('/api/requests/', self._payload(
            recipient_tag='Sarah in the red dress', knows_handle=False,
        ), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        req = Request.objects.get(id=res.data['id'])
        self.assertIsNone(req.recipient_user)
        self.assertEqual(Notification.objects.count(), 0)

    def test_free_text_matching_a_handle_is_not_silently_linked(self):
        """knows_handle=False means 'not an account' - even if the text matches one."""
        res = self.client.post('/api/requests/', self._payload(
            recipient_tag='sarah', knows_handle=False,
        ), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(Request.objects.get(id=res.data['id']).recipient_user)
        self.assertEqual(Notification.objects.count(), 0)

    def test_dj_sees_free_text_recipient_in_queue(self):
        """The DJ must still be able to announce a non-user dedication."""
        self.client.post('/api/requests/', self._payload(
            recipient_tag='Sarah in the red dress', knows_handle=False,
        ), format='json')

        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=self.dj).key}'
        )
        res = self.client.get(f'/api/dj/queue/?session_id={self.session.id}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data[0]['recipient_tag'], 'Sarah in the red dress')


class InboxPrivacyTests(APITestCase):
    def setUp(self):
        self.dj = make_user('dj', 'dj_sam')
        self.sarah = make_user('sarah', 'sarah')
        self.mallory = make_user('mallory', 'mallory')
        session = Session.objects.create(dj=self.dj, dj_name='S', venue_name='V')
        self.req = Request.objects.create(
            session=session, song_title='Starboy', artist_name='The Weeknd',
            recipient_tag='@sarah', recipient_user=self.sarah, trigger_type='reminded',
        )
        Notification.objects.create(
            request=self.req, recipient_tag='@sarah', message_text='for you',
        )

    def auth(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}'
        )

    def test_inbox_requires_authentication(self):
        self.assertEqual(
            self.client.get('/api/notifications/').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_recipient_reads_own_inbox(self):
        self.auth(self.sarah)
        res = self.client.get('/api/notifications/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

    def test_other_user_cannot_read_it_even_with_the_tag(self):
        self.auth(self.mallory)
        res = self.client.get('/api/notifications/?tag=@sarah')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, [])

    def test_other_user_cannot_consume_unread_state(self):
        """Reading someone else's inbox must not mark their dedications read."""
        self.auth(self.mallory)
        self.client.get('/api/notifications/?tag=@sarah')
        self.assertFalse(Notification.objects.get(request=self.req).is_read)

    def test_own_read_returns_unread_then_marks_read(self):
        self.auth(self.sarah)
        res = self.client.get('/api/notifications/')
        self.assertFalse(res.data[0]['is_read'], 'first read should show as unread')
        self.assertTrue(Notification.objects.get(request=self.req).is_read)

    def test_my_requests_returns_only_own_dedications(self):
        self.auth(self.mallory)
        res = self.client.get('/api/requests/me/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, [])


class VenueFeedTests(APITestCase):
    def setUp(self):
        self.dj = make_user('dj', 'dj_sam')
        self.other_dj = make_user('other', 'other_dj')
        self.session = Session.objects.create(dj=self.dj, dj_name='S', venue_name='V')
        Request.objects.create(
            session=self.session, song_title='Starboy', artist_name='The Weeknd',
            recipient_tag='Sarah in the red dress', trigger_type='reminded',
        )

    def auth(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}'
        )

    def url(self):
        return f'/api/venue/feed/?session_id={self.session.id}'

    def test_feed_requires_authentication(self):
        self.assertEqual(
            self.client.get(self.url()).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_owner_sees_feed(self):
        self.auth(self.dj)
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

    def test_non_owner_is_forbidden(self):
        self.auth(self.other_dj)
        self.assertEqual(
            self.client.get(self.url()).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_feed_expires_when_session_ends(self):
        self.session.is_active = False
        self.session.save()
        self.auth(self.dj)
        self.assertEqual(
            self.client.get(self.url()).status_code, status.HTTP_404_NOT_FOUND
        )

    def test_feed_never_exposes_sender_identity(self):
        self.auth(self.dj)
        row = self.client.get(self.url()).data[0]
        self.assertNotIn('is_anonymous', row)
        self.assertNotIn('custom_message', row)


class PublicSessionTests(APITestCase):
    """An attendee who scanned a QR code, before sending anything."""

    def setUp(self):
        self.dj = make_user('dj', 'dj_sam')
        self.session = Session.objects.create(
            dj=self.dj, dj_name='Sam', venue_name='The Loft'
        )

    def url(self, sid=None):
        return f'/api/sessions/{sid or self.session.id}/public/'

    def test_readable_without_a_token(self):
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['venue_name'], 'The Loft')
        self.assertEqual(res.data['dj_name'], 'Sam')
        self.assertTrue(res.data['is_active'])

    def test_ended_session_returns_200_not_404(self):
        """The page must distinguish 'ended' from 'wrong link'."""
        self.session.is_active = False
        self.session.save()
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data['is_active'])

    def test_unknown_session_is_404(self):
        import uuid
        self.assertEqual(
            self.client.get(self.url(uuid.uuid4())).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_leaks_nothing_beyond_the_poster(self):
        """No DJ account, no request history, no counts."""
        self.assertEqual(
            set(self.client.get(self.url()).data.keys()),
            {'id', 'dj_name', 'venue_name', 'is_active'},
        )
