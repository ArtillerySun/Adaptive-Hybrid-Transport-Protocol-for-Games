import struct
import queue
import socket

from utils import *

class Receiver:
    """
    Receiver side of the hybrid reliable/unreliable protocol.
    Handles:
      - Reliable channel: buffering, in-order delivery, selective skip (hole-jump)
      - Unreliable channel: immediate delivery
    """

    def __init__(self, sock, delivery_queue, lock):
        self.sock = sock
        self.delivery_queue = delivery_queue
        self.lock = lock

        # --- Reliable reception state ---
        self.next_expected_seq_num = 0
        self.receive_buffer = {}          # seq -> (payload_bytes, timestamp_ms)
        self.skip_deadline_ms = None      # timestamp (ms) for hole-skip timeout

        print(f"API (Receiver) listening on {self.sock.getsockname()}")

    # ----------------------------------------------------------------------
    # Core delivery helpers
    # ----------------------------------------------------------------------
    def _try_deliver_from_buffer(self):
        """
        Attempt to deliver all consecutively available packets
        starting from 'next_expected_seq_num'.
        If any were delivered, clear the skip deadline because
        the gap has been filled or bypassed.
        """
        progressed = False
        while self.next_expected_seq_num in self.receive_buffer:
            payload, ts_ms = self.receive_buffer.pop(self.next_expected_seq_num)
            self.delivery_queue.put((self.next_expected_seq_num, ts_ms, payload))
            self.next_expected_seq_num = seq_inc(self.next_expected_seq_num)
            progressed = True

        if progressed:
            self.skip_deadline_ms = None  # reset skip timer when sequence advances

    # ----------------------------------------------------------------------
    # Send ACK 
    # ----------------------------------------------------------------------
    def _send_ack(self, seq: int, sender_addr):
        try:
            ack_pkt = pack_header(ACK_CHANNEL, seq, now_ms32())
            self.sock.sendto(ack_pkt, sender_addr)
        except Exception as e:
            print(f"API (Receiver) ACK send error (seq={seq}): {e}")


    # ----------------------------------------------------------------------
    # Reliable data handler
    # ----------------------------------------------------------------------
    def handle_reliable(self, packet: bytes, sender_addr):
        """
        Process an incoming reliable data packet.
        Performs buffering, ordered delivery, and sets a skip deadline if needed.
        """
        chan, seq, ts_ms, payload = unpack_header(packet)
        
        # ignore non-reliable packets
        if chan != DATA_CHANNEL:
            return

        # --- send ACK immediately (even if duplicate) ---
        self._send_ack(seq, sender_addr)

        # ignore duplicate packets
        if seq < self.next_expected_seq_num:
            return

        # Store the packet in the reordering buffer
        self.receive_buffer[seq] = (payload, ts_ms)

        # Attempt in-order delivery of buffered data
        self._try_deliver_from_buffer()

        # If there is still a missing sequence number (gap),
        # set a skip deadline if one does not already exist.
        self.skip_deadline_ms = set_skip_deadline_if_needed(
            self.receive_buffer,
            self.next_expected_seq_num,
            self.skip_deadline_ms,
            now_ms32(),
        )

    # ----------------------------------------------------------------------
    # Unreliable data handler
    # ----------------------------------------------------------------------
    def handle_unreliable(self, packet: bytes):
        """
        Process an incoming unreliable packet.
        These are passed directly to the application layer without buffering.
        """
        chan, _, ts_ms, payload = unpack_header(packet)
        if chan != UNREL_CHANNEL:
            return
        self.delivery_queue.put((None, ts_ms, payload))

    # ----------------------------------------------------------------------
    # Idle timer handler (called on socket timeout)
    # ----------------------------------------------------------------------
    def on_idle(self, now_ms: int):
        """
        Called when the socket times out (no packets arrived for 'timeout' ms).

        If the skip deadline has expired and the next expected sequence
        number is still missing, skip it (advance 'next_expected_seq_num')
        so that later packets can be delivered immediately.
        """

        if not self.receive_buffer:
            return

        ttd = time_to_deadline_ms(now_ms, self.skip_deadline_ms)
        if ttd is not None and ttd <= 0 and self.next_expected_seq_num not in self.receive_buffer:
            missing = self.next_expected_seq_num
            print(f"API (Receiver) Skip timeout reached, skipping missing seq={missing}")

            # Advance to the next expected sequence number
            self.next_expected_seq_num = seq_inc(self.next_expected_seq_num)
            self.skip_deadline_ms = None

            # Deliver any packets that are now in order
            self._try_deliver_from_buffer()

            # If another gap remains, reset a new skip deadline
            self.skip_deadline_ms = clear_or_reset_deadline(
                self.receive_buffer, self.next_expected_seq_num, now_ms
            )
