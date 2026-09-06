"""Optionaler Upload von Transkript + Sidecar nach Google Drive.

Auth läuft über ein Service-Account (kein Browser-Login nötig): Der
freigegebene Zielordner in Drive muss mit der Service-Account-E-Mail
(``client_email`` aus der JSON-Key-Datei) als Bearbeiter geteilt sein.

Die google-api-Pakete sind eine optionale Dependency (``pip install
ytdigest[gdrive]``) und werden lazy importiert, damit der Rest der Pipeline
ohne sie lauffähig bleibt.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ytdigest.config import GDriveCfg

log = logging.getLogger(__name__)

_FOLDER_MIME = "application/vnd.google-apps.folder"
_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class GDriveUnavailable(Exception):
    """Upload nicht möglich (fehlende Dependency, Config oder Auth-Fehler)."""


def _escape(name: str) -> str:
    return name.replace("\\", "\\\\").replace("'", "\\'")


class DriveUploader:
    """Hält Credentials, Service-Client und den Unterordner-Cache über einen
    ganzen Lauf hinweg, damit nicht pro Video neu authentifiziert wird."""

    def __init__(self, cfg: GDriveCfg) -> None:
        if not cfg.folder_id:
            raise GDriveUnavailable("gdrive.folder_id ist nicht gesetzt")
        if not cfg.service_account_file.is_file():
            raise GDriveUnavailable(
                f"Service-Account-Datei nicht gefunden: {cfg.service_account_file}"
            )
        self.cfg = cfg
        self._service = self._build_service(cfg.service_account_file)
        self._folder_cache: dict[str, str] = {}

    @staticmethod
    def _build_service(key_file: Path):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise GDriveUnavailable(
                "google-api-python-client/google-auth fehlt "
                "(pip install ytdigest[gdrive])"
            ) from exc

        try:
            creds = service_account.Credentials.from_service_account_file(
                str(key_file), scopes=_SCOPES
            )
            return build("drive", "v3", credentials=creds, cache_discovery=False)
        except Exception as exc:  # noqa: BLE001
            raise GDriveUnavailable(f"Drive-Auth fehlgeschlagen: {exc}") from exc

    def _subfolder_id(self, dir_slug: str) -> str:
        if not self.cfg.mirror_subdirs:
            return self.cfg.folder_id
        if dir_slug in self._folder_cache:
            return self._folder_cache[dir_slug]

        parent = self.cfg.folder_id
        query = (
            f"'{parent}' in parents and name = '{_escape(dir_slug)}' "
            f"and mimeType = '{_FOLDER_MIME}' and trashed = false"
        )
        result = self._service.files().list(
            q=query, fields="files(id)", spaces="drive"
        ).execute()
        hits = result.get("files", [])
        if hits:
            folder_id = hits[0]["id"]
        else:
            metadata = {"name": dir_slug, "mimeType": _FOLDER_MIME, "parents": [parent]}
            folder = self._service.files().create(body=metadata, fields="id").execute()
            folder_id = folder["id"]
            log.info("Drive-Unterordner angelegt: %s (%s)", dir_slug, folder_id)

        self._folder_cache[dir_slug] = folder_id
        return folder_id

    def _upload_one(self, path: Path, parent_id: str) -> None:
        from googleapiclient.http import MediaFileUpload

        mimetype = "application/json" if path.suffix == ".json" else "text/plain"
        media = MediaFileUpload(str(path), mimetype=mimetype, resumable=True)
        metadata = {"name": path.name, "parents": [parent_id]}
        self._service.files().create(
            body=metadata, media_body=media, fields="id"
        ).execute()

    def upload(self, dir_slug: str, *paths: Path) -> None:
        """Lädt die übergebenen, bereits lokal geschriebenen Dateien hoch.

        Wirft ``GDriveUnavailable`` bei API-Fehlern; der Aufrufer entscheidet,
        ob das den Lauf stoppen soll (standardmäßig nicht - der lokale Stand
        ist bereits geschrieben und maßgeblich)."""
        try:
            parent_id = self._subfolder_id(dir_slug)
            for path in paths:
                self._upload_one(path, parent_id)
        except GDriveUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GDriveUnavailable(f"Upload fehlgeschlagen: {exc}") from exc
