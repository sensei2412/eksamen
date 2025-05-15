#!/usr/bin/env python3
"""
Application.py: DRTP File Transfer using UDP + Go-Back-N Reliability.

This script implements a simple reliable transport protocol (DRTP) on top of UDP,
featuring:
  - Three-way handshake (SYN, SYN-ACK, ACK)
  - Go-Back-N sliding-window data transfer
  - Two-way teardown handshake (FIN, FIN-ACK)
  - Optional single-packet drop simulation (--discard)
  - Throughput measurement on the server side

Usage:
  Server mode:
    python3 application.py -s [-i IP] [-p PORT] [-d DISCARD_SEQ]
  Client mode:
    python3 application.py -c -f FILE [-i IP] [-p PORT] [-w WINDOW_SIZE]
"""

import socket
import struct
import argparse
import time
import os
import sys

# === DRTP Constants ===
HEADER_FORMAT = '!HHHH'         # seq (16 bits), ack (16 bits), flags (16 bits), window (16 bits)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
DATA_CHUNK = 992                # bytes per application-data chunk
TIMEOUT = 0.4                   # retransmission timeout in seconds (400 ms)
DEFAULT_RECEIVER_WINDOW = 15    # advertised window size in SYN-ACK

# === DRTP Flag Bits ===
FLAG_FIN = 0x1  # connection teardown
FLAG_SYN = 0x2  # connection setup
FLAG_RST = 0x4  # reset (unused)
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

    # 1. Send SYN
    print("SYN packet is sent", flush=True)
    syn_pkt = pack_header(0, 0, FLAG_SYN, 0)
    sock.sendto(syn_pkt, server_addr)

    # 2. Receive SYN-ACK
    try:
        data, _ = sock.recvfrom(HEADER_SIZE)
    except socket.timeout:
        print("Connection failed: no SYN-ACK received", flush=True)
        sys.exit(1)

    seq, ack, flags, window = unpack_header(data)
    if not (flags & FLAG_SYN and flags & FLAG_ACK):
        print("Unexpected handshake response", flush=True)
        sys.exit(1)
    print("SYN-ACK packet is received", flush=True)

    # 3. Send ACK
    print("ACK packet is sent", flush=True)
    ack_pkt = pack_header(0, seq + 1, FLAG_ACK, 0)
    sock.sendto(ack_pkt, server_addr)
    print("Connection established", flush=True)

    return window

def three_way_handshake_server(sock):
    # 1. Receive SYN
    data, addr = sock.recvfrom(HEADER_SIZE)
    seq, _, flags, _ = unpack_header(data)
    if not (flags & FLAG_SYN):
        raise RuntimeError("Expected SYN")
    print("SYN packet is received", flush=True)

    # 2. Send SYN-ACK
    synack = pack_header(0, seq + 1, FLAG_SYN | FLAG_ACK, DEFAULT_RECEIVER_WINDOW)
    sock.sendto(synack, addr)
    print("SYN-ACK packet is sent", flush=True)

    # 3. Receive ACK
    data, _ = sock.recvfrom(HEADER_SIZE)
    _, ackn, flags2, _ = unpack_header(data)
    if not (flags2 & FLAG_ACK):
        raise RuntimeError("Expected ACK")
    print("ACK packet is received", flush=True)
    print("Connection established", flush=True)

    return addr

# --- Teardown ---
def teardown_client(sock, server_addr):
    sock.settimeout(TIMEOUT)
    print("Connection Teardown:", flush=True)

    # Send FIN
    print("FIN packet is sent", flush=True)
    fin = pack_header(0, 0, FLAG_FIN, 0)
    sock.sendto(fin, server_addr)

    # Receive FIN-ACK
    try:
        data, _ = sock.recvfrom(HEADER_SIZE)
        _, _, flags, _ = unpack_header(data)
        if flags & (FLAG_FIN | FLAG_ACK):
            print("FIN ACK packet is received", flush=True)
    except socket.timeout:
        print("Timeout waiting for FIN-ACK", flush=True)

    print("Connection Closes", flush=True)
    sock.close()


def teardown_server(sock, addr):
    data, _ = sock.recvfrom(HEADER_SIZE)
    _, _, flags, _ = unpack_header(data)
    if flags & FLAG_FIN:
        print("FIN packet is received", flush=True)

    finack = pack_header(0, 0, FLAG_FIN | FLAG_ACK, 0)
    sock.sendto(finack, addr)
    print("FIN ACK packet is sent", flush=True)
    print("Connection Closes", flush=True)
    sock.close()

# --- Client Mode ---
def client_mode(args):
    server_addr = (args.ip, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print("Connection Establishment Phase:", flush=True)
    receiver_window = three_way_handshake_client(sock, server_addr)
    window_size = min(args.window, receiver_window)

    # Prepare packets
    packets = []
    seq = 1
    with open(args.file, 'rb') as f:
        for chunk in iter(lambda: f.read(DATA_CHUNK), b''):
            header = pack_header(seq, 0, 0, window_size)
            packets.append(header + chunk)
            seq += 1

    total = len(packets)
    base = 1
    next_seq = 1
    sock.settimeout(TIMEOUT)

    print("Data Transfer:", flush=True)
    while base <= total:
        # send new packets within window
        while next_seq < base + window_size and next_seq <= total:
            # compute sliding window set
            window_set = ", ".join(str(n) for n in range(base, next_seq + 1))
            print(f"{timestamp()} -- packet with seq = {next_seq} is sent, sliding window = {{{window_set}}}", flush=True)
            sock.sendto(packets[next_seq-1], server_addr)
            next_seq += 1

        # await ACK
        try:
            data, _ = sock.recvfrom(HEADER_SIZE)
            _, ackn, flags, _ = unpack_header(data)
            if flags & FLAG_ACK:
                print(f"{timestamp()} -- ACK for packet = {ackn} is received", flush=True)
                base = ackn + 1
        except socket.timeout:
            print(f"{timestamp()} -- RTO occurred", flush=True)
            # retransmit all unacked
            for s in range(base, next_seq):
                print(f"{timestamp()} -- packet with seq = {s} is sent, sliding window = {{{', '.join(str(n) for n in range(base, next_seq))}}}", flush=True)
                sock.sendto(packets[s-1], server_addr)

    print("DATA Finished", flush=True)
    teardown_client(sock, server_addr)

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
            teardown_server(sock, addr)
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

def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-s', '--server', action='store_true', help='run as server')
    group.add_argument('-c', '--client', action='store_true', help='run as client')
    parser.add_argument('-i', '--ip', default='0.0.0.0', help='server IP (client) or local IP to bind (server)')
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
