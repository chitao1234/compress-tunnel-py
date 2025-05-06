#!/usr/bin/env python3
"""
Example usage of the tunnel with different compression algorithms.

To run the LZ4 example:
1. First terminal: python example.py server lz4
2. Second terminal: python example.py client lz4
3. Third terminal: curl http://localhost:1080

To run the ZSTD example:
1. First terminal: python example.py server
2. Second terminal: python example.py client
3. Third terminal: curl http://localhost:1080
"""

import sys
import argparse
from tunnel import ClientTunnel, ServerTunnel


def main():
    parser = argparse.ArgumentParser(description="Run a compression tunnel.")
    parser.add_argument("mode", choices=["client", "server"], help="Run as client or server")
    parser.add_argument("--compression", "-c", default="zstd", choices=["zstd", "lz4"], 
                        help="Compression algorithm (default: zstd)")
    parser.add_argument("--level", "-l", type=int, default=3, 
                        help="Compression level (default: 3)")
    parser.add_argument("--client-port", type=int, default=1080, 
                        help="Client listen port (default: 1080)")
    parser.add_argument("--server-port", type=int, default=19898, 
                        help="Server listen port (default: 19898)")
    parser.add_argument("--server-host", default="localhost", 
                        help="Server host for client (default: localhost)")
    parser.add_argument("--target-host", default="localhost", 
                        help="Target host for server (default: localhost)")
    parser.add_argument("--target-port", type=int, default=80, 
                        help="Target port for server (default: 80)")
    parser.add_argument("--high-compression", action="store_true",
                        help="Prioritize compression ratio over latency (default: False)")
    parser.add_argument("--buffer-size", type=int, default=1024,
                        help="Buffer size for network operations in bytes (default: 1024)")
    
    args = parser.parse_args()
    
    # Low latency is the inverse of high compression
    low_latency = not args.high_compression
    
    if args.mode == "client":
        print(f"Starting client with {args.compression} compression (level {args.level})")
        client = ClientTunnel(args.compression, args.level, low_latency, args.buffer_size)
        client.run(args.client_port, args.server_host, args.server_port, 
                  args.compression, args.level, low_latency, args.buffer_size)
    else:
        print(f"Starting server with {args.compression} compression (level {args.level})")
        server = ServerTunnel(args.compression, args.level, low_latency, args.buffer_size)
        server.run(args.server_port, args.target_host, args.target_port,
                  args.compression, args.level, low_latency, args.buffer_size)


if __name__ == "__main__":
    main() 