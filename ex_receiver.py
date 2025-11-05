import time
import json
import sys
import numpy as np
from api import ReliableUDP_API

DEFAULT_PORT = 6000
TEST_DURATION_SEC = 35 # Give 5s buffer for last packets to arrive

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    
    print(f"Receiver app starting. Listening on port {port} for {TEST_DURATION_SEC}s...")
    
    api = None
    reliable_latencies = []
    unreliable_latencies = []
    
    total_reliable_recv = 0
    total_unreliable_recv = 0
    total_bytes_recv = 0

    try:
        api = ReliableUDP_API(local_port=port)
        start_time = time.monotonic()
        
        while time.monotonic() - start_time < TEST_DURATION_SEC:
            
            item = api.receive()
            
            if item:
                # 1. A receiver application displays the data.
                seq, ts_ms, payload, latency_ms = item
                
                total_bytes_recv += len(payload)
                
                # 2. Print logs showing SeqNo, ChannelType, Timestamp
                if seq is not None:
                    # Reliable packet
                    channel_type = "RELIABLE"
                    seq_num = seq
                    total_reliable_recv += 1
                    reliable_latencies.append(latency_ms)
                else:
                    # Unreliable packet
                    channel_type = "UNRELIABLE"
                    seq_num = "N/A"
                    total_unreliable_recv += 1
                    unreliable_latencies.append(latency_ms)
                
                try:
                    data = json.loads(payload.decode('utf-8'))
                    payload_id = data.get('id', 'N/A')
                except Exception:
                    payload_id = "N/A"

                print(f"[Packet Arrival] Channel: {channel_type:<10} | SeqNo: {str(seq_num):<5} | "
                      f"PayloadID: {str(payload_id):<5} | Timestamp: {ts_ms:<10} | RTT: {latency_ms:<4} ms")
            
            else:
                time.sleep(0.001)

        print("\nTest duration finished.")

    except KeyboardInterrupt:
        print("\nReceiver shutting down.")
    except Exception as e:
        print(f"Receiver error: {e}")
    finally:
        if api:
            api.close()
        print("Receiver closed.")

    # 3. Mmeasure performance metrics: Latency/Jitter/Throughput
    print("\n--- Performance Metrics ---")
    
    # Throughput
    run_duration = time.monotonic() - start_time
    throughput_kbps = (total_bytes_recv * 8) / (run_duration) / 1000
    print(f"\nThroughput:")
    print(f"  Total Bytes Received: {total_bytes_recv}")
    print(f"  Total Test Duration:  {run_duration:.2f} s")
    print(f"  Average Throughput:   {throughput_kbps:.2f} kbps")

    # Packet Delivery
    print(f"\nPacket Delivery:")
    print(f"  Reliable Received:   {total_reliable_recv}")
    print(f"  Unreliable Received: {total_unreliable_recv}")
    print(f"  Total Received:      {total_reliable_recv + total_unreliable_recv}")
    
    # Latency & Jitter (Std Dev of Latency)
    if reliable_latencies:
        print(f"\nReliable Channel Latency/Jitter:")
        print(f"  Average: {np.mean(reliable_latencies):.2f} ms")
        print(f"  Min:     {np.min(reliable_latencies):.2f} ms")
        print(f"  Max:     {np.max(reliable_latencies):.2f} ms")
        print(f"  Jitter (StdDev): {np.std(reliable_latencies):.2f} ms")
        
    if unreliable_latencies:
        print(f"\nUnreliable Channel Latency/Jitter:")
        print(f"  Average: {np.mean(unreliable_latencies):.2f} ms")
        print(f"  Min:     {np.min(unreliable_latencies):.2f} ms")
        print(f"  Max:     {np.max(unreliable_latencies):.2f} ms")
        print(f"  Jitter (StdDev): {np.std(unreliable_latencies):.2f} ms")

if __name__ == "__main__":
    main()