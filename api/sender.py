import socket
import struct
import threading
from collections import deque

from utils import *

class Sender:
    """
    Sender side for the reliable/unreliable hybrid protocol.
    """

    def __init__(self, sock, remote_addr, lock, snd_win: int = 512):
        self.sock = sock
        self.remote_addr = remote_addr
        self.lock = lock

        # --- Reliable sending state ---
        self.seq_num = 0                      # next sequence number to use (16-bit)
        self.base_seq = 0                     # lowest unACKed sequence number (window start)
        self.send_buffer = {}                 # seq -> (packet_bytes, timer)
        self.SND_WIN = snd_win                # max in-flight reliable packets
        # inflight is now calculated as (seq_num - base_seq)
        # self.inflight = 0                     # current in-flight reliable packets
        self.pending_q = deque()              # queued payloads waiting for window

        # --- Unreliable channel state ---
        self.useq = 0                         # 16-bit seq for unreliable packets

        # --- RTO Adaptive State (Industry Standard) ---
        self.SRTT = RDT_TIMEOUT_MS                          # Smoothed RTT 
        self.RTTVAR = max(int(RDT_TIMEOUT_MS / 2), 50)      # RTT Variance Estimator
        self.RTO = min(max(2 * self.SRTT, self.SRTT + RTO_K_FACTOR * self.RTTVAR), 
                       RTO_MAX)                             # Current RTO
        self.rtx_cnt = 0
        self._last_send_ms = 0

        print(f"API (Sender) bound to {self.sock.getsockname()}, sending to {self.remote_addr}")

    # ----------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------
    def send_reliable(self, data: bytes):
        """
        Public method to send reliable data.
        If the window is full, the payload is queued and will be sent
        automatically when ACKs arrive and free up space.
        """
        with self.lock:
            # Calculate current inflight packets
            inflight = (self.seq_num - self.base_seq) & SEQ_MASK

            if inflight >= self.SND_WIN:
                # Window is full
                self.pending_q.append(data)
            else:
                self._send_one_reliable_locked(data)

    def send_unreliable(self, data: bytes):
        """
        Best-effort delivery; immediately send and bump the unreliable seq.
        """
        with self.lock:
            timestamp = now_ms32()
            packet = pack_header(UNREL_CHANNEL, self.useq, timestamp) + data
            try:
                self.sock.sendto(packet, self.remote_addr)
            except Exception as e:
                print(f"API (Sender) unreliable send error: {e}")
            self.useq = seq_inc(self.useq)

    # ----------------------------------------------------------------------
    # SACK handling
    # ----------------------------------------------------------------------
    def _mark_acked_and_cleanup(self, seq_num: int):
        """Helper to remove seq_num from buffer and cancel its timer."""
        entry = self.send_buffer.pop(seq_num, None)
        if entry is not None:
            _packet, timer = entry
            timer.cancel()
            # print(f"API (Sender) ACKed {seq_num}")
            return True
        return False

    def _update_rto(self, rtt_sample: int):
        """
        Updates SRTT and RTO using Jacobson's simplified algorithm (EWMA).
        """
        # TCP standard alpha (0.125) and beta (0.25)
        ALPHA = 0.125
        BETA = 0.25
        K = RTO_K_FACTOR
        
        # 1. Update RTTVAR (Deviation)
        rtt_deviation = abs(rtt_sample - self.SRTT)
        self.RTTVAR = int((1 - BETA) * self.RTTVAR + BETA * rtt_deviation)
        old_rto = self.RTO

        # 2. Update SRTT (Average)
        self.SRTT = int((1 - ALPHA) * self.SRTT + ALPHA * rtt_sample)

        # 3. Calculate RTO: SRTT + K * RTTVAR, applying bounds
        new_rto = self.SRTT + K * self.RTTVAR
        
        self.RTO = max(2 * self.SRTT, new_rto) 
        self.RTO = min(RTO_MAX, self.RTO) # Apply max bound
        # print(f"Current rto is {self.RTO}.")

        if abs(self.RTO - old_rto) >= max(50, int(old_rto * 0.5)):
            for seq, (packet, timer) in list(self.send_buffer.items()):
                timer.cancel()
                t = threading.Timer(self.RTO / 1000, self._retransmit_handler, args=[seq, 0])
                self.send_buffer[seq] = (packet, t)
                t.start()

    def _maybe_pace_locked(self):
        now = now_ms32()
        gap = now - self._last_send_ms
        if gap < 1:  # 1ms
            time.sleep((1 - gap) / 1000.0)
        self._last_send_ms = now

    def handle_sack(self, packet: bytes):
        """
        (Sender-side) A SACK came in.
        Processes cumulative ACK and SACK blocks.
        """
        # Unpack SACK payload
        chan, _, tm_ms, sack_payload = unpack_header(packet)
        if chan != ACK_CHANNEL:
            return
        
        rtt = calc_latency_ms(tm_ms)
        self._update_rto(rtt)

        try:
            cum_ack, sack_blocks = unpack_sack(sack_payload)
        except Exception as e:
            print(f"API (Sender) SACK unpack error: {e}")
            return

        with self.lock:
            # 1. Process Cumulative ACK
            # Move the base_seq forward up to the cum_ack
            while is_seq_less_than(self.base_seq, cum_ack):
                self._mark_acked_and_cleanup(self.base_seq)
                self.base_seq = seq_inc(self.base_seq)

            # 2. Process SACK Blocks (Selective ACKs)
            for start, end in sack_blocks:
                curr = start
                # Iterate from start up to and including end sequence
                while is_seq_in_range(curr, start, end):
                    self._mark_acked_and_cleanup(curr)
                    if curr == end:
                        break
                    curr = seq_inc(curr)

            # 3. Try to fill the window with queued payloads
            inflight = (self.seq_num - self.base_seq) & SEQ_MASK
            while self.pending_q and inflight < self.SND_WIN:
                payload = self.pending_q.popleft()
                self._send_one_reliable_locked(payload)
                inflight = (self.seq_num - self.base_seq) & SEQ_MASK


    # ----------------------------------------------------------------------
    # Internal helpers (require self.lock held)
    # ----------------------------------------------------------------------
    def _send_one_reliable_locked(self, data: bytes):
        """
        Build and send a single reliable packet under window budget.
        Precondition: self.lock is held and self.inflight < self.SND_WIN.
        """
        seq = self.seq_num
        timestamp = now_ms32()
        pkt = pack_header(DATA_CHANNEL, seq, timestamp) + data
        self._maybe_pace_locked()

        # Send the packet
        try:
            self.sock.sendto(pkt, self.remote_addr)
        except Exception as e:
            print(f"API (Sender) reliable send error (seq={seq}): {e}")

        # Start per-packet retransmission timer
        rtx_cnt = 0
        t = threading.Timer(self.RTO / 1000, self._retransmit_handler, args=[seq, rtx_cnt])
        self.send_buffer[seq] = (pkt, t)
        t.start()

        # Advance reliable sequence
        self.seq_num = seq_inc(self.seq_num)


    def _retransmit_handler(self, seq_num: int, rtx_cnt: int):
        """
        Called by a timer when a SACK wasn't received for this packet.
        """
        with self.lock:
            # Use .get() to avoid race conditions if SACK arrived just after timer fired
            entry = self.send_buffer.get(seq_num)
            if entry is None:
                return  # already ACKed by SACK

            # Retransmit un-ACKed packet.
            print(f"API (Sender) RETRANSMIT: Seq {seq_num} timed out. Resending.")
            packet, _old_timer = entry

            try:
                self.sock.sendto(packet, self.remote_addr)
            except Exception as e:
                print(f"API (Sender) retransmit error: {e}")

            # Start a new timer.
            rtx_cnt += 1
            new_timer = threading.Timer(min(self.RTO * 2**rtx_cnt, RTO_MAX) / 1000, self._retransmit_handler, args=[seq_num, rtx_cnt])
            self.send_buffer[seq_num] = (packet, new_timer)
            new_timer.start()

    # ----------------------------------------------------------------------
    # Shutdown
    # ----------------------------------------------------------------------
    def cancel_all(self):
        """
        Close the sender
        """
        with self.lock:
            for _seq, (_pkt, t) in self.send_buffer.items():
                t.cancel()
            self.send_buffer.clear()
            self.pending_q.clear()
            self.seq_num = 0
            self.base_seq = 0

