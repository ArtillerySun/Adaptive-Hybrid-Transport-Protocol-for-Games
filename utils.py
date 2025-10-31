import struct
import time

# --- Header Configuration ---
# B = Channel Type (1 byte), H = Seq/Ack Num (2 bytes), I = Timestamp (4 bytes)
HEADER_FORMAT = '!BHI'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

DATA_CHANNEL = 0x00
UNREL_CHANNEL = 0x01
ACK_CHANNEL = 0x02

# Default timeout (in ms) 
RDT_TIMEOUT_MS = 100
SKIP_TIMEOUT_MS = 200
DEFAULT_RECV_TIMEOUT_MS = 50

# --- 16-bit sequence helpers ---
SEQ_MOD  = 1 << 16        # 65536
SEQ_MASK = SEQ_MOD - 1    # 0xFFFF
RECV_WIN = 512            # Receive window size, which can be adjust (< 32768 should be safe)

def now_ms32() -> int:
    """ms clock, within 32-bit."""
    return (time.monotonic_ns() // 1_000_000) & 0xFFFFFFFF

def seq_inc(x: int) -> int:
    """seq adds 1 (wrapped in 16-bit)."""
    return (x + 1) & SEQ_MASK

def is_seq_before(a: int, b: int) -> bool:
    """To chech whether a is before b."""
    return ((a - b) & SEQ_MASK) > 0x8000

def is_in_window(seq: int, base: int, win: int = RECV_WIN) -> bool:
    """Whether seq is within [base, base+win) window."""
    return ((seq - base) & SEQ_MASK) < win

# --- Deadline helpers (ms) ---
def make_deadline_ms(now_ms: int, after_ms: int) -> int:
    """Generate a new deadline (ms), wrapping safely."""
    return (now_ms + after_ms) & 0xFFFFFFFF

def time_to_deadline_ms(now_ms: int, deadline_ms: int | None) -> int | None:
    """Return remaining ms until deadline. None if no deadline, 0 if expired."""
    if deadline_ms is None:
        return None
    delta = (deadline_ms - now_ms) & 0xFFFFFFFF
    # If delta > 2^31, means wrap-around, so treat as expired
    if delta > 0x80000000:
        return 0
    return delta

def set_skip_deadline_if_needed(buffer: dict, next_expected: int, deadline_ms: int | None, now_ms: int) -> int | None:
    """Set skip deadline if there is a gap and none exists."""
    if next_expected not in buffer and deadline_ms is None:
        return make_deadline_ms(now_ms, SKIP_TIMEOUT_MS)
    return deadline_ms

def clear_or_reset_deadline(buffer: dict, next_expected: int, now_ms: int) -> int | None:
    """Clear or reset skip deadline after delivery/jump."""
    if next_expected not in buffer:
        return make_deadline_ms(now_ms, SKIP_TIMEOUT_MS)
    return None

# --- I/O timeout calculation (for recvfrom) ---
def compute_recv_timeout_sec(now_ms: int, deadline_ms: int | None) -> float:
    """Compute socket timeout in seconds based on next skip deadline."""
    ttd = time_to_deadline_ms(now_ms, deadline_ms)
    if ttd is None:
        return DEFAULT_RECV_TIMEOUT_MS / 1000.0
    return max(0.0, min(DEFAULT_RECV_TIMEOUT_MS, ttd) / 1000.0)

# --- Header packing/unpacking ---
def pack_header(chan: int, seq: int, ts_ms: int) -> bytes:
    return struct.pack(HEADER_FORMAT, chan & 0xFF, seq & SEQ_MASK, ts_ms & 0xFFFFFFFF)

def unpack_header(pkt: bytes):
    """Return (chan, seq, ts_ms, payload)."""
    chan, seq, ts = struct.unpack(HEADER_FORMAT, pkt[:HEADER_SIZE])
    return chan, seq, ts, pkt[HEADER_SIZE:]