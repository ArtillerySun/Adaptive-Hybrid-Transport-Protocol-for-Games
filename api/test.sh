#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------
# Simple test.sh for H-UDP stack
# - Starts a receiver (python background)
# - Optionally applies tc netem on loopback (LOSS/DELAY/REORDER)
# - Starts a sender that sends BOTH reliable and unreliable packets
# - Cleans up tc and background receiver on exit
#
# Usage:
#   ./test.sh                # run with defaults
#   LOSS=15 USE_NETEM=1 ./test.sh
#   NREL=80 NUNREL=20 PPS=80 ./test.sh
# ---------------------------------------------------------

# --------- Configurable parameters (override via env) ----------
RPORT="${RPORT:-6000}"          # receiver bind port
SPORT="${SPORT:-6001}"          # sender local port
REMOTE_HOST="${REMOTE_HOST:-127.0.0.1}"
REMOTE_PORT="${REMOTE_PORT:-$RPORT}"  # where sender sends to (can be proxy)
USE_NETEM="${USE_NETEM:-1}"     # 1=apply tc netem on lo, 0=no
LOSS="${LOSS:-20}"               # loss % for netem (e.g., 20)
DELAY_MS="${DELAY_MS:-200}"       # delay ms for netem
JITTER_MS="${JITTER_MS:-50}"     # jitter ms for netem
REORDER="${REORDER:-20}"         # reorder % for netem
NREL="${NREL:-500}"              # number of reliable packets to send
NUNREL="${NUNREL:-200}"          # number of unreliable packets to send
PPS="${PPS:-100}"                # overall packet rate (packets per second)
DURATION="${DURATION:-30}"      # receiver runtime seconds
# ---------------------------------------------------------

TC=$(command -v tc || true)
SUDO=""
if [[ $EUID -ne 0 ]]; then
  SUDO="sudo"
fi

cleanup() {
  set +e
  echo "[CLEANUP] killing receiver (pid=${RECV_PID:-}) ..."
  [[ -n "${RECV_PID:-}" ]] && kill "$RECV_PID" 2>/dev/null || true
  wait "${RECV_PID:-}" 2>/dev/null || true

  if [[ "${USE_NETEM}" == "1" && -n "$TC" ]]; then
    echo "[CLEANUP] removing tc qdisc from lo ..."
    $SUDO $TC qdisc del dev lo root 2>/dev/null || true
  fi

  echo "[CLEANUP] done."
}
trap cleanup EXIT

# Start receiver (background)
echo "[STEP] starting receiver on 0.0.0.0:${RPORT} ..."
python3 - <<PY & 
from ReliableUDP_API import ReliableUDP_API
import time, sys
api = ReliableUDP_API(local_port=${RPORT})
print("[Receiver] up on :${RPORT}")
t0 = time.time()
try:
    while time.time() - t0 < ${DURATION}:
        item = api.receive()
        if item:
            seq, ts_ms, payload, latency = item
            chan = "R" if seq is not None else "U"
            # short print for observation
            print(f"[Receiver] {chan} seq={'-' if seq is None else seq} len={len(payload)} payload={payload[:40]!r} latency={latency}")
        time.sleep(0.005)
finally:
    api.close()
    print("[Receiver] closed")
PY
RECV_PID=$!
sleep 0.25

# Optionally apply netem on loopback
if [[ "${USE_NETEM}" == "1" ]]; then
  if [[ -z "$TC" ]]; then
    echo "[WARN] tc not found; cannot apply netem."
  else
    echo "[STEP] applying netem on lo: loss=${LOSS}% delay=${DELAY_MS}ms ±${JITTER_MS}ms reorder=${REORDER}%"
    $SUDO $TC qdisc add dev lo root netem loss ${LOSS}% delay ${DELAY_MS}ms ${JITTER_MS}ms reorder ${REORDER}% 50%
    sleep 0.15
  fi
fi

# Sender: send reliable and unreliable packets interleaved
echo "[STEP] starting sender from :${SPORT} -> ${REMOTE_HOST}:${REMOTE_PORT}"
python3 - <<PY
from ReliableUDP_API import ReliableUDP_API
import time, math, sys
api = ReliableUDP_API(local_port=${SPORT}, remote_host="${REMOTE_HOST}", remote_port=${REMOTE_PORT})
print("[Sender] up, sending reliable + unreliable")
pps = ${PPS}
interval = 1.0/pps if pps>0 else 0.0

nrel = ${NREL}
nunrel = ${NUNREL}
# We'll interleave: R, U, R, U... until both counts exhausted
i = 0
sent_rel = 0
sent_unrel = 0
while sent_rel < nrel or sent_unrel < nunrel:
    t0 = time.time()
    # try send reliable if remaining
    if sent_rel < nrel:
        api.send(f"R-{sent_rel}".encode(), reliable=True)
        sent_rel += 1
    # try send unreliable if remaining
    if sent_unrel < nunrel:
        api.send(f"U-{sent_unrel}".encode(), reliable=False)
        sent_unrel += 1

    # pacing
    if interval > 0:
        dt = time.time() - t0
        if dt < interval:
            time.sleep(interval - dt)

# allow time for last ACKs / deliveries
time.sleep(1.5)
api.close()
print("[Sender] finished: reliable={}, unreliable={}".format(sent_rel, sent_unrel))
PY

# Wait for receiver to finish naturally (or until DURATION)
wait "${RECV_PID}" || true

echo "[ALL DONE] test finished."
