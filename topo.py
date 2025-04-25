from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import Node
from mininet.log import setLogLevel, info
from mininet.cli import CLI
from mininet.link import TCLink


class LinuxRouter(Node):
    """A Node with IP forwarding enabled.
    Means that every packet that is in this node
    communicates freely with its interfaces."""

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

        self.addLink(
            h1, r,
            params1={ 'ip': '10.0.0.1/24' },
            params2={ 'ip': '10.0.0.2/24' }
        )
        self.addLink(
            r, h2,
            params1={ 'ip': '10.0.1.1/24' },
            params2={ 'ip': '10.0.1.2/24' }
        )


def run():
    topo = NetworkTopo()
    net = Mininet(topo=topo, link=TCLink)
    net.start()

    # Static routes
    net['h1'].cmd('ip route add 10.0.1.2 via 10.0.0.2 dev h1-eth0')
    net['h2'].cmd('ip route add 10.0.0.1 via 10.0.1.1 dev h2-eth0')
    net['h2'].cmd('ip route add 10.0.0.2 via 10.0.1.1 dev h2-eth0')

    # Link parameters: RTT/delay and optional loss
    net['r'].cmd('tc qdisc add dev r-eth1 root netem delay 100ms')
    # For packet loss test, uncomment:
    # net['r'].cmd('tc qdisc add dev r-eth1 root netem delay 100ms loss 2%')

    # Turn off offloading on hosts to get correct behavior
    for host in ('h1', 'h2'):
        for opt in ('tso','gso','lro','gro','ufo'):
            net[host].cmd(f'ethtool -K {host}-eth0 {opt} off')

    # Test connectivity then drop to CLI
    net.pingAll()
    CLI(net)
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run()
