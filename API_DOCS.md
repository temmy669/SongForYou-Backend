# songForyou — Backend API Reference

Anonymous song dedications for live venues. An attendee picks a song and sends it
to another attendee by app handle (`@st3m`). A DJ works the queue and plays tracks
through their own Spotify. The recipient is notified; the sender can stay anonymous.

- **Base URL (local):** `http://localhost:8000/api/`
- **All IDs** are UUID strings. **All timestamps** are ISO 8601 UTC.

---

## Authentication

Two mechanisms coexist.

**1. Spotify OAuth** — for DJs and for any user who wants an inbox identity.

```
GET /accounts/spotify/login/          → Spotify consent screen
                                      → /dashboard/ (backend)
                                      → <FRONTEND_URL>/dashboard?token=…&display_name=…&app_handle=…
```

The backend mints a DRF token and hands it to the frontend in the query string.
On first login a `UserProfile` is auto-created, with `app_handle` derived from the
Spotify display name (`"Dancefloor Sam!"` → `dancefloor_sam`, with `_1`, `_2`
suffixes on collision).

`/dashboard/` accepts a `?return_to=` parameter to resume an interrupted flow.
For safety only paths beginning `/request/` are honoured; anything else falls
back to `/dashboard`.

**2. DRF Token** — for authenticated API calls.

```
Authorization: Token <token>
```

### Which endpoints need a token

| Public (no auth) | Token required |
|---|---|
| `POST /api/requests/` | `GET/POST /api/sessions/` |
| `GET /api/users/search/` | `GET/DELETE /api/sessions/<id>/` |
| `GET /api/search/` | `POST/PATCH /api/sessions/<id>/deactivate/` |
| | `GET /api/requests/me/` |
| | `GET /api/notifications/` |
| | `PATCH /api/requests/<id>/` |
| | `GET /api/dj/queue/` |
| | `POST /api/dj/play/` |
| | `GET /api/venue/feed/` |

> **Note:** `GET /api/search/` (Spotify track search) is **public**. It uses an
> app-level Spotify token, so senders can search for songs without logging in.

---

## Common error shapes

| Status | Meaning | Body |
|---|---|---|
| `400` | Validation failure | `{"field": ["message"]}` or `{"error": "…"}` |
| `401` | Missing/invalid token | `{"detail": "…"}` |
| `403` | Authenticated, but not the owner | `{"error": "You do not own this session."}` |
| `404` | No such object | `{"detail": "Not found."}` |
| `502` | Spotify upstream failed | `{"error": "…"}` |

A malformed `session_id` query parameter returns **`400`**, not `404`:
`{"error": "session_id must be a valid UUID."}`

---

# Sessions (DJ)

A session is one night at one venue. Requests belong to a session.
**Every session endpoint is owner-only** — acting on another DJ's session returns `403`.

## List sessions

`GET /api/sessions/` — token required. Yours only, newest first.

```json
[{ "id": "uuid", "dj_name": "DJ K", "venue_name": "The Basement",
   "is_active": true, "created_at": "2026-06-08T12:00:00Z" }]
```

## Create a session

`POST /api/sessions/` — token required.

| Field | Type | Required |
|---|---|---|
| `dj_name` | string | yes |
| `venue_name` | string | yes |

`id`, `is_active` (defaults `true`) and `created_at` are server-set.
Returns `201` with the session body above.

## Retrieve one session

`GET /api/sessions/<id>/` — token required, owner only. Returns `200`.

## Delete a session

`DELETE /api/sessions/<id>/` — token required, owner only.
Returns `204` with no body. **Cascades**: deletes every request and notification
in that session.

## End a session

`POST /api/sessions/<id>/deactivate/` — token required, owner only.
(`PATCH` behaves identically.)

```json
{ "status": "Session marked as inactive" }
```

Once inactive, `POST /api/requests/` against it returns `400`. Deactivating is
the safe way to close a night; deleting destroys the history.

---

# Requests

## Submit a dedication

`POST /api/requests/` — **no auth**.

| Field | Type | Required | Notes |
|---|---|---|---|
| `session` | UUID | yes | Must be an **active** session |
| `song_title` | string | yes | |
| `artist_name` | string | yes | |
| `trigger_type` | enum | yes | `"thought"` or `"reminded"` |
| `knows_handle` | bool | yes | Does the sender know the recipient's handle? See below |
| `recipient_tag` | string | yes | A handle (`"@st3m"`) or free text (`"Sarah in the red dress"`) |
| `album_art_url` | URL | no | From the track-search result |
| `spotify_track_id` | string | no | **Required for `/api/dj/play/` to work** |
| `custom_message` | string | no | Free text from the sender |
| `is_anonymous` | bool | no | Defaults `true` |

```json
{
  "session": "uuid", "song_title": "Starboy", "artist_name": "The Weeknd",
  "album_art_url": "https://i.scdn.co/image/…",
  "spotify_track_id": "7MXVkk9YMctZqd1Srtv4MB",
  "recipient_tag": "@st3m", "knows_handle": true, "trigger_type": "reminded",
  "custom_message": "saw this and thought of you", "is_anonymous": true
}
```

