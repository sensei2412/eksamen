from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import Node
from mininet.log import setLogLevel, info
from mininet.link import TCLink
import re

def run_experiments(net, server_ip, server_port, filename):
    """
    Automate throughput experiments:
    1) Window sizes [3,5,10,15,20,25] at default RTT
    2) RTTs [50,100,200] for same windows
    3) Random loss scenarios [2%,5%,50%]
    """
    h1, h2, r = net['h1'], net['h2'], net['r']
    window_sizes = [3, 5, 10, 15, 20, 25]
    rtts = [50, 100, 200]
    losses = [0, 2, 5, 50]

    # Helper to parse throughput
    def parse_throughput(output):
        match = re.search(r"The throughput is ([0-9.]+) Mbps", output)
        return float(match.group(1)) if match else None

    results = {}

    # 1) Base RTT tests (100ms)
    info("*** Running base RTT tests (100ms)\n")
    r.cmd('tc qdisc change dev r-eth1 root netem delay 100ms')
    for w in window_sizes:
        # start server
        h2.cmd(f'python3 application.py -s -i {server_ip} -p {server_port} &')
        # run client
        out = h1.cmd(f'python3 application.py -c -f {filename} -i {server_ip} -p {server_port} -w {w}')
        tp = parse_throughput(out)
        results[f"rtt100_w{w}"] = tp
        h2.cmd('pkill -f application.py')

    # 2) Varying RTT
    for rtt in rtts:
        info(f"*** Running RTT tests ({rtt}ms)\n")
        r.cmd(f'tc qdisc change dev r-eth1 root netem delay {rtt}ms')
        for w in window_sizes:
            h2.cmd(f'python3 application.py -s -i {server_ip} -p {server_port} &')
            out = h1.cmd(f'python3 application.py -c -f {filename} -i {server_ip} -p {server_port} -w {w}')
            tp = parse_throughput(out)
            results[f"rtt{rtt}_w{w}"] = tp
            h2.cmd('pkill -f application.py')

    # 4) Random loss scenarios at 100ms
    r.cmd('tc qdisc change dev r-eth1 root netem delay 100ms')
    for loss in losses[1:]:  # skip 0
        info(f"*** Running loss test ({loss}%)\n")
        r.cmd(f'tc qdisc change dev r-eth1 root netem delay 100ms loss {loss}%')
        for w in window_sizes:
            h2.cmd(f'python3 application.py -s -i {server_ip} -p {server_port} &')
            out = h1.cmd(f'python3 application.py -c -f {filename} -i {server_ip} -p {server_port} -w {w}')
            tp = parse_throughput(out)
            results[f"loss{loss}_w{w}"] = tp
            h2.cmd('pkill -f application.py')

    # Print results
    info("\n*** Experiment Results:\n")
    for key, val in sorted(results.items()):
        info(f"{key}: {val} Mbps\n")


topo = Topo()  # Your NetworkTopo definition replaced here
# (Insert your NetworkTopo class building as before)
class LinuxRouter(Node):
    def config(self, **params):
        super().config(**params)
        self.cmd('sysctl net.ipv4.ip_forward=1')
    def terminate(self):
        self.cmd('sysctl net.ipv4.ip_forward=0')
        super().terminate()

class NetworkTopo(Topo):
    def build(self, **_opts):        
        h1 = self.addHost('h1', ip=None)
        r = self.addNode('r', cls=LinuxRouter, ip=None)
        h2 = self.addHost('h2', ip=None)
        self.addLink(h1, r, params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24'})
        self.addLink(r, h2, params1={'ip': '10.0.1.1/24'}, params2={'ip': '10.0.1.2/24'})

def main():
    setLogLevel('info')
    topo = NetworkTopo()
    net = Mininet(topo=topo, link=TCLink)
    net.start()
    # Configure routing
    net['h1'].cmd("ip route add 10.0.1.0/24 via 10.0.0.2")
    net['h2'].cmd("ip route add 10.0.0.0/24 via 10.0.1.1")
    # Disable offloads
    for host in ['h1', 'h2']:
        for opt in ['tso', 'gso', 'lro', 'gro', 'ufo']:
            net[host].cmd(f'ethtool -K {host}-eth0 {opt} off')
    # Initial delay
    net['r'].cmd('tc qdisc add dev r-eth1 root netem delay 100ms')
    net.pingAll()
    # Run automated experiments
    run_experiments(net, server_ip='10.0.1.2', server_port=8088, filename='test10MB.bin')
    net.stop()

if __name__ == '__main__':
    main()
