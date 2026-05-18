# VoiceLinkVR Legacy Update Backend

This is an upgrade-only backend for old VRCLS clients. It uses only the Python standard library, so the Docker build does not install Python packages.

It intentionally keeps only the endpoints needed for old clients to start and receive an update prompt:

- `POST /login`
- `POST /api/login`
- `GET /latestVersionInfo`
- `GET /api/latestVersionInfo`

All other paths return `426 Upgrade Required`.

## Deploy

```bash
cd legacy_update_backend
docker compose up -d --build
```

Point the old client domain/path at this service. If existing old clients use a `baseurl` ending in `/api`, keep routing `/api/login` and `/api/latestVersionInfo` to this service. If some clients use a root `baseurl`, route `/login` and `/latestVersionInfo` too.

## Release Source

By default the service reads:

```text
https://cloudflarestorage.boyqiu001.top/app-releases/upload-manifest.json
```

To pin one installer, set both env vars:

```env
LEGACY_UPDATE_VERSION=0.7.0-beta28
LEGACY_UPDATE_PACKAGE_URL=https://cloudflarestorage.boyqiu001.top/app-releases/VRCLS-0.7.0-beta28-win-x64-setup.exe
```

The old client expects the misspelled `packgeURL` field, so this service returns it exactly.

## CDN Changelog

Old clients read the update dialog body from:

```text
https://cloudflarestorage.boyqiu001.top/VRCLS_changeLog.md
```

Update that object with the migration notice before routing production traffic to this backend.