Returns `201` with the created request (`status` is always `"pending"`).
Submitting to an inactive session returns `400`:
`{"session": ["This session is no longer active."]}`

## Who the dedication is for

`knows_handle` picks between two paths. Ask the sender outright - *"Do you know
their handle?"* - and send the answer.

**`knows_handle: true`** - `recipient_tag` is an app handle. It is resolved to a
real account **at creation time**. If no such handle exists the request is
rejected with `400`, so a dedication can never silently go nowhere:

```json
{"recipient_tag": ["No user with the handle '@ghost' exists. If you do not know
their handle, send it with knows_handle=false and name them instead."]}
```

Matching is case-insensitive, and the stored tag is normalised to the handle's
real casing - send `@SARAH`, get back `@sarah`. A notification is delivered.

**`knows_handle: false`** - `recipient_tag` is free text naming someone who may
not use the app at all: `"Sarah in the red dress"`. Nothing is resolved and no
notification is created - there is no one to notify. The dedication still
reaches the DJ's queue, and the DJ still announces the name and plays the song.
This is the whole point of the flag: a sender is never blocked by whether their
recipient happens to have the app.

> Free text is **never** matched against accounts, at creation or later. Sending
> `"sarah"` with `knows_handle: false` does not reach `@sarah` even though the
> string matches. Use `true` when you mean the account.

**Side effect (handle path only):** creates one `Notification`, with
`message_text` built from `trigger_type`:

| `trigger_type` | `message_text` |
|---|---|
| `thought` | `Someone listened to '<song>' by <artist> and thought about you` |
| `reminded` | `'<song>' by <artist> reminded someone of you` |

> Pass `spotify_track_id` whenever you have it. Without it the DJ can see the
> request but cannot play it — `/api/dj/play/` will reject it with `400`.

## Requests sent to me

`GET /api/requests/me/` — token required.

Matched against `@<your app_handle>`, newest first. Sender identity is never
included, anonymous or not.

```json
[{ "id": "uuid", "song_title": "Starboy", "artist_name": "The Weeknd",
   "album_art_url": "https://i.scdn.co/image/…",
   "custom_message": "saw this and thought of you",
   "trigger_type": "reminded", "status": "pending",
   "created_at": "2026-06-08T12:00:00Z" }]
```

Returns `404` if the user has no `UserProfile`.

## Update a request's status

`PATCH /api/requests/<id>/` — token required, must own the parent session.

| Field | Values |
|---|---|
| `status` | `"played"` or `"rejected"` |

Every other field is read-only. Resetting to `"pending"` returns `400`
(`"Status cannot be reset to pending."`). Returns `200` with the updated request.

---

# Notifications

## Inbox

`GET /api/notifications/` — **token required**. No parameters.

Returns the **caller's own** dedications, newest first, and **marks all unread
ones as read as a side effect**. There is no separate read-receipt call.

The recipient is taken from the authenticated token, never from a parameter: a
handle is public and discoverable via `/api/users/search/`, so a caller-supplied
tag would let anyone read — and silently consume — someone else's dedications.
Passing `?tag=` does nothing; you only ever see your own.

`is_read` in the response reflects the state **before** this call, so a
dedication being seen for the first time comes back `is_read: false` and is
marked read immediately after.

```json
[{ "id": "uuid",
   "message_text": "'Starboy' by The Weeknd reminded someone of you",
   "song_title": "Starboy", "artist_name": "The Weeknd",
   "album_art_url": "https://i.scdn.co/image/…",
   "is_read": true, "created_at": "2026-06-08T12:00:00Z" }]
```

> **Two caveats for frontend work.** Because read-marking happens during the
> `GET`, `is_read` is effectively always `true` in the response — you cannot
> build an unread badge from this payload today. And a plain `GET` is
> destructive, so do not call it speculatively (prefetch, retry, double-render)
> unless you intend to consume the unread state.
>
> Missing `?tag=` returns `400`.

---

# Search

## Spotify track search

`GET /api/search/?q=Starboy` — **no auth**. Top 5 tracks.

```json
[{ "track_id": "7MXVkk9YMctZqd1Srtv4MB", "track_name": "Starboy",
   "artist_name": "The Weeknd, Daft Punk", "album_name": "Starboy",
   "album_art_url": "https://i.scdn.co/image/…" }]
```

`artist_name` is a comma-joined string of all credited artists.
`album_art_url` prefers an image ≤300px, falls back to the first available, and
is `null` when the album has no artwork. Feed `track_id` into `spotify_track_id`
and `album_art_url` straight into `POST /api/requests/`.

Missing `?q=` → `400`. Spotify unreachable or credentials wrong → `502`.

## User search (recipient autocomplete)

`GET /api/users/search/?q=sam` — **no auth**. Top 5, matched on display name or
handle, ordered by display name. A leading `@` is stripped, so `?q=@sam` works.

```json
[{ "display_name": "sT3m", "app_handle": "st3m",
   "profile_image_url": "https://i.scdn.co/image/…" }]
```

Note `app_handle` comes back **without** `@`, but `recipient_tag` must be sent
**with** it — prepend `@` when submitting.

Missing `?q=` → `400`.

