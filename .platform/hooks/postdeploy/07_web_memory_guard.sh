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
set -euo pipefail

SWAPFILE=/var/swapfile
SWAP_MB=512
WEB_MEMORY_MAX=1400M
DROPIN_DIR=/etc/systemd/system/web.service.d

if ! swapon --show=NAME --noheadings | grep -qx "$SWAPFILE"; then
    if [[ ! -f "$SWAPFILE" ]]; then
        dd if=/dev/zero of="$SWAPFILE" bs=1M count="$SWAP_MB" status=none
        chmod 0600 "$SWAPFILE"
        mkswap "$SWAPFILE" >/dev/null
    fi
    swapon "$SWAPFILE"
fi
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
