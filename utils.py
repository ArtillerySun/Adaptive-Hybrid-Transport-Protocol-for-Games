import struct
import time

# --- Header Configuration ---
# B = Channel Type (1 byte), H = Seq/Ack Num (2 bytes), I = Timestamp (4 bytes)
HEADER_FORMAT = '!B H I'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
DATA_CHANNEL = 0
UNREL_CHANNEL = 1
ACK_CHANNEL = 2
RDT_TIMEOUT = 0.1

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
