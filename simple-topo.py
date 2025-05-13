import argparse
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import Node
from mininet.log import setLogLevel, info
from mininet.cli import CLI
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

        # Links with static IPs
        self.addLink(h1, r,
            params1={'ip': '10.0.0.1/24'},
            params2={'ip': '10.0.0.2/24'})
        self.addLink(r, h2,
            params1={'ip': '10.0.1.1/24'},
            params2={'ip': '10.0.1.2/24'})


def run_setup(rtt, loss):
    """
    Returns the tc qdisc command string based on rtt and loss parameters.
    """
    cmd = f"tc qdisc add dev r-eth1 root netem delay {rtt}"
    if loss > 0:
        cmd += f" loss {loss}%"
    return cmd


def run():
    # Parse command-line for rtt and loss configuration
    parser = argparse.ArgumentParser(description='Simple DRTP topo with configurable delay/loss')
    parser.add_argument('--rtt', type=int, default=100,
        help='One-way delay in ms for r-eth1 (default: 100)')
    parser.add_argument('--loss', type=float, default=0.0,
        help='Packet loss percentage to simulate on r-eth1 (default: 0)')
    args = parser.parse_args()

    topo = NetworkTopo()
    net = Mininet(topo=topo, link=TCLink)
    net.start()

    info('*** Configuring static routes\n')
    net['h1'].cmd('ip route add 10.0.1.0/24 via 10.0.0.2 dev h1-eth0')
    net['h2'].cmd('ip route add 10.0.0.0/24 via 10.0.1.1 dev h2-eth0')

    info(f"*** Applying network emulation: rtt={args.rtt}ms, loss={args.loss}%\n")
    # Remove existing qdisc (if any) and apply new
    net['r'].cmd('tc qdisc del dev r-eth1 root || true')
    qdisc_cmd = run_setup(args.rtt, args.loss)
    net['r'].cmd(qdisc_cmd)

    info('*** Disabling offloads on hosts\n')
    for host in ('h1', 'h2'):
        for opt in ('tso','gso','lro','gro','ufo'):
            net[host].cmd(f'ethtool -K {host}-eth0 {opt} off')

    info('*** Testing connectivity\n')
    net.pingAll()

    info('*** Running CLI; run your server on h2 and client on h1 with desired window sizes and scenarios.\n')
    CLI(net)
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run()
