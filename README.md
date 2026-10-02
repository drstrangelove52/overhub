# OverHub

Over-Apps (OverCook, OverStand, …) auf einem Raspberry Pi oder einer VM betreiben: ein Installer-Befehl, danach alles im Browser. Erreichbar nur im eigenen [Tailscale](https://tailscale.com)-Netz, mit echtem HTTPS-Zertifikat pro App.

## Installation

Voraussetzungen:

- Raspberry Pi 4/5 mit **64-Bit**-Raspberry-Pi-OS, oder Debian/Ubuntu (amd64 oder arm64)
- ein kostenloser Tailscale-Account
- 8 GB RAM empfohlen, SSD statt SD-Karte

```bash
curl -fsSL https://raw.githubusercontent.com/drstrangelove52/overhub/main/install.sh | sudo bash
```

Der Installer

1. installiert Docker und Tailscale, falls sie fehlen, und begrenzt die Docker-Logs
2. verbindet das Gerät mit deinem Tailnet (Login-Link öffnen) und prüft, ob MagicDNS und HTTPS-Zertifikate eingeschaltet sind
3. startet OverHub unter `https://<gerätename>.<tailnet>.ts.net` und zeigt das Admin-Passwort **einmal** an

Erneut ausführen ist jederzeit möglich: Daten, Passwörter und Apps bleiben, OverHub wird auf die Version des Installers gebracht.

Danach in Tailscale bei diesem Gerät **„Disable key expiry“** wählen, sonst ist es nach 180 Tagen nicht mehr erreichbar.

## Bedienung

- **Katalog**: App wählen, installieren. OverHub erzeugt alle Passwörter selbst, zeigt die Zugangsdaten einmal an und richtet die App unter einem festen Port ein (z.B. OverCook `https://<gerät>…ts.net:8443`)
- **Update**, **Logs**, **Starten/Stoppen** pro App

**Backup**: jede Nacht um 03:00 auf eine USB-Disk mit dem Namen `OVERHUB` (erscheint unter `/mnt/overhub/backup`) oder einen anderen Ordner, verschlüsselt mit [restic](https://restic.net), behalten werden 7 tägliche, 4 wöchentliche und 6 monatliche Stände. Der Wiederherstellungs-Schlüssel wird im UI angezeigt und gehört in den Passwort-Manager.

**Update mit Rollback**: vor jedem Update sichert OverHub die App zusätzlich auf dem Gerät selbst (`/opt/overhub/safety`, auch ohne Backup-Ziel). Wird die neue Version nicht gesund, spielt OverHub die alte Konfiguration und die Daten zurück und startet die alte Version.

**Daten einer App** (Knopf „Daten“): **Exportieren** als eine verschlüsselte `.overhub`-Datei zum Herunterladen (ein restic-Repository mit eigener Passphrase; lässt sich notfalls auch ohne OverHub entpacken und mit `restic restore` öffnen) und **Wiederherstellen** eines gesicherten Stands. Beim **Entfernen** gibt es „Daten exportieren, dann alles löschen“, beim **Installieren** „Daten aus einem Export übernehmen“ — etwa für den Umzug auf ein neues Gerät. Daten einer älteren App-Version lassen sich in eine neuere einspielen, nicht umgekehrt.

**Gerät ersetzen**: auf einem neuen Gerät den Installer ausführen, in OverHub „Daten vom alten Gerät übernehmen“ wählen, Backup-Disk einstecken und den Wiederherstellungs-Schlüssel eingeben. OverHub übernimmt Benutzer, Backup-Ziele und alle Apps mit ihren Daten. Tipp: dem neuen Gerät in Tailscale denselben Namen geben wie dem alten, dann bleiben alle Adressen gültig.

NAS (SFTP) und Cloud als Backup-Ziel kommen in einer späteren Version.

## Aufbau

| Pfad | Inhalt |
|---|---|
| `install.sh` | Installer (Docker, Tailscale, OverHub) |
| `backend/` | FastAPI, SQLite (`/opt/overhub/overhub.db`), steuert Apps per `docker compose` und `tailscale serve` |
| `frontend/` | Vue 3 + Tailwind |
| `catalog/<app>/` | `manifest.yml` (Version, Digests, Secrets, Ports) + `compose.yml` der App |
| `Dockerfile` | ein Image mit Backend, Frontend, Katalog, docker- und tailscale-CLI |

Auf dem Gerät:

```
/opt/overhub/
  compose.yml, .env, overhub.env   ← OverHub selbst (von install.sh)
  overhub.db                        ← Zustand
  backup.key                        ← Wiederherstellungs-Schlüssel der Backups (0600)
  safety/                           ← Sicherungen vor Updates (für Rollback)
  apps/<app>/compose.yml, .env      ← je App (von OverHub, .env mit Rechten 0600)
```

OverHub läuft mit `network_mode: host` auf `127.0.0.1:10443` und hat Zugriff auf den Docker- und den Tailscale-Socket des Hosts. `tailscale serve` macht daraus `https://<gerät>…ts.net` (Port 443) und für jede App ihren festen Port.

Die Regeln für Apps im Katalog stehen im Over-App-Vertrag (Images in GHCR, Multi-Arch, ein Loopback-Port, `/api/health`, `/api/version`, Migrationen beim Start, Secrets über Env).

## Entwicklung

```bash
cd backend && pip install -r requirements-dev.txt && pytest
cd frontend && npm install && npm run dev   # Proxy auf ein Backend unter 127.0.0.1:10443
```

Release: Version in `install.sh` (`OVERHUB_VERSION`) anpassen, Tag `v1.2.3` pushen. GitHub Actions testet und baut `ghcr.io/drstrangelove52/overhub:1.2.3` für amd64 und arm64.

Neue App-Version in den Katalog: `compose.overhub.yml` aus dem App-Repo nach `catalog/<app>/compose.yml` kopieren, in `manifest.yml` `version` und die Digests aus dem Build-Lauf der App eintragen.
