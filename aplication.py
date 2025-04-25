import socket
import struct
import argparse
import time
import os

# DRTP Constants
HEADER_FORMAT = '!HHHH'  # seq (16), ack (16), flags (16), window (16)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
DATA_CHUNK = 992  # bytes per chunk
TIMEOUT = 0.4  # 400 ms
DEFAULT_WINDOW = 3

# Flags positions
FLAG_FIN = 0x1
FLAG_SYN = 0x2
FLAG_RST = 0x4
FLAG_ACK = 0x8


def pack_header(seq, ack, flags, window):
    """
    Pack DRTP header fields into bytes.
    :param seq: Sequence number
    :param ack: Acknowledgment number
    :param flags: Flag bits
    :param window: Receiver window size
    :return: Packed header bytes
    """
    return struct.pack(HEADER_FORMAT, seq, ack, flags, window)


def unpack_header(data):
    """
    Unpack DRTP header from bytes.
    :param data: Bytes (>= HEADER_SIZE)
    :return: (seq, ack, flags, window)
    """
    return struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])


def timestamp():
    """
    Get ISO timestamp string for logging.
    """
    return time.strftime('%H:%M:%S.') + f"{int(time.time()*1e6)%1e6:06d}"


def three_way_handshake_client(sock, server_addr):
    # SYN
    sock.settimeout(TIMEOUT)
    syn_pkt = pack_header(0, 0, FLAG_SYN, 0)
    print("SYN packet is sent")
    sock.sendto(syn_pkt, server_addr)
    # SYN-ACK
    data, _ = sock.recvfrom(HEADER_SIZE)
    seq, ack, flags, window = unpack_header(data)
    if flags & FLAG_SYN and flags & FLAG_ACK:
        print("SYN-ACK packet is received")
    # ACK
    ack_pkt = pack_header(0, seq+1, FLAG_ACK, 0)
    sock.sendto(ack_pkt, server_addr)
    print("ACK packet is sent")
    print("Connection established")
    return window


def three_way_handshake_server(sock):
    # Wait SYN
    data, addr = sock.recvfrom(HEADER_SIZE)
    seq, ack, flags, window = unpack_header(data)
    if flags & FLAG_SYN:
        print("SYN packet is received")
    # Send SYN-ACK
    synack = pack_header(0, seq+1, FLAG_SYN|FLAG_ACK, DEFAULT_WINDOW)
    sock.sendto(synack, addr)
    print("SYN-ACK packet is sent")
    # Wait ACK
    data, _ = sock.recvfrom(HEADER_SIZE)
    _, ackn, flags2, _ = unpack_header(data)
    if flags2 & FLAG_ACK:
        print("ACK packet is received")
    print("Connection established")
    return addr


def teardown_client(sock, server_addr):
    # FIN
    fin = pack_header(0, 0, FLAG_FIN, 0)
    sock.sendto(fin, server_addr)
    print("FIN packet is sent")
    data, _ = sock.recvfrom(HEADER_SIZE)
    _, _, flags, _ = unpack_header(data)
    if flags & FLAG_ACK:
        print("FIN-ACK packet is received")
    print("Connection Closes")


def teardown_server(sock, addr):
    data, _ = sock.recvfrom(HEADER_SIZE)
    _, _, flags, _ = unpack_header(data)
    if flags & FLAG_FIN:
        print("FIN packet is received")
    finack = pack_header(0, 0, FLAG_ACK, 0)
    sock.sendto(finack, addr)
    print("FIN-ACK packet is sent")
    print("Connection Closes")


def client_mode(args):
    server_addr = (args.ip, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    window_size = three_way_handshake_client(sock, server_addr)
    base = 1
    next_seq = 1
    packets = []
    # Read file chunks and prepare packets
    with open(args.file, 'rb') as f:
        chunk = f.read(DATA_CHUNK)
        while chunk:
            header = pack_header(next_seq, 0, 0, window_size)
            packets.append(header + chunk)
            next_seq += 1
            chunk = f.read(DATA_CHUNK)
    total_packets = len(packets)
    # Go-Back-N send
    sock.settimeout(TIMEOUT)
    next_to_send = 1
    while base <= total_packets:
        # send up to window
        while next_to_send < base + args.window and next_to_send <= total_packets:
            sock.sendto(packets[next_to_send-1], server_addr)
            print(f"{timestamp()} -- packet with seq = {next_to_send} is sent, sliding window = {{{', '.join(str(i) for i in range(base, min(base+args.window, total_packets+1)))}}} ")
            next_to_send += 1
        try:
            data, _ = sock.recvfrom(HEADER_SIZE)
            _, ackn, flags, _ = unpack_header(data)
            if flags & FLAG_ACK:
                print(f"{timestamp()} -- ACK for packet = {ackn} is received")
                base = ackn + 1
        except socket.timeout:
            print(f"{timestamp()} -- RTO occured")
            # retransmit
            for seq in range(base, next_to_send):
                sock.sendto(packets[seq-1], server_addr)
                print(f"{timestamp()} -- retransmitting packet with seq =  {seq}")
    print("Data Finished")
    teardown_client(sock, server_addr)


def server_mode(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.ip, args.port))
    addr = three_way_handshake_server(sock)

    filename = f"received_{int(time.time())}"
    f = open(filename, 'wb')
    expected_seq = 1
    start_time = time.time()
    sock.settimeout(None)

    while True:
        data, _ = sock.recvfrom(HEADER_SIZE + DATA_CHUNK)
        seq, ackn, flags, window = unpack_header(data)

        if flags & FLAG_FIN:
            print(f"{timestamp()} -- FIN packet is received")
            finack = pack_header(0, 0, FLAG_ACK, 0)
            sock.sendto(finack, addr)
            print(f"{timestamp()} -- FIN-ACK packet is sent")
            break

        if len(data) > HEADER_SIZE:
            if seq == expected_seq:
                print(f"{timestamp()} -- packet {seq} is received")
                f.write(data[HEADER_SIZE:])
                expected_seq += 1
                ack_pkt = pack_header(0, seq, FLAG_ACK, args.window)
                sock.sendto(ack_pkt, addr)
                print(f"{timestamp()} -- sending ack for the received {seq}")
            else:
                print(f"{timestamp()} -- out-of-order packet {seq} is received and discarded")
        else:
            print(f"{timestamp()} -- empty or invalid packet received (ignored)")

    f.close()
    elapsed = time.time() - start_time
    size_MB = os.path.getsize(filename) / (1000 * 1000)
    throughput = (size_MB * 8) / elapsed
    print(f"The throughput is {throughput:.2f} Mbps")

def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-s', '--server', action='store_true')
    group.add_argument('-c', '--client', action='store_true')
    parser.add_argument('-i', '--ip', default='127.0.0.1')
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
