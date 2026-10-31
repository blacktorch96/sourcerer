#!/usr/bin/env bash
# Update-Helfer für den Pi-Betrieb (siehe INSTALL.md): Web-Dienst stoppen,
# Code aktualisieren, Abhängigkeiten nachziehen, Dienst wieder starten.
# Erwartet einen systemd-Service wie in INSTALL.md beschrieben; Name bei
# Bedarf unten anpassen (z. B. für einen Profil-eigenen Dienst).
set -euo pipefail

SERVICE="ytdigest-web"
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ -n "$(git status --porcelain)" ]; then
    echo "Lokale Änderungen vorhanden - erst committen oder verwerfen, dann erneut versuchen:" >&2
    git status --short >&2
    exit 1
fi

restart_on_error() {
    echo "==> Fehler beim Update - starte $SERVICE sicherheitshalber wieder" >&2
    sudo systemctl start "$SERVICE" || true
}
trap restart_on_error ERR

echo "==> Stoppe $SERVICE"
sudo systemctl stop "$SERVICE"

echo "==> git pull"
git pull

echo "==> Abhängigkeiten aktualisieren"
uv sync --extra web

trap - ERR

echo "==> Starte $SERVICE"
sudo systemctl start "$SERVICE"

sleep 1
sudo systemctl status "$SERVICE" --no-pager -l
