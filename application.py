# Application.py: DRTP File Transfer using UDP + Go-Back-N Reliability

# Description:
# This script implements a simple reliable transport protocol on top of UDP using
# a Go-Back-N sliding window and a custom header format. It supports file transfer
# between a client and a server.

import socket       # For network communication
import struct       # For packing/unpacking binary headers
import argparse     # For command-line argument parsing
import time         # For timestamps and performance measurement
import os           # For file operations
import sys          # For program termination

# === DRTP Constants ===
HEADER_FORMAT = '!HHHH'         # Format of the header: 4 unsigned shorts (network byte order)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # Size of the header (8 bytes)
DATA_CHUNK = 992                # Max bytes per packet payload (1000 - 8 header)
TIMEOUT = 0.4                   # Timeout for retransmission (in seconds)
DEFAULT_RECEIVER_WINDOW = 25    # Default advertised receiver window size

# === DRTP Flags (used in packet header) ===
FLAG_FIN = 0x1  # FIN flag for connection termination
FLAG_SYN = 0x2  # SYN flag for connection setup
FLAG_RST = 0x4  # RST (reset) - not used
FLAG_ACK = 0x8  # ACK flag to acknowledge packets

# === Helper Functions ===

# Description:
# Packs header fields into bytes using struct.
# Arguments:
#   seq: Sequence number (int)
#   ack: Acknowledgment number (int)
#   flags: Bitmask of flags (int)
#   window: Advertised window size (int)
# Returns:
#   A packed bytes object representing the header
def pack_header(seq, ack, flags, window):
    return struct.pack(HEADER_FORMAT, seq, ack, flags, window)

# Description:
# Unpacks the header from a received packet.
# Arguments:
#   data: A bytes object containing at least HEADER_SIZE bytes
# Returns:
#   Tuple: (seq, ack, flags, window)
def unpack_header(data):
    return struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])

# Description:
# Generates a timestamp string for logging
# Returns:
#   Formatted time string with microsecond resolution
def timestamp():
    micros = int(time.time() * 1e6) % 1_000_000
    return time.strftime('%H:%M:%S.') + f"{micros:06d}"

# === Connection Setup (Three-Way Handshake) ===

# Description:
# Implements the client side of a three-way handshake.
# Arguments:
#   sock: A UDP socket
#   server_addr: Tuple (ip, port) of the server
# Returns:
#   window: The window size advertised by the server
# Handles:
#   socket.timeout if SYN-ACK is not received
def three_way_handshake_client(sock, server_addr):
    sock.settimeout(TIMEOUT)

    print("SYN packet is sent", flush=True)
    syn_pkt = pack_header(0, 0, FLAG_SYN, 0)
    sock.sendto(syn_pkt, server_addr)

    try:
        data, _ = sock.recvfrom(HEADER_SIZE)
    except socket.timeout:
        print("Connection failed: no SYN-ACK received", flush=True)
        sys.exit(1)

    seq, _, flags, window = unpack_header(data)
    if not (flags & FLAG_SYN and flags & FLAG_ACK):
        print("Unexpected handshake response", flush=True)
        sys.exit(1)

    print("SYN-ACK packet is received", flush=True)
    print("ACK packet is sent", flush=True)
    ack_pkt = pack_header(0, seq + 1, FLAG_ACK, 0)
    sock.sendto(ack_pkt, server_addr)
    print("Connection established", flush=True)

    return window

# Description:
# Implements the server side of a three-way handshake.
# Arguments:
#   sock: A bound UDP socket
# Returns:
#   addr: Address tuple of the client
# Raises:
#   RuntimeError if a non-SYN packet is received
def three_way_handshake_server(sock):
    data, addr = sock.recvfrom(HEADER_SIZE)
    seq, _, flags, _ = unpack_header(data)
    if not (flags & FLAG_SYN):
        raise RuntimeError("Expected SYN")
    print("SYN packet is received", flush=True)

    synack = pack_header(0, seq + 1, FLAG_SYN | FLAG_ACK, DEFAULT_RECEIVER_WINDOW)
    sock.sendto(synack, addr)
    print("SYN-ACK packet is sent", flush=True)

    data, _ = sock.recvfrom(HEADER_SIZE)
    _, _, flags2, _ = unpack_header(data)
    if not (flags2 & FLAG_ACK):
        raise RuntimeError("Expected ACK")
    print("ACK packet is received", flush=True)
    print("Connection established", flush=True)

    return addr

# === Connection Teardown (Client Side) ===

# Description:
# Gracefully tears down the connection from the client side.
# Arguments:
#   sock: UDP socket
#   server_addr: Address tuple of the server
# Handles:
#   socket.timeout when waiting for FIN-ACK
def teardown_client(sock, server_addr):
    sock.settimeout(TIMEOUT)
    print("Connection Teardown:", flush=True)

    print("FIN packet is sent", flush=True)
    fin = pack_header(0, 0, FLAG_FIN, 0)
    sock.sendto(fin, server_addr)

    try:
        data, _ = sock.recvfrom(HEADER_SIZE)
        _, _, flags, _ = unpack_header(data)
        if flags & (FLAG_FIN | FLAG_ACK):
            print("FIN ACK packet is received", flush=True)
    except socket.timeout:
        print("Timeout waiting for FIN-ACK", flush=True)

    print("Connection Closes", flush=True)
    sock.close()

