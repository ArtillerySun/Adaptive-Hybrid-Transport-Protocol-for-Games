import socket
import struct
import time
import threading

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
