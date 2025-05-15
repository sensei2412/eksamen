"""
Application.py: DRTP File Transfer using UDP + Go-Back-N Reliability.

This script implements a simple reliable data transfer protocol (DRTP) on top of UDP.
It provides in-order, reliable file transfer between a client (sender) and a server (receiver),
using a three-way handshake, Go-Back-N sliding-window reliability, and a two-way teardown.

Packet structure (8 B header + up to 992 B data = 1000 B total):
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|  Sequence Number (16)  | Acknowledgment Number (16)           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| Flags (16)              | Receiver Window Size (16)            |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      Application Data (≤ 992 B)               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

Flags (bitmask in Flags field):
  FIN = 0x1 : connection teardown
  SYN = 0x2 : connection setup
  RST = 0x4 : reset (unused)
  ACK = 0x8 : acknowledgment

Usage:
  Server: python3 application.py -s [-i IP] [-p PORT] [-d DISCARD_SEQ]
  Client: python3 application.py -c -f FILE [-i IP] [-p PORT] [-w WINDOW_SIZE]
"""

import socket
import struct
import argparse
import time
import os
import sys

# === DRTP Constants ===
HEADER_FORMAT = '!HHHH'         # 4 fields, each 16 bits: seq, ack, flags, window
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
DATA_CHUNK = 992                # payload bytes per packet (8 B header + 992 B data = 1000 B)
TIMEOUT = 0.4                   # retransmission timeout (400 ms)
DEFAULT_RECEIVER_WINDOW = 25    # advertised window in SYN-ACK (max in-flight packets)

# === DRTP Flag Bits (in header.flags) ===
FLAG_FIN = 0x1  # end of data / teardown
FLAG_SYN = 0x2  # connection initiation
FLAG_RST = 0x4  # reset (not used)
FLAG_ACK = 0x8  # acknowledgment

def pack_header(seq, ack, flags, window):
    return struct.pack(HEADER_FORMAT, seq, ack, flags, window)

def unpack_header(data):
    return struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])

def timestamp():
    micros = int(time.time() * 1e6) % 1_000_000
    return time.strftime('%H:%M:%S.') + f"{micros:06d}"

# --- Handshake ---
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

# --- Client Teardown ---
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

# --- Server Mode ---
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

# --- Client Mode ---
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

# --- Main ---
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
