#!/usr/bin/env bash
set -euo pipefail

test "${RUNNER_ENVIRONMENT:?}" = github-hosted
case "$(uname -m)" in
    x86_64)
        archive=restic_0.18.0-1+b4_amd64.deb
        url="https://deb.debian.org/debian/pool/main/r/restic/$archive"
        archive_sha=4888fb20b272a28b84ac39171b7d7f0d9a58c2aeae51ec28e54684648d66e12c
        binary_sha=7135345e471b64fe43356f8cc665d33d9e52fb042ba39e5cff77310c46d46857
        version='restic 0.18.0 compiled with go1.24.4 on linux/amd64'
        ;;
    aarch64)
        archive=restic_0.18.0_linux_arm64.bz2
        url="https://github.com/restic/restic/releases/download/v0.18.0/$archive"
        archive_sha=ce18179c25dc5f2e33e3c233ba1e580f9de1a4566d2977e8d9600210363ec209
        binary_sha=a4aa97c7d651e87eb5ade27e32ae0f457887ee16b86958845e781aa18e524d03
        version='restic 0.18.0 compiled with go1.24.1 on linux/arm64'
        ;;
    *)
        echo "Unsupported runner architecture: $(uname -m)" >&2
        exit 1
        ;;
esac
machine=$(uname -m)
echo "Runner architecture: $machine, expected: ${1:?Expected runner architecture}"
test "$machine" = "$1"

download=$(mktemp -d "${RUNNER_TEMP:?}/restic-download.XXXXXX")
trap 'rm -rf -- "$download"' EXIT
bin=$(mktemp -d "$RUNNER_TEMP/restic-bin.XXXXXX")
curl --fail --location --retry 2 --max-time 90 --proto '=https' --proto-redir '=https' \
    --output "$download/$archive" "$url"
printf '%s  %s\n' "$archive_sha" "$download/$archive" | sha256sum --check
case "$archive" in
    *.deb)
        dpkg-deb --extract "$download/$archive" "$download/package"
        cp "$download/package/usr/bin/restic" "$bin/restic"
        ;;
    *.bz2)
        bzip2 --decompress --stdout "$download/$archive" > "$bin/restic"
        ;;
esac
printf '%s  %s\n' "$binary_sha" "$bin/restic" | sha256sum --check
chmod 0755 "$bin/restic"
actual_version=$("$bin/restic" version)
printf '%s\n' "$actual_version"
test "$actual_version" = "$version"
printf '%s\n' "$bin" >> "${GITHUB_PATH:?}"

runtime="/run/user/$(id -u)"
echo "Starting the user systemd manager and checking cgroup v2 containment."
sudo -n loginctl enable-linger "$(id -un)"
sudo -n systemctl start "user@$(id -u).service"
test -S "$runtime/bus"
export XDG_RUNTIME_DIR="$runtime"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime/bus"
timeout 15s systemctl --user show-environment > /dev/null
test -f /sys/fs/cgroup/cgroup.controllers
unit="backup-manager-preflight-$$.scope"
timeout 20s systemd-run --user --scope --quiet --unit="$unit" \
    --property=KillMode=control-group --property=TimeoutStopSec=2s \
    bash -euc 'path=$(cut -d: -f3 /proc/self/cgroup); test "${path##*/}" = "$1"; grep -qx "populated 1" "/sys/fs/cgroup$path/cgroup.events"' bash "$unit"
printf 'XDG_RUNTIME_DIR=%s\nDBUS_SESSION_BUS_ADDRESS=%s\n' \
    "$XDG_RUNTIME_DIR" "$DBUS_SESSION_BUS_ADDRESS" >> "${GITHUB_ENV:?}"
echo "User scope and cgroup v2 preflight passed."
