import struct
import time

# --- Header Configuration ---
# B = Channel Type (1 byte), H = Seq/Ack Num (2 bytes), I = Timestamp (4 bytes)
HEADER_FORMAT = '!BHI'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

DATA_CHANNEL = 0x00
UNREL_CHANNEL = 0x01
ACK_CHANNEL = 0x02

# SACK Configuration
MAX_SACK_BLOCKS = 4
# 2 bytes for Cum ACK + (4 blocks * 4 bytes/block)
SACK_PAYLOAD_SIZE = 2 + (MAX_SACK_BLOCKS * 4)
# H for CumAck, followed by 4 pairs of HH (Start/End Seq)
SACK_FORMAT = f'!H{MAX_SACK_BLOCKS * "HH"}'

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

def calc_rtt_ms(recv_time: int) -> int:
    return (now_ms32() - recv_time) & 0xFFFFFFFF

def seq_inc(x: int) -> int:
    """seq adds 1 (wrapped in 16-bit)."""
    return (x + 1) & SEQ_MASK

def is_seq_before(a: int, b: int) -> bool:
    """To chech whether a is before b."""
    return ((a - b) & SEQ_MASK) > 0x8000

def is_seq_less_than(a: int, b: int) -> bool:
    """Checks if sequence number 'a' is less than 'b' (respecting wrap-around)."""
    return (a != b) and ((a - b) & SEQ_MASK) > 0x8000

def is_seq_in_range(seq: int, start: int, end_inclusive: int) -> bool:
    """Checks if seq is in [start, end_inclusive] (respecting wrap-around)."""
    if start == end_inclusive:
        return seq == start
    return not is_seq_less_than(seq, start) and not is_seq_less_than(end_inclusive, seq)


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
    # Set deadline only if buffer is not empty AND there's a gap
    if buffer and next_expected not in buffer and deadline_ms is None:
        return make_deadline_ms(now_ms, SKIP_TIMEOUT_MS)
    return deadline_ms

def clear_or_reset_deadline(buffer: dict, next_expected: int, now_ms: int) -> int | None:
    """Clear or reset skip deadline after delivery/jump."""
    # Reset deadline if a new gap is exposed
    if buffer and next_expected not in buffer:
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

# --- SACK packing/unpacking ---
def pack_sack(cum_ack: int, sack_blocks: list) -> bytes:
    """Pack Cumulative ACK and SACK blocks into payload."""
    data = [cum_ack & SEQ_MASK]

    # Add up to MAX_SACK_BLOCKS
    for start, end in sack_blocks[:MAX_SACK_BLOCKS]:
        data.extend([start & SEQ_MASK, end & SEQ_MASK])

    # Pad with zeros if fewer than MAX_SACK_BLOCKS are present
    padding_needed = MAX_SACK_BLOCKS - len(sack_blocks)
    data.extend([0, 0] * padding_needed)

    return struct.pack(SACK_FORMAT, *data)

def unpack_sack(payload: bytes):
    """Unpack SACK payload into (cum_ack, sack_blocks)."""
    if len(payload) < SACK_PAYLOAD_SIZE:
        # Handle payloads that might be smaller than expected
        payload += b'\x00' * (SACK_PAYLOAD_SIZE - len(payload))

    unpacked_data = struct.unpack(SACK_FORMAT, payload[:SACK_PAYLOAD_SIZE])

    cum_ack = unpacked_data[0]
    sack_blocks = []

    # Blocks start at index 1
    for i in range(MAX_SACK_BLOCKS):
        start_idx = 1 + (i * 2)
        start_seq = unpacked_data[start_idx]
        end_seq = unpacked_data[start_idx + 1]

        # A block of (0, 0) is treated as a null block/padding,
        # but we must accept (0, 0) if it's the first block (e.g., seq 0 was SACKed)
        if start_seq == 0 and end_seq == 0 and i > 0:
             break

        # Basic validation: start must not be after end
        if is_seq_in_range(start_seq, start_seq, end_seq):
             sack_blocks.append((start_seq, end_seq))

    return cum_ack, sack_blocks