---

# DJ console

## Queue

`GET /api/dj/queue/?session_id=<uuid>` — token required, owner only.

Pending requests for the session, **oldest first** (play order). This is the
privileged view: it returns all fields, including `is_anonymous` and
`custom_message`.

```json
[{ "id": "uuid", "session": "uuid", "song_title": "Starboy",
   "artist_name": "The Weeknd", "album_art_url": "https://i.scdn.co/image/…",
   "spotify_track_id": "7MXVkk9YMctZqd1Srtv4MB", "recipient_tag": "@st3m",
   "custom_message": "saw this and thought of you", "trigger_type": "reminded",
   "is_anonymous": true, "status": "pending",
   "created_at": "2026-06-08T12:00:00Z" }]
```

Missing or malformed `session_id` → `400`. Another DJ's session → `403`.

## Play a request

`POST /api/dj/play/` — token required, must own the parent session.

```json
{ "request_id": "uuid" }
```

Starts playback on the DJ's **own active Spotify device** using their OAuth token
(refreshed automatically). On success the request's `status` is set to
`"played"`, so it leaves the queue.

| Status | Meaning |
|---|---|
| `200` | `{"status": "Playback started."}` — request is now `played` |
| `400` | No `request_id`, or the request has no `spotify_track_id` |
| `401` | No linked Spotify account, or the token needs re-auth |
| `403` | Not your session, **or** Spotify Premium required |
| `404` | No active Spotify device — open Spotify somewhere first |
| `502` | Spotify rejected the request for another reason |

The request stays `pending` on every failure path, so a `404`/`403` is safely
retryable once the DJ opens Spotify.

> `403` is overloaded: ownership failure returns `{"error": "You do not own …"}`,
> Premium failure returns `{"error": "Spotify Premium is required …"}`.
> Branch on the message, not the status code.

---

# Public venue display

`GET /api/venue/feed/?session_id=<uuid>` — **token required, owner-only**.
Last 20, newest first.

The big screen renders through the DJ's own session. Session IDs are handed to
every attendee so they can send requests, so an unauthenticated feed keyed on
the session ID was readable by anyone holding the QR code, from anywhere — and
it names recipients, which under free-text dedications can be a physical
description of someone in the room.

- Another DJ's session — `403`
- A deactivated session — `404 {"error": "This session has ended."}`

The feed goes dark when the night ends; it is not a permanent record.

Sender identity is still filtered: `is_anonymous` is **never exposed** — not even
as a flag — and the sender is flattened to a label. `custom_message`,
`album_art_url` and `spotify_track_id` are omitted.

```json
[{ "id": "uuid", "song_title": "Starboy", "artist_name": "The Weeknd",
   "recipient_tag": "@st3m", "trigger_type": "reminded", "status": "pending",
   "sender": "Anonymous", "created_at": "2026-06-08T12:00:00Z" }]
```

`sender` is `"Anonymous"` when `is_anonymous` is true, otherwise `"A User"`.
There is currently no path by which the public feed reveals a real sender name.

---

# Flows

**Sending a dedication**

1. `GET /api/users/search/?q=…` → recipient autocomplete (prepend `@` to `app_handle`)
2. `GET /api/search/?q=…` → track picker (no login needed)
3. Ask *"do you know their handle?"* -> sets `knows_handle`, and either a handle
   picker (`GET /api/users/search/`) or a free-text name field
4. Pick `trigger_type`, optional `custom_message`, anonymity
5. `POST /api/requests/` with `spotify_track_id` **and** `album_art_url` from step 2

**Checking an inbox**

1. `GET /api/notifications/` — logged in; remember this marks everything read
2. Or `GET /api/requests/me/` — non-destructive, richer payload

**DJ console**

1. Spotify login → token from the `/dashboard/` redirect
2. `POST /api/sessions/` to open the night
3. Poll `GET /api/dj/queue/?session_id=…`
4. `POST /api/dj/play/` to play (auto-marks played), or `PATCH /api/requests/<id>/` to reject
5. `POST /api/sessions/<id>/deactivate/` to close

**Venue screen** — poll `GET /api/venue/feed/?session_id=…` with the DJ's token.

> There is no WebSocket or push. The DJ console and venue screen must poll;
> ~3–5s is reasonable.

---

# Data types

| Field | Format |
|---|---|
| `id`, `session` | UUID string |
| `created_at` | ISO 8601 UTC, e.g. `"2026-06-08T12:00:00Z"` |
| `recipient_tag` | `"@st3m"` when `knows_handle`, else free text |
| `app_handle` | **without** `@`, e.g. `"st3m"` |
| `status` | `"pending"` \| `"played"` \| `"rejected"` |
| `trigger_type` | `"thought"` \| `"reminded"` |

---

# Known gaps

Current behaviour, so the frontend isn't built on wrong assumptions:

1. **No rate limiting** on any endpoint, including public track search.
2. **No "played" notification.** The recipient is told a song was dedicated, never
   that it actually played.
3. **Polling only** — no realtime transport.
4. **Free-text recipients are unmoderated.** `recipient_tag` is announced aloud by
   the DJ and shown on the venue screen with no filtering.
