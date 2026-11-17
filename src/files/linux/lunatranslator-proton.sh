#!/bin/sh
# Start LunaTranslator's text-hook bridge together with a Proton game.
#
# LunaTranslator normally starts its bridge (LunaBridge64.exe) by itself
# when you attach it to a game, also for Proton games inside the Steam Linux
# Runtime container. Use this wrapper only if that fails (the app's
# cache/lunabridge.log says the wineserver is not reachable), e.g. with
# Flatpak Steam or other sandboxes.
#
# Steam: game Properties > Launch Options:
#     "/path/to/LunaTranslator/files/linux/lunatranslator-proton.sh" %command%
# umu-run / Lutris / Heroic: put the script in front of the command, or
# export the variables below yourself.
#
# What it does:
# * PROTON_REMOTE_DEBUG_CMD makes Proton run the bridge with the game's Wine,
#   inside the game's container, next to the game (and stop it with the game);
# * PRESSURE_VESSEL_FILESYSTEMS_RO/RW make the bridge files and the app's
#   rendezvous folder ($XDG_RUNTIME_DIR/lunatranslator, where the running app
#   publishes its port and token) visible inside the container;
# * LUNA_BRIDGE_RETRY: the bridge waits this many seconds for LunaTranslator
#   to be started (default: as long as the game runs).

here=$(cd "$(dirname "$0")" && pwd)
hookdir=$(cd "$here/../LunaHook" 2>/dev/null && pwd)
if [ -z "$hookdir" ] || [ ! -f "$hookdir/LunaBridge64.exe" ]; then
    echo "lunatranslator-proton.sh: LunaBridge64.exe not found next to $here" >&2
    exec "$@"
fi

rt="${XDG_RUNTIME_DIR:-/tmp}/lunatranslator"
mkdir -p "$rt" && chmod 700 "$rt"

case "$hookdir$rt" in
    *:*)
        # PRESSURE_VESSEL_FILESYSTEMS_* is ':'-separated
        echo "lunatranslator-proton.sh: path contains ':', cannot share it with the container" >&2
        exec "$@"
        ;;
esac

# Proton splits this with shlex: single-quote the path, escaping any '
# inside it as '\'' (paths may contain spaces and quotes)
q=$(printf '%s' "$hookdir/LunaBridge64.exe" | sed "s/'/'\\\\''/g")
PROTON_REMOTE_DEBUG_CMD="'$q'"
LUNA_BRIDGE_RETRY="${LUNA_BRIDGE_RETRY:-31536000}"
PRESSURE_VESSEL_FILESYSTEMS_RO="${PRESSURE_VESSEL_FILESYSTEMS_RO:+$PRESSURE_VESSEL_FILESYSTEMS_RO:}$hookdir"
PRESSURE_VESSEL_FILESYSTEMS_RW="${PRESSURE_VESSEL_FILESYSTEMS_RW:+$PRESSURE_VESSEL_FILESYSTEMS_RW:}$rt"
export PROTON_REMOTE_DEBUG_CMD LUNA_BRIDGE_RETRY PRESSURE_VESSEL_FILESYSTEMS_RO PRESSURE_VESSEL_FILESYSTEMS_RW

exec "$@"
