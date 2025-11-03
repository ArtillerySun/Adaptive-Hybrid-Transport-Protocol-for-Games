import socket
import struct
import threading
import queue

from utils import *

from sender import Sender
from receiver import Receiver

class ReliableUDP_API:
    """
    Implements a Selective Repeat protocol.
    """

    def __init__(self, local_port, remote_host=None, remote_port=None):
        self.local_addr = ('0.0.0.0', local_port)
        self.remote_addr = (remote_host, remote_port) if remote_host is not None and remote_port is not None else None

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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

    # ----------------------------------------------------------------------
    # Background I/O loop
    # ----------------------------------------------------------------------
    def _io_loop(self):
        """
        Receives all incoming packets, demultiplexes channels, and
        drives the receiver's hole-skip via adaptive timeouts.
        """
        while not self.stop_event.is_set():

            now_ms = now_ms32()
            timeout = compute_recv_timeout_sec(now_ms, self._receiver.skip_deadline_ms)

            try:
                self.sock.settimeout(timeout)
                packet, sender_addr = self.sock.recvfrom(64 * 1024)

                if len(packet) < HEADER_SIZE:
                    continue

                channel_type, seq, timestamp, payload = unpack_header(packet)

                if channel_type == DATA_CHANNEL:
                    self._receiver.handle_reliable(packet, sender_addr)
                elif channel_type == ACK_CHANNEL:
                    if self._sender is not None:
                        # --- THIS IS THE CHANGE ---
                        # Pass the whole packet for SACK processing
                        self._sender.handle_sack(packet)
                elif channel_type == UNREL_CHANNEL:
                    self._receiver.handle_unreliable(packet)
                    # No need to continue, loop will restart
                else:
                    # Unknown channel, ignore
                    pass

            except socket.timeout:
                self._receiver.on_idle(now_ms32())
            except OSError as e:
                # 11 = EWOULDBLOCK / EAGAIN (common on non-blocking, but we use timeout)
                if self.stop_event.is_set():
                    break # Exit loop if we are closing
                if e.errno == 11:
                    # nothing to read, just idle
                    self._receiver.on_idle(now_ms32())
                else:
                    print(f"API Receive error: {e}")
            except Exception as e:
                if not self.stop_event.is_set():
                    print(f"API unhandled error in _io_loop: {e}")

    # ----------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------
    def send(self, data: bytes, reliable: bool = True):
        """
        Send data to the remote peer.
        - reliable=True: goes via the reliable channel (SR with retransmission).
        - reliable=False: goes via the unreliable channel (best-effort).
        """
        if self._sender is None:
            raise RuntimeError("API has no remote; cannot send from a receiver-only endpoint.")
        if reliable:
            self._sender.send_reliable(data)
        else:
            self._sender.send_unreliable(data)
    
    def receive(self):
        """
        Non-blocking read from the delivery queue.
        Returns:
          - (seq, ts_ms, payload, rtt) for reliable channel
          - (None, ts_ms, payload, rtt) for unreliable channel
          or None if no message is available.
        """
        try:
            return self.delivery_queue.get_nowait()
        except queue.Empty:
            return None

    def close(self):
        """Shuts down the API."""
        print("Closing API... stopping threads...")
        self.stop_event.set()
        
        if self._sender is not None:
            try:
                self._sender.cancel_all()
            except Exception:
                pass

        # Nudge the socket to close to unblock recvfrom
        # This is a bit of a hack, but effective
        try:
            dummy_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            dummy_sock.sendto(b'close', self.local_addr)
            dummy_sock.close()
        except Exception:
            pass

        try:
            self.io_thread.join(timeout=1.0)
        except RuntimeError:
            pass

        self.sock.close()
        print("API closed.")

