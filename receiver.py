import struct
import queue
import socket

from functools import cmp_to_key

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
            rtt = calc_rtt_ms(ts_ms)
            self.delivery_queue.put((self.next_expected_seq_num, ts_ms, payload, rtt))
            self.next_expected_seq_num = seq_inc(self.next_expected_seq_num)
            progressed = True

        if progressed:
            self.skip_deadline_ms = None  # reset skip timer when sequence advances

    # ----------------------------------------------------------------------
    # SACK generation helper
    # ----------------------------------------------------------------------
    def _get_sack_blocks(self) -> list:
        """
        Analyzes the buffer to generate a list of contiguous received blocks
        that are *after* the next_expected_seq_num.
        Returns a list of (start_seq, end_seq_inclusive) tuples.
        """
        if not self.receive_buffer:
            return []

        # Create a comparison function that sorts based on distance from next_expected
        def compare_seq(a, b):
            a_dist = (a - self.next_expected_seq_num) & SEQ_MASK
            b_dist = (b - self.next_expected_seq_num) & SEQ_MASK
            return a_dist - b_dist

        # Sort keys based on their sequence order *after* the cumulative ACK point
        sorted_keys = sorted(self.receive_buffer.keys(), key=cmp_to_key(compare_seq))

        sack_blocks = []
        start_of_block = None
        current_seq_in_block = None

        for seq in sorted_keys:
            # Skip any sequences that are at or before the cumulative ACK point
            if not is_seq_less_than(self.next_expected_seq_num, seq):
                continue

            if start_of_block is None:
                start_of_block = seq
            if current_seq_in_block is None:
                current_seq_in_block = seq
            elif seq == seq_inc(current_seq_in_block):
                # This sequence is contiguous, extend the block
                current_seq_in_block = seq
            else:
                # Gap detected. Close the previous block.
                sack_blocks.append((start_of_block, current_seq_in_block))
                if len(sack_blocks) >= MAX_SACK_BLOCKS:
                    return sack_blocks # Stop if we've filled our SACK blocks

                # Start a new block
                start_of_block = seq
                current_seq_in_block = seq

        # Close the last open block, if any
        if start_of_block is not None:
            sack_blocks.append((start_of_block, current_seq_in_block))

        return sack_blocks[:MAX_SACK_BLOCKS]


    # ----------------------------------------------------------------------
    # Send SACK
    # ----------------------------------------------------------------------
    def _send_sack(self, sender_addr):
        """
        Generates and sends a SACK packet containing the cumulative ACK
        and all available SACK blocks from the buffer.
        """
        cum_ack = self.next_expected_seq_num
        sack_blocks = self._get_sack_blocks()

        try:
            sack_payload = pack_sack(cum_ack, sack_blocks) 
            # Use seq=0 in header as a dummy, channel is ACK_CHANNEL
            ack_pkt = pack_header(ACK_CHANNEL, 0, now_ms32()) + sack_payload
            self.sock.sendto(ack_pkt, sender_addr)
        except Exception as e:
            print(f"API (Receiver) SACK send error (cum_ack={cum_ack}): {e}")


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

        # --- Generate and send SACK immediately (even if duplicate) ---
        # This SACK acknowledges everything received so far.
        self._send_sack(sender_addr)

        # ignore packets that are already cumulatively ACKed (old duplicates)
        if is_seq_less_than(seq, self.next_expected_seq_num):
            return

        # Store the packet in the reordering buffer (if not already present)
        if seq not in self.receive_buffer:
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

        rtt = calc_rtt_ms(ts_ms)
        self.delivery_queue.put((None, ts_ms, payload, rtt))

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
            self.skip_deadline_ms = None # No data, clear deadline
            return

        ttd = time_to_deadline_ms(now_ms, self.skip_deadline_ms)

        if ttd is not None and ttd <= 0:
            # Timeout expired!
            if self.next_expected_seq_num not in self.receive_buffer:
                missing = self.next_expected_seq_num
                print(f"API (Receiver) Skip timeout reached, skipping missing seq={missing}")

                # Advance to the next expected sequence number
                self.next_expected_seq_num = seq_inc(self.next_expected_seq_num)
                self.skip_deadline_ms = None # We just skipped, clear deadline

                # Deliver any packets that are now in order
                self._try_deliver_from_buffer()

                # If another gap remains, reset a new skip deadline
                self.skip_deadline_ms = clear_or_reset_deadline(
                    self.receive_buffer, self.next_expected_seq_num, now_ms
                )
            else:
                # Deadline expired but the packet IS in the buffer.
                # This shouldn't happen if _try_deliver_from_buffer is correct.
                # We'll just clear the deadline.
                self.skip_deadline_ms = None
                self._try_deliver_from_buffer() # Try again

