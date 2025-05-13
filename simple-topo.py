```python
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import Node
from mininet.log import setLogLevel, info
from mininet.link import TCLink
import re

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
        # Hosts and router
        h1 = self.addHost('h1', ip=None)
        r  = self.addNode('r', cls=LinuxRouter, ip=None)
        h2 = self.addHost('h2', ip=None)
        # Links with IPs
        self.addLink(h1, r,
            params1={'ip': '10.0.0.1/24'},
            params2={'ip': '10.0.0.2/24'})
        self.addLink(r, h2,
            params1={'ip': '10.0.1.1/24'},
            params2={'ip': '10.0.1.2/24'})

# Automated experiments driver
def run_experiments(net, server_ip, server_port, filename):
    """
    1) Test window sizes [3,5,10,15,20,25] at base RTT (100ms).
    2) Vary RTT in [50,100,200] ms for same windows.
    3) Simulate loss rates [2,5,50]%% at 100ms.
    """
    h1, h2, r = net['h1'], net['h2'], net['r']
    window_sizes = [3, 5, 10, 15, 20, 25]
    rtts = [50, 100, 200]
    losses = [2, 5, 50]

    def parse_tp(output):
        m = re.search(r"The throughput is ([0-9.]+) Mbps", output)
        return float(m.group(1)) if m else None

    results = {}
    # 1) Base RTT tests
    info("*** Base RTT 100ms tests\n")
    r.cmd('tc qdisc change dev r-eth1 root netem delay 100ms')
    for w in window_sizes:
        info(f"- window {w} \n")
        h2.cmd(f'python3 application.py -s -i {server_ip} -p {server_port} &')
        out = h1.cmd(
            f'python3 application.py -c -f {filename} -i {server_ip} -p {server_port} -w {w}')
        tp = parse_tp(out)
        results[f"100ms_w{w}"] = tp
        h2.cmd('pkill -f application.py')

    # 2) RTT variation
    for rtt in rtts:
        info(f"*** RTT {rtt}ms tests\n")
        r.cmd(f'tc qdisc change dev r-eth1 root netem delay {rtt}ms')
        for w in window_sizes:
            h2.cmd(f'python3 application.py -s -i {server_ip} -p {server_port} &')
            out = h1.cmd(
                f'python3 application.py -c -f {filename} -i {server_ip} -p {server_port} -w {w}')
            tp = parse_tp(out)
            results[f"{rtt}ms_w{w}"] = tp
            h2.cmd('pkill -f application.py')

    # 3) Loss scenarios at 100ms
    r.cmd('tc qdisc change dev r-eth1 root netem delay 100ms')
    for loss in losses:
        info(f"*** Loss {loss}%% tests\n")
        r.cmd(f'tc qdisc change dev r-eth1 root netem delay 100ms loss {loss}%%')
        for w in window_sizes:
            h2.cmd(f'python3 application.py -s -i {server_ip} -p {server_port} &')
            out = h1.cmd(
                f'python3 application.py -c -f {filename} -i {server_ip} -p {server_port} -w {w}')
            tp = parse_tp(out)
            results[f"loss{loss}_w{w}"] = tp
            h2.cmd('pkill -f application.py')

    # Print
    info("\n*** Results:\n")
    for k, v in sorted(results.items()):
        info(f"{k}: {v} Mbps\n")

if __name__ == '__main__':
    setLogLevel('info')
    topo = NetworkTopo()
    net = Mininet(topo=topo, link=TCLink)
    net.start()
    # routing
    net['h1'].cmd('ip route add 10.0.1.0/24 via 10.0.0.2')
    net['h2'].cmd('ip route add 10.0.0.0/24 via 10.0.1.1')
    # disable offloads
    for host in ['h1', 'h2']:
        for opt in ['tso','gso','lro','gro','ufo']:
            net[host].cmd(f'ethtool -K {host}-eth0 {opt} off')
    # initial RTT
    net['r'].cmd('tc qdisc add dev r-eth1 root netem delay 100ms')
    net.pingAll()
    # run experiments
    run_experiments(net, '10.0.1.2', 8080, 'Photo.jpg')
    net.stop()
```
