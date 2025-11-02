import socket
import struct
import threading
from collections import deque

from utils import *

class Sender:
    """
    Sender side for the reliable/unreliable hybrid protocol.
    """

    def __init__(self, sock, remote_addr, lock, snd_win: int = 64):
        self.sock = sock
        self.remote_addr = remote_addr
        self.lock = lock

        # --- Reliable sending state ---
        self.seq_num = 0                      # next sequence number to use (16-bit)
        self.send_buffer = {}                 # seq -> (packet_bytes, timer)
        self.SND_WIN = snd_win                # max in-flight reliable packets
        self.inflight = 0                     # current in-flight reliable packets
        self.pending_q = deque()              # queued payloads waiting for window

        # --- Unreliable channel state ---
        self.useq = 0                         # 16-bit seq for unreliable packets

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
            if self.inflight >= self.SND_WIN:
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
    # ACK handling
    # ----------------------------------------------------------------------
    def handle_ack(self, ack_num: int):
        """
        (Sender-side) An ACK came in.
        """
        with self.lock:
            entry = self.send_buffer.pop(ack_num, None)

            if entry is None:
                return  # duplicate or late ACK; ignore
            
            _packet, timer = entry
            timer.cancel()
            
            self.inflight = max(0, self.inflight - 1)
            print(f"API (Sender) Received ACK for {ack_num}")

            # Try to fill the window with queued payloads
            while self.pending_q and self.inflight < self.SND_WIN:
                payload = self.pending_q.popleft()
                self._send_one_reliable_locked(payload)

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

        # Send the packet
        try:
            self.sock.sendto(pkt, self.remote_addr)
        except Exception as e:
            print(f"API (Sender) reliable send error (seq={seq}): {e}")

        # Start per-packet retransmission timer
        t = threading.Timer(RDT_TIMEOUT_MS / 1000.0, self._retransmit_handler, args=[seq])
        self.send_buffer[seq] = (pkt, t)
        t.start()

        # Advance reliable sequence and inflight counter
        self.seq_num = seq_inc(self.seq_num)
        self.inflight += 1

    def _retransmit_handler(self, seq_num: int):
        """
        Called by a timer when an ACK wasn't received.
        """
        with self.lock:
            if seq_num in self.send_buffer:
                entry = self.send_buffer.get(seq_num)
                if entry is None:
                    return  # already ACKed
                
                # Retransmit un-ACKed packet.
                print(f"API (Sender) RETRANSMIT: Seq {seq_num} timed out. Resending.")
                packet, _old_timer = entry
                
                try:
                    self.sock.sendto(packet, self.remote_addr)
                except Exception as e:
                    print(f"API (Sender) retransmit error: {e}")
                
                # Start a new timer.
                new_timer = threading.Timer(RDT_TIMEOUT_MS / 1000, self._retransmit_handler, args=[seq_num])
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
            self.inflight = 0
