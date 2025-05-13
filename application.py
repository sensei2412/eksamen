import socket
import struct
import argparse
import time
import os
import sys

# DRTP Constants
HEADER_FORMAT = '!HHHH'  # seq (16), ack (16), flags (16), window (16)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
DATA_CHUNK = 992  # bytes per chunk
TIMEOUT = 0.4  # 400 ms
DEFAULT_WINDOW = 15  # advertised by server

# Flags positions
FLAG_FIN = 0x1
FLAG_SYN = 0x2
FLAG_RST = 0x4
FLAG_ACK = 0x8


def pack_header(seq, ack, flags, window):
    return struct.pack(HEADER_FORMAT, seq, ack, flags, window)


def unpack_header(data):
    return struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])


def timestamp():
    micros = int(time.time() * 1e6) % 1_000_000
    return time.strftime('%H:%M:%S.') + f"{micros:06d}"


def three_way_handshake_client(sock, server_addr):
    sock.settimeout(TIMEOUT)
    syn_pkt = pack_header(0, 0, FLAG_SYN, 0)
    print("SYN packet is sent", flush=True)
    sock.sendto(syn_pkt, server_addr)

    try:
        data, _ = sock.recvfrom(HEADER_SIZE)
    except socket.timeout:
        print("Connection failed: no SYN-ACK received", flush=True)
        sys.exit(1)

    seq, ack, flags, window = unpack_header(data)
    if flags & FLAG_SYN and flags & FLAG_ACK:
        print("SYN-ACK packet is received", flush=True)
    else:
        print("Unexpected handshake response", flush=True)
        sys.exit(1)

    ack_pkt = pack_header(0, seq + 1, FLAG_ACK, 0)
    sock.sendto(ack_pkt, server_addr)
    print("ACK packet is sent", flush=True)
    print("Connection established", flush=True)
    return window


def three_way_handshake_server(sock):
    data, addr = sock.recvfrom(HEADER_SIZE)
    seq, ack, flags, window = unpack_header(data)
    if flags & FLAG_SYN:
        print("SYN packet is received", flush=True)
    synack = pack_header(0, seq + 1, FLAG_SYN | FLAG_ACK, DEFAULT_WINDOW)
    sock.sendto(synack, addr)
    print("SYN-ACK packet is sent", flush=True)

    data, _ = sock.recvfrom(HEADER_SIZE)
    _, ackn, flags2, _ = unpack_header(data)
    if flags2 & FLAG_ACK:
        print("ACK packet is received", flush=True)
    print("Connection established", flush=True)
    return addr


def teardown_client(sock, server_addr):
    fin = pack_header(0, 0, FLAG_FIN, 0)
    sock.sendto(fin, server_addr)
    print("FIN packet is sent", flush=True)
    try:
        data, _ = sock.recvfrom(HEADER_SIZE)
    except socket.timeout:
        print("No FIN-ACK received, closing anyway", flush=True)
        sock.close()
        return
    _, _, flags, _ = unpack_header(data)
    if flags & FLAG_FIN and flags & FLAG_ACK:
        print("FIN-ACK packet is received", flush=True)
    sock.close()
    print("Connection Closes", flush=True)


def teardown_server(sock, addr):
    data, _ = sock.recvfrom(HEADER_SIZE)
    _, _, flags, _ = unpack_header(data)
    if flags & FLAG_FIN:
        print(f"{timestamp()} -- FIN packet is received", flush=True)
    finack = pack_header(0, 0, FLAG_FIN | FLAG_ACK, 0)
    sock.sendto(finack, addr)
    print(f"{timestamp()} -- FIN-ACK packet is sent", flush=True)
    sock.close()
    print("Connection Closes", flush=True)


def client_mode(args):
    server_addr = (args.ip, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # 1. Handshake
    receiver_window = three_way_handshake_client(sock, server_addr)
    window_size = min(args.window, receiver_window)

    # 2. Prepare data
    base = 1
    next_seq = 1
    packets = []
    with open(args.file, 'rb') as f:
        while True:
            chunk = f.read(DATA_CHUNK)
            if not chunk:
                break
            header = pack_header(next_seq, 0, 0, window_size)
            packets.append(header + chunk)
            next_seq += 1

    total = len(packets)
    sock.settimeout(TIMEOUT)
    next_to_send = 1

    # 3. Go-Back-N
    while base <= total:
        while next_to_send < base + window_size and next_to_send <= total:
            sock.sendto(packets[next_to_send-1], server_addr)
            print(f"{timestamp()} -- packet with seq = {next_to_send} is sent, sliding window = {{{', '.join(str(i) for i in range(base, min(base+window_size, total+1)))}}} ", flush=True)
            next_to_send += 1
        try:
            data, _ = sock.recvfrom(HEADER_SIZE)
            _, ackn, flags, _ = unpack_header(data)
            if flags & FLAG_ACK:
                print(f"{timestamp()} -- ACK for packet = {ackn} is received", flush=True)
                base = ackn + 1
        except socket.timeout:
            print(f"{timestamp()} -- RTO occurred", flush=True)
            for seq in range(base, next_to_send):
                sock.sendto(packets[seq-1], server_addr)
                print(f"{timestamp()} -- retransmitting packet with seq = {seq}", flush=True)

    print("Data Finished", flush=True)
    teardown_client(sock, server_addr)


def server_mode(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.ip, args.port))
    addr = three_way_handshake_server(sock)

    filename = f"received_{int(time.time())}.bin"
    f = open(filename, 'wb')
    expected_seq = 1
    start = time.time()
    sock.settimeout(None)
    dropped = False

    while True:
        data, _ = sock.recvfrom(HEADER_SIZE + DATA_CHUNK)
        seq, ackn, flags, window = unpack_header(data)
        # simulate drop
        if args.discard is not None and seq == args.discard and not dropped:
            print(f"{timestamp()} -- Simulating drop of packet {seq}", flush=True)
            dropped = True
            continue
        if flags & FLAG_FIN:
            teardown_server(sock, addr)
            break
        if len(data) > HEADER_SIZE and seq == expected_seq:
            print(f"{timestamp()} -- packet {seq} is received", flush=True)
            f.write(data[HEADER_SIZE:])
            expected_seq += 1
            ack_pkt = pack_header(0, seq, FLAG_ACK, window)
            sock.sendto(ack_pkt, addr)
            print(f"{timestamp()} -- sending ack for the received {seq}", flush=True)
        else:
            print(f"{timestamp()} -- out-of-order packet {seq} is received", flush=True)

    f.close()
    elapsed = time.time() - start
    size_MB = os.path.getsize(filename) / 1e6
    throughput = (size_MB * 8) / elapsed
    print(f"The throughput is {throughput:.2f} Mbps", flush=True)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-s', '--server', action='store_true')
    group.add_argument('-c', '--client', action='store_true')
    parser.add_argument('-i', '--ip', default='0.0.0.0')
    parser.add_argument('-p', '--port', type=int, default=8088)
    parser.add_argument('-f', '--file', type=str, help='file to send')
    parser.add_argument('-w', '--window', type=int, default=DEFAULT_WINDOW)
    parser.add_argument('-d', '--discard', type=int, default=None, help='simulate drop of seq')
    args = parser.parse_args()

    if args.server:
        server_mode(args)
    else:
        client_mode(args)

if __name__ == '__main__':
    main()
