#!/usr/bin/env bash
set -euo pipefail

# ===== Config (can be overridden by env) =====
RPORT="${RPORT:-6000}"          # receiver bind port
SPORT="${SPORT:-6001}"          # sender bind port
LOSS="${LOSS:-10}"              # loss percentage (integer, e.g., 20 means 20%)
DELAY_MS="${DELAY_MS:-30}"      # base delay in ms
JITTER_MS="${JITTER_MS:-10}"    # jitter in ms
REORDER="${REORDER:-20}"        # reorder percentage
DURATION="${DURATION:-12}"      # receiver run seconds
NREL="${NREL:-40}"              # number of reliable packets to send
PPS="${PPS:-50}"                # packets per second (reliable)
# ============================================

TC=$(command -v tc || true)
SUDO=""
if [[ $EUID -ne 0 ]]; then
  SUDO="sudo"
fi

cleanup() {
  set +e
  echo "[CLEANUP] removing tc netem and stopping processes..."
  $SUDO $TC qdisc del dev lo root 2>/dev/null || true
  [[ -n "${RECV_PID:-}" ]] && kill "$RECV_PID" 2>/dev/null || true
  wait "$RECV_PID" 2>/dev/null || true
  echo "[CLEANUP] done."
}
trap cleanup EXIT

if [[ -z "$TC" ]]; then
  echo "[ERROR] 'tc' not found. On Debian/Ubuntu: sudo apt-get install iproute2"
  exit 1
fi

echo "[STEP] starting receiver on :$RPORT ..."
python3 - <<PY > /dev/stdout 2>&1 &
from ReliableUDP_API import ReliableUDP_API
import time
api = ReliableUDP_API(local_port=${RPORT})
print("[Receiver] up on :${RPORT}")
t0 = time.time()
while time.time() - t0 < ${DURATION}:
    m = api.receive()
    if m:
        seq, ts, data = m
        chan = "R" if seq is not None else "U"
        # keep output compact
        print(f"[Receiver] {chan} seq={seq} len={len(data)}")
    time.sleep(0.01)
api.close()
print("[Receiver] closed")
PY
RECV_PID=$!
sleep 0.3

echo "[STEP] applying netem on loopback: loss=${LOSS}%, delay=${DELAY_MS}ms ±${JITTER_MS}ms, reorder=${REORDER}%"
$SUDO $TC qdisc add dev lo root netem loss ${LOSS}% delay ${DELAY_MS}ms ${JITTER_MS}ms reorder ${REORDER}% 50%

echo "[STEP] starting sender on :$SPORT -> 127.0.0.1:$RPORT ..."
python3 - <<PY
from ReliableUDP_API import ReliableUDP_API
import time, math
api = ReliableUDP_API(local_port=${SPORT}, remote_host="127.0.0.1", remote_port=${RPORT})
print("[Sender] up on :${SPORT} -> 127.0.0.1:${RPORT}")
interval = 1.0/${PPS} if ${PPS} > 0 else 0.0
for i in range(${NREL}):
    api.send(f"Hello {i}".encode(), reliable=True)
    if interval>0:
        time.sleep(interval)
time.sleep(2.0)
api.close()
print("[Sender] closed")
PY

echo "[STEP] waiting receiver to finish (${DURATION}s total) ..."
wait "$RECV_PID" || true

echo "[OK] test complete."
