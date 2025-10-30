import socket
import struct
import threading
import queue

from sender import Sender
from receiver import Receiver

# --- Header Configuration ---
# B = Channel Type (1 byte), H = Seq/Ack Num (2 bytes), I = Timestamp (4 bytes)
HEADER_FORMAT = '!B H I'
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
        