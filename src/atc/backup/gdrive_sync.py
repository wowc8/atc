"""Google Drive integration for ATC backup sync.

Uses google-auth + google-api-python-client.  Install with:
    pip install google-auth>=2.0.0 google-auth-oauthlib>=1.0.0 google-api-python-client>=2.0.0

Auth flow (OAuth 2.0 installed-app):
  1. Call ``get_auth_url()`` — user visits the URL and grants access.
  2. User pastes the authorisation code back into ATC.
  3. Call ``authenticate(code)`` — exchanges the code for credentials dict.
  4. Store the credentials dict in ``config.backup.gdrive_credentials``.
  5. Subsequent calls use the stored credentials automatically.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from atc.backup.models import RemoteBackup

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_MIME_TYPE_ATCB = "application/octet-stream"
_MIME_TYPE_FOLDER = "application/vnd.google-apps.folder"


class GoogleDriveSync:
    """Upload / download ATC backups to Google Drive.

    Parameters
    ----------
    client_id:
        Google OAuth2 client ID.
    client_secret:
        Google OAuth2 client secret.
    redirect_uri:
        Redirect URI registered in the Google Cloud console.
        Use ``urn:ietf:wg:oauth:2.0:oob`` for installed applications.
    credentials:
        Stored credentials dict from a previous :meth:`authenticate` call.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob",
        credentials: dict[str, Any] | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._credentials = credentials

    # ------------------------------------------------------------------
    # OAuth helpers
    # ------------------------------------------------------------------

    def get_auth_url(self) -> str:
        """Return the Google OAuth URL for the user to visit."""
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "google-auth-oauthlib not installed — run: "
                "pip install google-auth-oauthlib>=1.0.0"
            ) from exc

        client_config = {
            "installed": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uris": [self._redirect_uri],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, scopes=_SCOPES)
        flow.redirect_uri = self._redirect_uri
        auth_url, _state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true"
        )
        return str(auth_url)

    def authenticate(self, auth_code: str) -> dict[str, Any]:
        """Exchange an OAuth authorisation code for credentials.

        Returns a credentials dict (store in config).
        """
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "google-auth-oauthlib not installed — run: "
                "pip install google-auth-oauthlib>=1.0.0"
            ) from exc

        client_config = {
            "installed": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uris": [self._redirect_uri],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, scopes=_SCOPES)
        flow.redirect_uri = self._redirect_uri
        flow.fetch_token(code=auth_code.strip())

        creds = flow.credentials
        cred_dict: dict[str, Any] = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else _SCOPES,
        }
        self._credentials = cred_dict
        logger.info("Google Drive authenticated successfully")
        return cred_dict

    # ------------------------------------------------------------------
    # Drive helpers
    # ------------------------------------------------------------------

    def _service(self) -> Any:
        """Return an authenticated Google Drive service object."""
        if self._credentials is None:
            raise RuntimeError(
                "Google Drive not authenticated — call authenticate() first"
            )
        try:
            from google.oauth2.credentials import Credentials  # type: ignore[import-untyped]
            from googleapiclient.discovery import build  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "google-api-python-client not installed — run: "
                "pip install google-api-python-client>=2.0.0"
            ) from exc

        creds = Credentials(
            token=self._credentials.get("token"),
            refresh_token=self._credentials.get("refresh_token"),
            token_uri=self._credentials.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=self._credentials.get("client_id"),
            client_secret=self._credentials.get("client_secret"),
            scopes=self._credentials.get("scopes", _SCOPES),
        )
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    def upload(self, local_path: Path, folder_id: str) -> str:
        """Upload *local_path* to the given Google Drive folder.

        Returns the file ID of the uploaded file.
        """
        try:
            from googleapiclient.http import MediaFileUpload  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "google-api-python-client not installed — run: "
                "pip install google-api-python-client>=2.0.0"
            ) from exc

        service = self._service()
        file_metadata: dict[str, Any] = {
            "name": local_path.name,
            "parents": [folder_id],
        }
        media = MediaFileUpload(str(local_path), mimetype=_MIME_TYPE_ATCB, resumable=True)
        result = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id")
            .execute()
        )
        file_id: str = result.get("id", "")
        logger.info("Google Drive upload complete: %s → %s", local_path.name, file_id)
        return file_id

    def list_backups(self, folder_id: str) -> list[RemoteBackup]:
        """List .atcb backups in the given Google Drive folder."""
        service = self._service()
        query = (
            f"'{folder_id}' in parents and name contains '.atcb' "
            "and trashed = false"
        )
        try:
            result = (
                service.files()
                .list(
                    q=query,
                    fields="files(id, name, size, createdTime)",
                    orderBy="createdTime desc",
                )
                .execute()
            )
        except Exception as exc:
            logger.warning("Google Drive list failed: %s", exc)
            return []

        backups: list[RemoteBackup] = []
        for item in result.get("files", []):
            backups.append(
                RemoteBackup(
                    remote_path=str(item.get("id", "")),
                    name=str(item.get("name", "")),
                    size_bytes=int(item.get("size", 0)),
                    created_at=str(item.get("createdTime", "")),
                )
            )
        return backups

    def download(self, file_id: str, local_path: Path) -> bool:
        """Download file *file_id* from Google Drive to *local_path*.

        Returns ``True`` on success.
        """
        try:
            from googleapiclient.http import MediaIoBaseDownload  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "google-api-python-client not installed — run: "
                "pip install google-api-python-client>=2.0.0"
            ) from exc

        import io

        service = self._service()
        request = service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _status, done = downloader.next_chunk()

        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(buf.getvalue())
        logger.info("Google Drive download complete: %s → %s", file_id, local_path)
        return True
