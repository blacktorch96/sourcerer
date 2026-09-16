# Hinweise für Claude Code in diesem Repo

## Commit-Nachrichten

Nach abgeschlossenen Änderungen immer eine Commit-Nachricht formulieren.
Niemals selbst `git commit` ausführen - der Nutzer committet grundsätzlich
selbst, auch ohne dass er das jedes Mal dazusagt.

Format: Conventional Commits mit deutscher Beschreibung -
`<typ>(<scope>): <beschreibung>`.

- `<typ>`: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `chore`, `ci`,
  `build` - je nachdem, was überwiegt.
- `<scope>` optional, i. d. R. das betroffene Modul/Feature
  (z. B. `asr`, `vtt`, `local`, `cli`, `config`).
- `<beschreibung>`: lowercase, deutsch, kein Punkt am Ende.
  Beispiel: `feat(local): scan-befehl für lokale videos ergänzt`.
- Bei mehreren nennenswerten Änderungen darunter eine Bullet-Liste
  (`-`), die kurz beschreibt, was sich geändert hat.
- Keine `Co-Authored-By`-Zeile anhängen.
