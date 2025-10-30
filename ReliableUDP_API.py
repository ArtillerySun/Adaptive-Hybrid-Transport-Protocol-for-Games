import socket
import struct
import time
import threading
import queue


# --- Header Configuration ---
# B = Channel Type (1 byte), H = Seq/Ack Num (2 bytes), f = Timestamp (4 bytes)
HEADER_FORMAT = '!B H f'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
DATA_CHANNEL = 0
ACK_CHANNEL = 2
RDT_TIMEOUT = 0.1

class ReliableUDP_API:
    """
    Implements a Selective Repeat protocol.
    """

    def __init__(self, local_port, remote_host=None, remote_port=None):
        self.local_addr = ('0.0.0.0', local_port)
        if remote_host:
            self.remote_addr = (remote_host, remote_port)
        else:
            self.remote_addr = None

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        self.sock.bind(self.local_addr)

        # Sender State
        self.seq_num = 0 
        self.send_buffer = {}

        # Receiver State
        self.next_expected_seq_num = 1
        self.receive_buffer = {}

        # Delivery Queue for Application
        self.delivery_queue = queue.Queue()

        # Threading Control
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.receive_thread = threading.Thread(target=self._receiver_loop, daemon=True)
        self.receive_thread.start()

        if self.remote_addr:
            print(f"API (Sender) bound to {self.local_addr}, sending to {self.remote_addr}")
        else:
            print(f"API (Receiver) listening on {self.local_addr}")

    def _receiver_loop(self):
        """
        This thread listens for all incoming packets and demultiplexes them.
        """
        while not self.stop_event.is_set():
            try:
                packet, sender_addr = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            except Exception as e:
                if not self.stop_event.is_set():
                    print(f"Receive error: {e}")
                continue

            if len(packet) < HEADER_SIZE:
                continue

            try:
                channel_type, num, timestamp = struct.unpack(HEADER_FORMAT, packet[:HEADER_SIZE])
            except struct.error:
                continue

            # Demultiplexing
            with self.lock:
                if channel_type == DATA_CHANNEL:
                    data = packet[HEADER_SIZE:]
                    self._handle_reliable(num, data, timestamp, sender_addr)
                elif channel_type == ACK_CHANNEL:
                    self._handle_ack(num)

    def _handle_ack(self, ack_num):
        """
        (Sender-side) An ACK came in.
        """
        if ack_num in self.send_buffer:
            _packet, timer = self.send_buffer.pop(ack_num)
            timer.cancel()
            print(f"  [API] Received ACK for {ack_num}")

    def _handle_reliable(self, seq_num, data, timestamp, sender_addr):
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

    def _send_ack(self, ack_num, addr):
        """
        (Receiver-side) Helper to send an ACK packet.
        """
        try:
            header = struct.pack(HEADER_FORMAT, ACK_CHANNEL, ack_num, 0.0)
            self.sock.sendto(header, addr)
        except Exception as e:
            print(f"Error sending ACK: {e}")

    def send(self, data: bytes):
        """
        (Sender-side) Public method to send reliable data.
        """
        with self.lock:
            # Assign a new sequence number
            self.seq_num += 1
            current_seq_num = self.seq_num
            timestamp = time.time()

            header = struct.pack(HEADER_FORMAT, DATA_CHANNEL, current_seq_num, timestamp)
            packet = header + data
            
            # Send the packet
            self.sock.sendto(packet, self.remote_addr)

            # Start a timer
            timer = threading.Timer(RDT_TIMEOUT, self._retransmit_handler, args=[current_seq_num])
            
            # Store the packet and timer
            self.send_buffer[current_seq_num] = (packet, timer)
            timer.start()

    def _retransmit_handler(self, seq_num):
        """
        (Sender-side) Called by a timer when an ACK wasn't received.
        """
        with self.lock:
            if seq_num in self.send_buffer:
                # Retransmit un-ACKed packet.
                print(f"  [API] RETRANSMIT: Seq {seq_num} timed out. Resending.")
                packet, old_timer = self.send_buffer[seq_num]
                self.sock.sendto(packet, self.remote_addr)
                
                # Start a new timer.
                new_timer = threading.Timer(RDT_TIMEOUT, self._retransmit_handler, args=[seq_num])
                self.send_buffer[seq_num] = (packet, new_timer)
                new_timer.start()

    def receive(self):
        """
        (Receiver-side) Method for the app to get data.
        """
        try:
            return self.delivery_queue.get_nowait()
        except queue.Empty:
            return None

    def close(self):
        """Shuts down the API."""
        print("Closing API... stopping threads...")
        self.stop_event.set()
        self.receive_thread.join()
        
        with self.lock:
            for _seq, (_packet, timer) in self.send_buffer.items():
                timer.cancel()
                
        self.sock.close()
        print("Socket closed.")
        