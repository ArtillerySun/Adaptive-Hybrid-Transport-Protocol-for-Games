import struct
import queue
import socket

HEADER_FORMAT = '!B H I'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
DATA_CHANNEL = 0
ACK_CHANNEL = 2
RDT_TIMEOUT = 0.1

class Receiver:
    def __init__(self, sock, delivery_queue, lock):
        self.sock = sock
        self.delivery_queue = delivery_queue
        self.lock = lock

        self.next_expected_seq_num = 1
        self.receive_buffer = {}
        print(f"API (Receiver) listening on {self.sock.getsockname()}")

    def _send_ack(self, ack_num, addr):
        """
        (Receiver-side) Helper to send an ACK packet.
        """
        try:
            header = struct.pack(HEADER_FORMAT, ACK_CHANNEL, ack_num, 0)
            self.sock.sendto(header, addr)
        except Exception as e:
            print(f"Error sending ACK: {e}")

    def handle_reliable(self, seq_num, data, timestamp, sender_addr):
        """
        (Receiver-side) A data packet came in.
        """
        # ACK what you receive
        self._send_ack(seq_num, sender_addr)

        if seq_num < self.next_expected_seq_num:
            return

        if seq_num in self.receive_buffer:
            return

        # Buffer the packet
        self.receive_buffer[seq_num] = (data, timestamp)
        print(f"  [API] Received {seq_num}, buffering. (Next expected: {self.next_expected_seq_num})")

        # Try to deliver from the buffer
        while self.next_expected_seq_num in self.receive_buffer:
            data_to_deliver, ts_to_deliver = self.receive_buffer.pop(self.next_expected_seq_num)
            self.delivery_queue.put((self.next_expected_seq_num, ts_to_deliver, data_to_deliver))
            self.next_expected_seq_num += 1
