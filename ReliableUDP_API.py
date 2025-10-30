import socket
import struct
import time
import threading
import queue


# --- Header Configuration ---
# B = Channel Type (1 byte), H = Seq/Ack Num (2 bytes), I = Timestamp (4 bytes)
HEADER_FORMAT = '!B H I'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
DATA_CHANNEL = 0
ACK_CHANNEL = 2
RDT_TIMEOUT = 0.1

class Sender:
    def __init__(self, sock, remote_addr, lock):
        self.sock = sock
        self.remote_addr = remote_addr
        self.lock = lock

        # Sender State
        self.seq_num = 0 
        self.send_buffer = {}

        print(f"API (Sender) bound to {self.sock.getsockname()}, sending to {self.remote_addr}")
    
    def send(self, data: bytes):
        """
        (Sender-side) Public method to send reliable data.
        """
        with self.lock:
            # Assign a new sequence number
            self.seq_num += 1
            seq = self.seq_num
            timestamp = int(time.time() * 1000)

            header = struct.pack(HEADER_FORMAT, DATA_CHANNEL, seq, timestamp)
            packet = header + data
            
            # Send the packet
            self.sock.sendto(packet, self.remote_addr)

            # Start a timer
            timer = threading.Timer(RDT_TIMEOUT, self._retransmit_handler, args=[seq])
            
            # Store the packet and timer
            self.send_buffer[seq] = (packet, timer)
            timer.start()
    
    def handle_ack(self, ack_num: int):
        """
        (Sender-side) An ACK came in.
        """
        with self.lock:
            if ack_num in self.send_buffer:
                _, timer = self.send_buffer.pop(ack_num)
                timer.cancel()
                print(f"  [API] Received ACK for {ack_num}")

    def _retransmit_handler(self, seq_num: int):
        """
        (Sender-side) Called by a timer when an ACK wasn't received.
        """
        with self.lock:
            if seq_num in self.send_buffer:
                # Retransmit un-ACKed packet.
                print(f"  [API] RETRANSMIT: Seq {seq_num} timed out. Resending.")
                packet, _ = self.send_buffer[seq_num]
                try:
                    self.sock.sendto(packet, self.remote_addr)
                except Exception as e:
                    print(f"[Sender] retransmit error: {e}")
                
                # Start a new timer.
                new_timer = threading.Timer(RDT_TIMEOUT, self._retransmit_handler, args=[seq_num])
                self.send_buffer[seq_num] = (packet, new_timer)
                new_timer.start()

    def cancel_all(self):
        """
        Close the sender
        """
        with self.lock:
            for _seq, (_pkt, t) in self.send_buffer.items():
                t.cancel()
            self.send_buffer.clear()

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

class ReliableUDP_API:
    """
    Implements a Selective Repeat protocol.
    """

    def __init__(self, local_port, remote_host=None, remote_port=None):
        self.local_addr = ('0.0.0.0', local_port)
        self.remote_addr = (remote_host, remote_port) if remote_host is not None and remote_port is not None else None

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        self.sock.bind(self.local_addr)

        # Delivery Queue for Application
        self.delivery_queue = queue.Queue()

        # Threading Control
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

        self._receiver = Receiver(self.sock, self.delivery_queue, self.lock)
        self._sender = Sender(self.sock, self.remote_addr, self.lock) if self.remote_addr else None

        self.io_thread = threading.Thread(target=self._io_loop, daemon=True)
        self.io_thread.start()

    def _io_loop(self):
        """
        This thread listens for all incoming packets and demultiplexes them.
        """
        while not self.stop_event.is_set():
            try:
                packet, sender_addr = self.sock.recvfrom(64 * 1024)
            except socket.timeout:
                continue
            except Exception as e:
                if not self.stop_event.is_set():
                    print(f"API Receive error: {e}")
                continue

            if len(packet) < HEADER_SIZE:
                continue

            try:
                channel_type, num, timestamp = struct.unpack(HEADER_FORMAT, packet[:HEADER_SIZE])
            except struct.error:
                continue

            payload = packet[HEADER_SIZE:]

            if channel_type == DATA_CHANNEL:
                self._receiver.handle_reliable(num, payload, timestamp, sender_addr)
            elif channel_type == ACK_CHANNEL and self._sender is not None:
                self._sender.handle_ack(num)
            else:
                pass

    def send(self, data: bytes):
        if self._sender is None or self.remote_addr is None:
            raise RuntimeError("API has no remote; cannot send from a receiver-only endpoint.")
        self._sender.send(data)
    
    def receive(self):
        try:
            return self.delivery_queue.get_nowait()
        except queue.Empty:
            return None

    def close(self):
        """Shuts down the API."""
        print("Closing API... stopping threads...")
        self.stop_event.set()
        
        if self._sender is not None:
            self._sender.cancel_all() 

        try:
            self.io_thread.join(timeout=1.0)
        except RuntimeError:
            pass
                       
        self.sock.close()
        print("API closed.")
        