#!/usr/bin/env bash
# Render build step. Runs on every deploy, before the web process starts.
#
# `set -o errexit` matters here: without it a failed migration still exits 0 and
# Render happily promotes a broken release.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

# Required. staticfiles/ is gitignored, so the repo Render clones has no static
# assets at all, and production uses CompressedManifestStaticFilesStorage, which
# raises on a missing manifest rather than degrading. Skip this and every admin
# and DRF browsable-API page 500s.
python manage.py collectstatic --no-input

# Render's free plan has no pre-deploy hook, so migrations run here. The build
# has DATABASE_URL, so this is the documented Render approach for Django.
python manage.py migrate --no-input
