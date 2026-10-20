#!/bin/bash
# Keep a Daphne memory leak from freezing the whole Green web instance.
#
# Without a cgroup limit and without swap, a t3.small that runs out of RAM
# does not OOM-kill Daphne: the kernel keeps evicting and re-reading page
# cache (EBS reads jump to ~1.5k IOPS), nginx, healthd and the CloudWatch
# agent stall too, and the instance stays unhealthy until ASG replaces it
# (2026-10-19, 13:00 and 20:40 UTC, both instances at once).
#
# A MemoryMax on web.service turns that into a fast OOM kill of Daphne only;
# Restart=always in the EB-generated unit brings it back in seconds. The
# small swap file is headroom for the agents, not for Daphne
# (MemorySwapMax=0), so Daphne never slows down by swapping.
#
# EB does not rerun postdeploy hooks after a reboot, so swap and swappiness
# are also persisted in fstab and sysctl.d; the drop-in already lives in /etc.
set -euo pipefail

# Tests run the hook against a scratch tree with stub system commands.
ROOT="${MEMORY_GUARD_ROOT:-}"
SWAPFILE=/var/swapfile
SWAP_MB=512
# Memory left to nginx, healthd, the CloudWatch/SSM agents and page cache
# (~300 MB RSS together on t3.small) once Daphne reaches its limit.
RESERVE_MB=512
DROPIN_DIR="$ROOT/etc/systemd/system/web.service.d"

mem_total_mb=$(awk '/^MemTotal:/ {print int($2 / 1024)}' "$ROOT/proc/meminfo")
web_memory_max_mb=$((mem_total_mb - RESERVE_MB))
# Never squeeze Daphne below 60% of RAM on a smaller instance.
floor_mb=$((mem_total_mb * 60 / 100))
if (( web_memory_max_mb < floor_mb )); then
    web_memory_max_mb=$floor_mb
fi
WEB_MEMORY_MAX="${web_memory_max_mb}M"

create_swapfile() {
    rm -f "$ROOT$SWAPFILE"
    dd if=/dev/zero of="$ROOT$SWAPFILE" bs=1M count="$SWAP_MB" status=none
    chmod 0600 "$ROOT$SWAPFILE"
    mkswap "$ROOT$SWAPFILE" >/dev/null
}

if ! swapon --show=NAME --noheadings | grep -qx "$SWAPFILE"; then
    # A file left by an interrupted earlier run may lack a swap signature.
    if [[ ! -f "$ROOT$SWAPFILE" ]] || ! swapon "$ROOT$SWAPFILE" 2>/dev/null; then
        create_swapfile
        swapon "$ROOT$SWAPFILE"
    fi
fi
if ! grep -qs "^$SWAPFILE " "$ROOT/etc/fstab"; then
    printf '%s none swap defaults,nofail 0 0\n' "$SWAPFILE" >>"$ROOT/etc/fstab"
fi

install -d -m 0755 "$ROOT/etc/sysctl.d"
printf 'vm.swappiness = 10\n' >"$ROOT/etc/sysctl.d/90-interoves-swappiness.conf"
sysctl -q -w vm.swappiness=10

install -d -m 0755 "$DROPIN_DIR"
cat >"$DROPIN_DIR/10-memory-guard.conf" <<UNIT
[Service]
MemoryAccounting=yes
MemoryMax=$WEB_MEMORY_MAX
MemorySwapMax=0
RestartSec=2
UNIT
chmod 0644 "$DROPIN_DIR/10-memory-guard.conf"

systemctl daemon-reload
# daemon-reload re-applies cgroup limits; set-property makes it explicit for
# the Daphne process that is already running after this deploy.
systemctl set-property --runtime web.service \
    MemoryMax="$WEB_MEMORY_MAX" MemorySwapMax=0
echo "web.service MemoryMax=$WEB_MEMORY_MAX (MemTotal=${mem_total_mb}M), swap $SWAPFILE"
