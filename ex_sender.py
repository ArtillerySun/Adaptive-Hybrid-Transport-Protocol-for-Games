import time
import json
import random
import sys
from api.ReliableUDP_API import ReliableUDP_API

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 6000
SENDER_PORT = 6001
PACKET_RATE_PPS = 100  # Packets per second
TEST_DURATION_SEC = 30 # Duration of the test
RELIABLE_RATIO = 0.5 # 50% of packets will be reliable

def main():
    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    local_port = int(sys.argv[3]) if len(sys.argv) > 3 else SENDER_PORT
    
    print(f"Sender app starting. Sending to {host}:{port} for {TEST_DURATION_SEC}s...")
    
    api = None
    try:
        api = ReliableUDP_API(local_port=local_port,
                              remote_host=host,
                              remote_port=port)
        
        start_time = time.monotonic()
        packet_id = 0
        total_reliable_sent = 0
        total_unreliable_sent = 0
        
        while time.monotonic() - start_time < TEST_DURATION_SEC:
            packet_id += 1
            
            # 1. Create mock game data
            mock_data = {
                "id": packet_id,
                "note": "Mock game state",
                "payload": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            }
            data_bytes = json.dumps(mock_data).encode('utf-8')
            
            # 2. Tag outgoing data packets as reliable or unreliable randomly
            is_reliable = (random.random() < RELIABLE_RATIO)
            
            # 3. Sends them
            api.send(data_bytes, reliable=is_reliable)
            
            if is_reliable:
                total_reliable_sent += 1
            else:
                total_unreliable_sent += 1
                
            time.sleep(1.0 / PACKET_RATE_PPS)

        print("\nTest duration finished.")
        print("--- Sender Summary ---")
        print(f"Total Reliable Packets Sent:   {total_reliable_sent}")
        print(f"Total Unreliable Packets Sent: {total_unreliable_sent}")
        print(f"Total Packets Sent:            {total_reliable_sent + total_unreliable_sent}")

    except KeyboardInterrupt:
        print("\nSender shutting down.")
    except Exception as e:
        print(f"Sender error: {e}")
    finally:
        if api:
            api.close()
        print("Sender closed.")

if __name__ == "__main__":
    main()