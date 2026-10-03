#!/usr/bin/env bash
# OverHub Installer — Raspberry Pi OS / Debian / Ubuntu, 64 Bit (arm64, amd64)
#
#   curl -fsSL https://raw.githubusercontent.com/drstrangelove52/overhub/main/install.sh | sudo bash
#
# Installiert Docker und Tailscale (falls nötig), verbindet das Gerät mit
# deinem Tailnet und startet OverHub. Darf jederzeit erneut ausgeführt werden:
# bestehende Daten, Passwörter und Apps bleiben unverändert, OverHub wird auf
# die Version dieses Installers gebracht.
set -euo pipefail

OVERHUB_VERSION="${OVERHUB_VERSION:-0.5.1}"
OVERHUB_IMAGE="ghcr.io/drstrangelove52/overhub"
DATA=/opt/overhub
PORT_INTERNAL=10443
USB_LABEL=OVERHUB
# The container binds the parent dir: binding the automount point itself fails
# with "no such device" while no disk is plugged in, and OverHub would not start.
USB_PARENT=/mnt/overhub
USB_MOUNT=$USB_PARENT/backup

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die() { printf '\n\033[31mFehler: %s\033[0m\n' "$*" >&2; exit 1; }
# Under `curl … | bash` stdin is the script itself; ask on the terminal.
ask() { printf '\n%s ' "$1"; read -r _ </dev/tty || true; }
ts_json() { tailscale status --self --peers=false --json 2>/dev/null || true; }
# First "key": "value" in the JSON on stdin. Reads all input (no early-closing
# pipe like `| head`, which would SIGPIPE the writer and trip pipefail).
json_str() { sed -n "/\"$1\": *\"/{s/.*\"$1\": *\"\([^\"]*\)\".*/\1/p;q}"; }
ts_has() { local st; st="$(ts_json)"; grep -q -- "$1" <<<"$st"; }

# ---------------------------------------------------------------- Prüfungen
[ "$(id -u)" -eq 0 ] || die "Bitte mit sudo ausführen: curl -fsSL … | sudo bash"
case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) die "Nicht unterstützte Architektur $(uname -m). Nötig ist ein 64-Bit-System (Raspberry Pi: 64-Bit-Raspberry-Pi-OS)." ;;
esac
. /etc/os-release
case "${ID:-}${ID_LIKE:-}" in
  *debian*|*ubuntu*|*raspbian*) ;;
  *) die "Nur Debian, Ubuntu und Raspberry Pi OS werden unterstützt (gefunden: ${PRETTY_NAME:-unbekannt})." ;;
esac
bold "OverHub ${OVERHUB_VERSION} auf ${PRETTY_NAME} (${ARCH})"
command -v curl >/dev/null || { apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null; }

# ---------------------------------------------------------------- Docker
bold "1/5 Docker"
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  info "vorhanden: $(docker --version)"
else
  info "wird installiert (get.docker.com) …"
  curl -fsSL https://get.docker.com | sh >/tmp/overhub-docker-install.log 2>&1 \
    || die "Docker-Installation fehlgeschlagen, siehe /tmp/overhub-docker-install.log"
  info "installiert: $(docker --version)"
fi
if [ ! -f /etc/docker/daemon.json ]; then
  # Docker begrenzt Logs sonst nicht — auf einem Pi läuft die Disk voll.
  mkdir -p /etc/docker
  cat >/etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
JSON
  systemctl restart docker
  info "Logrotation eingerichtet (/etc/docker/daemon.json)"
elif ! grep -q '"max-size"' /etc/docker/daemon.json; then
  info "Hinweis: /etc/docker/daemon.json existiert ohne Logrotation (max-size) — nicht verändert."
fi
systemctl enable --now docker >/dev/null 2>&1 || true

# ---------------------------------------------------------------- Tailscale
bold "2/5 Tailscale"
if command -v tailscale >/dev/null; then
  info "vorhanden: $(tailscale version | head -n1)"
else
  info "wird installiert (tailscale.com/install.sh) …"
  curl -fsSL https://tailscale.com/install.sh | sh >/tmp/overhub-tailscale-install.log 2>&1 \
    || die "Tailscale-Installation fehlgeschlagen, siehe /tmp/overhub-tailscale-install.log"
fi
systemctl enable --now tailscaled >/dev/null 2>&1 || true
if [ "$(ts_json | json_str BackendState)" != "Running" ]; then
  info "Jetzt das Gerät mit deinem Tailnet verbinden: den folgenden Link öffnen und anmelden."
  tailscale up
fi
[ "$(ts_json | json_str BackendState)" = "Running" ] || die "Tailscale ist nicht verbunden."

until ts_has '"MagicDNSEnabled": *true'; do
  info "MagicDNS ist in deinem Tailnet ausgeschaltet."
  info "Einschalten: https://login.tailscale.com/admin/dns → MagicDNS → Enable"
  ask "Danach Enter drücken …"
done
until ts_has '"CertDomains": *\[$\|"CertDomains": *\["'; do
  info "HTTPS-Zertifikate sind in deinem Tailnet ausgeschaltet."
  info "Einschalten: https://login.tailscale.com/admin/dns → HTTPS Certificates → Enable"
  ask "Danach Enter drücken …"
