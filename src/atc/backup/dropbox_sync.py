"""Dropbox integration for ATC backup sync.

Uses the Dropbox API v2 (dropbox Python SDK).  Install with:
    pip install dropbox>=12.0.0

Auth flow (OAuth PKCE / offline):
  1. Call ``get_auth_url()`` — user visits the URL and grants access.
  2. User pastes the authorisation code back into ATC.
  3. Call ``authenticate(code)`` — exchanges the code for a refresh token.
  4. Store the refresh token in ``config.backup.dropbox_refresh_token``.
  5. Subsequent calls use the stored refresh token automatically.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from atc.backup.models import RemoteBackup

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_APP_KEY = "atc-backup"  # placeholder — users supply their own Dropbox app key
_REDIRECT_URI = "https://localhost"


class DropboxSync:
    """Upload / download ATC backups to Dropbox.

    Parameters
    ----------
    app_key:
        Dropbox OAuth app key (from the Dropbox developer console).
    app_secret:
        Dropbox OAuth app secret.
    refresh_token:
        Long-lived refresh token obtained via :meth:`authenticate`.
        Pass ``None`` before authentication.
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        refresh_token: str | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._refresh_token = refresh_token

    # ------------------------------------------------------------------
    # OAuth helpers
    # ------------------------------------------------------------------

    def get_auth_url(self) -> str:
        """Return the Dropbox OAuth URL for the user to visit."""
        try:
            import dropbox  # type: ignore[import-untyped]
            from dropbox import oauth  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "dropbox package not installed — run: pip install dropbox>=12.0.0"
            ) from exc

        auth_flow = oauth.DropboxOAuth2FlowNoRedirect(
            self._app_key,
            consumer_secret=self._app_secret,
            token_access_type="offline",
        )
        return str(auth_flow.start())

    def authenticate(self, auth_code: str) -> str:
        """Exchange an OAuth authorisation code for a refresh token.

        Returns the refresh token string (store in config).
        """
        try:
            import dropbox  # type: ignore[import-untyped]
            from dropbox import oauth  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "dropbox package not installed — run: pip install dropbox>=12.0.0"
            ) from exc

        auth_flow = oauth.DropboxOAuth2FlowNoRedirect(
            self._app_key,
            consumer_secret=self._app_secret,
            token_access_type="offline",
        )
        result = auth_flow.finish(auth_code.strip())
        self._refresh_token = result.refresh_token
        logger.info("Dropbox authenticated successfully")
        return str(result.refresh_token)

    # ------------------------------------------------------------------
    # Upload / download
    # ------------------------------------------------------------------

    def _client(self) -> object:
        """Return an authenticated Dropbox client."""
        if self._refresh_token is None:
            raise RuntimeError("Dropbox not authenticated — call authenticate() first")
        try:
            import dropbox  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "dropbox package not installed — run: pip install dropbox>=12.0.0"
            ) from exc
        return dropbox.Dropbox(  # type: ignore[attr-defined]
            app_key=self._app_key,
            app_secret=self._app_secret,
            oauth2_refresh_token=self._refresh_token,
        )

    def upload(self, local_path: Path, remote_path: str) -> bool:
        """Upload *local_path* to Dropbox at *remote_path*.

        Returns ``True`` on success.
        """
        try:
            import dropbox  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "dropbox package not installed — run: pip install dropbox>=12.0.0"
            ) from exc

        dbx = self._client()
        with open(local_path, "rb") as f:
            data = f.read()

        # Use upload_session for files > 150 MB, simple upload otherwise
        chunk_size = 150 * 1024 * 1024
        if len(data) <= chunk_size:
            dbx.files_upload(  # type: ignore[attr-defined]
                data,
                remote_path,
                mode=dropbox.files.WriteMode.overwrite,  # type: ignore[attr-defined]
            )
        else:
            session = dbx.files_upload_session_start(data[:chunk_size])  # type: ignore[attr-defined]
            cursor = dropbox.files.UploadSessionCursor(  # type: ignore[attr-defined]
                session_id=session.session_id, offset=chunk_size
            )
            remaining = data[chunk_size:]
            while remaining:
                chunk = remaining[:chunk_size]
                remaining = remaining[chunk_size:]
                if remaining:
                    dbx.files_upload_session_append_v2(chunk, cursor)  # type: ignore[attr-defined]
                    cursor.offset += len(chunk)
                else:
                    commit = dropbox.files.CommitInfo(path=remote_path)  # type: ignore[attr-defined]
                    dbx.files_upload_session_finish(chunk, cursor, commit)  # type: ignore[attr-defined]

        logger.info("Dropbox upload complete: %s → %s", local_path.name, remote_path)
        return True

    def list_backups(self, remote_dir: str = "/ATC Backups") -> list[RemoteBackup]:
        """List .atcb backups in *remote_dir*."""
        dbx = self._client()
        try:
            result = dbx.files_list_folder(remote_dir)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Dropbox list_folder failed: %s", exc)
            return []

        backups: list[RemoteBackup] = []
        for entry in result.entries:  # type: ignore[attr-defined]
            if hasattr(entry, "size") and entry.name.endswith(".atcb"):
                backups.append(
                    RemoteBackup(
                        remote_path=entry.path_lower,
                        name=entry.name,
                        size_bytes=int(entry.size),
                        created_at=str(
                            entry.client_modified.isoformat()
                            if hasattr(entry, "client_modified")
                            else ""
                        ),
                    )
                )
        return sorted(backups, key=lambda b: b.created_at, reverse=True)

    def download(self, remote_path: str, local_path: Path) -> bool:
        """Download *remote_path* from Dropbox to *local_path*.

        Returns ``True`` on success.
        """
        dbx = self._client()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        _metadata, response = dbx.files_download(remote_path)  # type: ignore[attr-defined]
        local_path.write_bytes(response.content)
        logger.info("Dropbox download complete: %s → %s", remote_path, local_path)
        return True
