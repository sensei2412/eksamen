import argparse
import re
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import Node
from mininet.log import setLogLevel, info
from mininet.link import TCLink

class LinuxRouter(Node):
    """A Node with IP forwarding enabled."""
    def config(self, **params):
        super(LinuxRouter, self).config(**params)
        self.cmd('sysctl net.ipv4.ip_forward=1')

    def terminate(self):
        self.cmd('sysctl net.ipv4.ip_forward=0')
        super(LinuxRouter, self).terminate()

class NetworkTopo(Topo):
    def build(self, **_opts):
        h1 = self.addHost('h1', ip=None)
        r = self.addNode('r', cls=LinuxRouter, ip=None)
        h2 = self.addHost('h2', ip=None)
        self.addLink(h1, r, params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24'})
        self.addLink(r, h2, params1={'ip': '10.0.1.1/24'}, params2={'ip': '10.0.1.2/24'})

def parse_throughput(output):
    m = re.search(r'The throughput is ([0-9.]+) Mbps', output)
    return float(m.group(1)) if m else None


def run_experiments(net, server_ip, server_port, filename):
    h1, h2, r = net['h1'], net['h2'], net['r']
    window_sizes = [3, 5, 10, 15, 20, 25]
    rtts = [50, 100, 200]
    losses = [0, 2, 5, 50]
    results = {}

    # Ensure static qdisc
    info('*** Base RTT tests (100ms, no loss)\n')
    r.cmd('tc qdisc del dev r-eth1 root || true')
    r.cmd('tc qdisc add dev r-eth1 root netem delay 100ms')
    for w in window_sizes:
        h2.cmd(f'python3 application.py -s -i {server_ip} -p {server_port} &')
        out = h1.cmd(f'python3 application.py -c -f {filename} -i {server_ip} -p {server_port} -w {w}')
        results[f'100ms_w{w}'] = parse_throughput(out)
        h2.cmd('pkill -f application.py')

    # RTT variation
    for rtt in rtts:
        info(f'*** RTT tests ({rtt}ms)\n')
        r.cmd('tc qdisc del dev r-eth1 root || true')
        r.cmd(f'tc qdisc add dev r-eth1 root netem delay {rtt}ms')
        for w in window_sizes:
            h2.cmd(f'python3 application.py -s -i {server_ip} -p {server_port} &')
            out = h1.cmd(f'python3 application.py -c -f {filename} -i {server_ip} -p {server_port} -w {w}')
            results[f'{rtt}ms_w{w}'] = parse_throughput(out)
            h2.cmd('pkill -f application.py')

    # Loss variation at 100ms
    info('*** Loss tests at 100ms\n')
    for loss in losses[1:]:
        r.cmd('tc qdisc del dev r-eth1 root || true')
        r.cmd(f'tc qdisc add dev r-eth1 root netem delay 100ms loss {loss}%')
        for w in window_sizes:
            h2.cmd(f'python3 application.py -s -i {server_ip} -p {server_port} &')
            out = h1.cmd(f'python3 application.py -c -f {filename} -i {server_ip} -p {server_port} -w {w}')
            results[f'loss{loss}_w{w}'] = parse_throughput(out)
            h2.cmd('pkill -f application.py')

    # Print results
    info('\n*** Results:\n')
    for k in sorted(results):
        info(f'{k}: {results[k]} Mbps\n')


def main():
    setLogLevel('info')
    topo = NetworkTopo()
    net = Mininet(topo=topo, link=TCLink)
    net.start()
    net['h1'].cmd('ip route add 10.0.1.0/24 via 10.0.0.2')
    net['h2'].cmd('ip route add 10.0.0.0/24 via 10.0.1.1')
    # disable offloads
    for host in ('h1','h2'):
        for opt in ('tso','gso','lro','gro','ufo'):
            net[host].cmd(f'ethtool -K {host}-eth0 {opt} off')
    net.pingAll()
    # run experiments (assumes test10MB.bin exists on h1)
    run_experiments(net, server_ip='10.0.1.2', server_port=8080, filename='test10MB.bin')
    net.stop()

if __name__ == '__main__':
    main()