# === Server Logic ===

# Description:
# Server function to receive a file reliably using DRTP.
# Arguments:
#   args: Parsed argparse object with options
# Behavior:
#   Performs handshake, receives packets, sends ACKs, handles optional packet drop
def server_mode(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.ip, args.port))

    print("Connection Establishment Phase:", flush=True)
    addr = three_way_handshake_server(sock)

    print("Data Transfer:", flush=True)
    fname = f"received_{int(time.time())}.dat"
    f = open(fname, 'wb')
    expected = 1
    start_time = time.time()

    while True:
        data, _ = sock.recvfrom(HEADER_SIZE + DATA_CHUNK)
        seq, _, flags, _ = unpack_header(data)

        if flags & FLAG_FIN:
            print(f"{timestamp()} -- FIN packet is received", flush=True)
            finack = pack_header(0, 0, FLAG_FIN | FLAG_ACK, 0)
            sock.sendto(finack, addr)
            print(f"{timestamp()} -- FIN ACK packet is sent", flush=True)
            break

        if args.discard and seq == args.discard:
            print(f"{timestamp()} -- DROPPED packet {seq}", flush=True)
            args.discard = None
            continue

        if seq == expected:
            f.write(data[HEADER_SIZE:])
            print(f"{timestamp()} -- packet {seq} is received", flush=True)
            print(f"{timestamp()} -- sending ack for the received {seq}", flush=True)
            ack_pkt = pack_header(0, seq, FLAG_ACK, DEFAULT_RECEIVER_WINDOW)
            sock.sendto(ack_pkt, addr)
            expected += 1
        else:
            print(f"{timestamp()} -- out-of-order packet {seq}", flush=True)

    f.close()
    elapsed = time.time() - start_time
    mb = os.path.getsize(fname) / 1e6
    throughput = (mb * 8) / elapsed
    print(f"The throughput is {throughput:.2f} Mbps", flush=True)
    print("Connection Closes", flush=True)
    sock.close()

# === Client Logic ===

# Description:
# Client function to send a file reliably using DRTP.
# Arguments:
#   args: Parsed argparse object with options
# Behavior:
#   Performs handshake, reads and sends file in chunks, implements Go-Back-N
def client_mode(args):
    server_addr = (args.ip, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print("Connection Establishment Phase:", flush=True)
    receiver_window = three_way_handshake_client(sock, server_addr)
    window_size = min(args.window, receiver_window)

    packets = []
    seq = 1
    with open(args.file, 'rb') as f_in:
        for chunk in iter(lambda: f_in.read(DATA_CHUNK), b''):
            header = pack_header(seq, 0, 0, window_size)
            packets.append(header + chunk)
            seq += 1

    total = len(packets)
    base = 1
    next_seq = 1
    sock.settimeout(TIMEOUT)

    print("Data Transfer:", flush=True)
    while base <= total:
        while next_seq < base + window_size and next_seq <= total:
            window_set = ", ".join(str(n) for n in range(base, next_seq + 1))
            print(f"{timestamp()} -- packet with seq = {next_seq} is sent, sliding window = {{{window_set}}}", flush=True)
            sock.sendto(packets[next_seq-1], server_addr)
            next_seq += 1

        try:
            data, _ = sock.recvfrom(HEADER_SIZE)
            _, ackn, flags, _ = unpack_header(data)
            if flags & FLAG_ACK:
                print(f"{timestamp()} -- ACK for packet = {ackn} is received", flush=True)
                base = ackn + 1
        except socket.timeout:
            print(f"{timestamp()} -- RTO occurred", flush=True)
            for s in range(base, next_seq):
                print(f"{timestamp()} -- retransmit packet {s}", flush=True)
                sock.sendto(packets[s-1], server_addr)

    print("DATA Finished", flush=True)
    teardown_client(sock, server_addr)

# === Main Function ===

# Description:
# Parses command-line arguments and launches server or client mode.
# Arguments:
#   None
# Returns:
#   None
def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-s', '--server', action='store_true', help='run as server')
    group.add_argument('-c', '--client', action='store_true', help='run as client')
    parser.add_argument('-i', '--ip', default='0.0.0.0', help='server IP or local bind')
    parser.add_argument('-p', '--port', type=int, default=8088, help='port number')
    parser.add_argument('-f', '--file', help='file to send (client only)')
    parser.add_argument('-w', '--window', type=int, default=3, help='sender window size')
    parser.add_argument('-d', '--discard', type=int, help='sequence to drop once (server only)')
    args = parser.parse_args()

    if args.server:
        server_mode(args)
    else:
        if not args.file:
            parser.error('Client mode requires -f/--file')
        client_mode(args)

if __name__ == '__main__':
    main()
