from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen


APP_NAME = "VoiceLinkVR Legacy Update Backend"
DEFAULT_MANIFEST_URL = "https://cloudflarestorage.boyqiu001.top/app-releases/upload-manifest.json"
DEFAULT_CHANGELOG_URL = "https://cloudflarestorage.boyqiu001.top/VRCLS_changeLog.md"

_release_cache: dict[str, Any] = {
    "expires_at": 0.0,
    "data": None,
}


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _cache_seconds() -> int:
    try:
        return max(1, int(_env("LEGACY_UPDATE_CACHE_SECONDS", "300")))
    except ValueError:
        return 300


def _fetch_timeout() -> float:
    try:
        return max(1.0, float(_env("LEGACY_UPDATE_FETCH_TIMEOUT", "10")))
    except ValueError:
        return 10.0


def _is_absolute_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _origin_from_url(url: str) -> str:
    if "://" not in url:
        return ""
    scheme, rest = url.split("://", 1)
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}/"


def _directory_url(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/" if "/" in url else url


def _pick_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return {}

    def score(item: dict[str, Any]) -> int:
        text = " ".join(
            str(item.get(key) or "").lower()
            for key in ("type", "runtime", "fileName", "relativePath", "remoteKey", "url", "downloadUrl")
        )
        value = 0
        if "win" in text:
            value += 10
        if "x64" in text:
            value += 5
        if "setup" in text or "installer" in text:
            value += 4
        if ".exe" in text:
            value += 3
        return value

    candidates = [item for item in artifacts if isinstance(item, dict)]
    if not candidates:
        return {}
    return max(candidates, key=score)


def _artifact_url(payload: dict[str, Any], artifact: dict[str, Any], manifest_url: str) -> str:
    for key in ("url", "downloadUrl"):
        direct = str(artifact.get(key) or "").strip()
        if _is_absolute_url(direct):
            return direct

    relative_path = str(artifact.get("relativePath") or artifact.get("fileName") or "").strip()
    base_url = str(payload.get("appCastBaseUrl") or payload.get("baseUrl") or "").strip()
    if base_url and relative_path:
        return urljoin(base_url.rstrip("/") + "/", relative_path.lstrip("/"))

    remote_key = str(artifact.get("remoteKey") or "").strip()
    if remote_key:
        return urljoin(_origin_from_url(base_url or manifest_url), remote_key.lstrip("/"))

    if relative_path:
        return urljoin(_directory_url(manifest_url), relative_path.lstrip("/"))

    return ""


def _load_release_from_manifest(manifest_url: str) -> dict[str, str]:
    request = UrlRequest(manifest_url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=_fetch_timeout()) as response:
        raw = response.read().decode("utf-8-sig")

    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("release manifest is not a JSON object")

    artifact = _pick_artifact(payload)
    download_url = _artifact_url(payload, artifact, manifest_url)
    version = str(
        _env("LEGACY_UPDATE_VERSION")
        or payload.get("displayVersion")
        or payload.get("version")
        or payload.get("internalVersion")
        or artifact.get("displayVersion")
        or artifact.get("version")
        or ""
    ).strip()

    if not version or not download_url:
        raise ValueError("release manifest did not contain version and download URL")

    return {"version": version, "packgeURL": download_url}


def _resolve_release() -> dict[str, str]:
    direct_url = _env("LEGACY_UPDATE_PACKAGE_URL") or _env("PACKAGE_URL")
    direct_version = _env("LEGACY_UPDATE_VERSION") or _env("LATEST_VERSION")
    if direct_url and direct_version:
        return {"version": direct_version, "packgeURL": direct_url}

    package_base_url = _env("PACKAGE_BASE_URL")
    package_type = _env("PACKAGE_TYPE")
    if package_base_url and direct_version:
        return {"version": direct_version, "packgeURL": f"{package_base_url}{direct_version}{package_type}"}

    manifest_url = _env("LEGACY_UPDATE_MANIFEST_URL", DEFAULT_MANIFEST_URL)
    return _load_release_from_manifest(manifest_url)


def _latest_release(force_refresh: bool = False) -> dict[str, str]:
    now = time.time()
    cached = _release_cache.get("data")
    if cached and not force_refresh and now < float(_release_cache.get("expires_at") or 0.0):
        return dict(cached)

    release = _resolve_release()
    _release_cache["data"] = dict(release)
    _release_cache["expires_at"] = now + _cache_seconds()
    return release


def _upgrade_required_payload() -> dict[str, Any]:
    return {
        "message": "旧版客户端服务已停止提供业务功能，请升级到最新客户端。",
        "upgradeRequired": True,
        "latestVersionInfo": "/api/latestVersionInfo",
    }


class LegacyUpdateHandler(BaseHTTPRequestHandler):
    server_version = APP_NAME

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_bytes(204, b"", "text/plain; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return

        if path in {"/latestVersionInfo", "/api/latestVersionInfo"}:
            try:
                release = _latest_release(force_refresh=str(query.get("refresh", ["false"])[0]).lower() == "true")
                self._send_json(200, release)
            except Exception as exc:  # pylint: disable=broad-except
                self._send_json(503, {"message": "version not defined", "detail": str(exc)})
            return

        if path in {"/VRCLS_changeLog.md", "/api/VRCLS_changeLog.md"}:
            self._send_text(200, _env("LEGACY_UPDATE_CHANGELOG_TEXT", _default_changelog_text()))
            return

        self._send_json(426, _upgrade_required_payload())

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in {"/login", "/api/login"}:
            payload = self._read_json_body()
            username = str(payload.get("username") or "").strip() if isinstance(payload, dict) else ""
            self._send_json(
                200,
                {
                    "message": "Login successful",
                    "access_token": f"upgrade-only:{username or 'legacy-client'}",
                    "token_type": "bearer",
                },
            )
            return

        self._send_json(426, _upgrade_required_payload())

    def do_PUT(self) -> None:  # noqa: N802
        self._send_json(426, _upgrade_required_payload())

    def do_PATCH(self) -> None:  # noqa: N802
        self._send_json(426, _upgrade_required_payload())

    def do_DELETE(self) -> None:  # noqa: N802
        self._send_json(426, _upgrade_required_payload())

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _read_json_body(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            content_length = 0
        if content_length <= 0:
            return {}
        try:
            raw = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except Exception:  # pylint: disable=broad-except
            return {}

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        self._send_bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send_text(self, status: int, text: str) -> None:
        self._send_bytes(status, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()
        if body:
            self.wfile.write(body)


def _default_changelog_text() -> str:
    return (
        "# VRCLS 新客户端迁移通知\n\n"
        "旧版客户端服务即将停止维护。请点击更新安装最新客户端；如果自动更新失败，"
        f"请打开 {_env('LEGACY_UPDATE_CHANGELOG_URL', DEFAULT_CHANGELOG_URL)} 查看手动下载说明。\n"
    )


def main() -> None:
    host = _env("HOST", "0.0.0.0")
    port = int(_env("PORT", "8980"))
    server = ThreadingHTTPServer((host, port), LegacyUpdateHandler)
    print(f"{APP_NAME} listening on {host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