done
DNS_NAME="$(ts_json | json_str DNSName)"
DNS_NAME="${DNS_NAME%.}"
info "verbunden als ${DNS_NAME}"

# ---------------------------------------------------------------- OverHub
bold "3/5 OverHub einrichten (${DATA})"
mkdir -p "$DATA/apps"
chmod 700 "$DATA"
NEW_ADMIN=""
if [ ! -f "$DATA/overhub.env" ]; then
  NEW_ADMIN="$(set +o pipefail; tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20)"
  umask 077
  cat >"$DATA/overhub.env" <<EOF
OVERHUB_ADMIN_USERNAME=admin
OVERHUB_ADMIN_PASSWORD=${NEW_ADMIN}
OVERHUB_TZ=$(cat /etc/timezone 2>/dev/null || echo Europe/Zurich)
EOF
  info "Zugangsdaten erzeugt"
else
  info "bestehende Einstellungen bleiben (overhub.env)"
fi
# Backup-Disk: jede USB-Disk mit dem Namen OVERHUB wird beim ersten Zugriff
# unter /mnt/overhub/backup eingehängt (nofail: ohne Disk startet das Gerät
# normal). OVERHUB statt längerem Namen: FAT32 erlaubt nur 11 Zeichen.
mkdir -p "$USB_MOUNT"
# device-timeout=1s: without a disk every look at the mount point waits that
# long for the device (5 s made OverHub's backup page slow).
USB_FSTAB="LABEL=${USB_LABEL} ${USB_MOUNT} auto nofail,x-systemd.automount,x-systemd.idle-timeout=600,x-systemd.device-timeout=1s 0 0"
if ! grep -qxF "$USB_FSTAB" /etc/fstab; then
  # older entries: other path (OverHub 0.2.0) or other options
  if grep -q "^LABEL=${USB_LABEL} " /etc/fstab; then
    old="$(awk -v l="LABEL=${USB_LABEL}" '$1 == l { print $2; exit }' /etc/fstab)"
    sed -i "/^LABEL=${USB_LABEL} /d" /etc/fstab
    umount "$old" 2>/dev/null || true
  fi
  printf '%s\n' "$USB_FSTAB" >>/etc/fstab
  systemctl daemon-reload
  systemctl restart local-fs.target 2>/dev/null || true
  info "Backup-Disk: USB-Disk mit dem Namen ${USB_LABEL} wird unter ${USB_MOUNT} verwendet"
fi
printf 'OVERHUB_VERSION=%s\n' "$OVERHUB_VERSION" >"$DATA/.env"
cat >"$DATA/compose.yml" <<EOF
# Von install.sh geschrieben — wird bei jedem Lauf ersetzt.
name: overhub
services:
  overhub:
    image: ${OVERHUB_IMAGE}:\${OVERHUB_VERSION}
    restart: unless-stopped
    network_mode: host
    env_file: overhub.env
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /var/run/tailscale:/var/run/tailscale
      - ${DATA}:${DATA}
      # rslave: a USB disk mounted later on the host shows up in the container
      - ${USB_PARENT}:${USB_PARENT}:rslave
EOF

bold "4/5 OverHub ${OVERHUB_VERSION} starten"
docker compose --project-directory "$DATA" -f "$DATA/compose.yml" pull --quiet
docker compose --project-directory "$DATA" -f "$DATA/compose.yml" up -d
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:${PORT_INTERNAL}/api/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "http://127.0.0.1:${PORT_INTERNAL}/api/health" >/dev/null 2>&1 \
  || die "OverHub antwortet nicht. Logs: docker compose -f $DATA/compose.yml logs"
info "läuft ($(curl -fsS "http://127.0.0.1:${PORT_INTERNAL}/api/version"))"

bold "5/5 HTTPS im Tailnet"
tailscale serve --bg --https=443 "http://127.0.0.1:${PORT_INTERNAL}" >/dev/null
info "https://${DNS_NAME} → OverHub"

# ---------------------------------------------------------------- Fertig
KEY_EXPIRY="$(ts_json | json_str KeyExpiry)"
bold "Fertig!"
cat <<EOF

  OverHub:   https://${DNS_NAME}
EOF
if [ -n "$NEW_ADMIN" ]; then
  cat <<EOF
  Benutzer:  admin
  Passwort:  ${NEW_ADMIN}

  Das Passwort wird nur jetzt angezeigt. Im Passwort-Manager speichern und
  nach dem ersten Login in OverHub ändern (Klick auf "admin" oben rechts).
EOF
fi
cat <<EOF

  Erreichbar von allen Geräten in deinem Tailnet (Tailscale-App installieren
  und mit demselben Konto anmelden).

  Wichtig:
  - Den Gerätenamen ${DNS_NAME%%.*} nicht mehr ändern: die Adressen der Apps
    hängen daran (Lesezeichen, Apps auf dem Homescreen).
EOF
if [ -n "$KEY_EXPIRY" ]; then
  cat <<EOF
  - Der Tailscale-Schlüssel dieses Geräts läuft am ${KEY_EXPIRY%%T*} ab. Damit
    OverHub danach erreichbar bleibt: https://login.tailscale.com/admin/machines
    → ${DNS_NAME%%.*} → "…" → "Disable key expiry".
EOF
fi
echo
