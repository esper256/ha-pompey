#!/usr/bin/env python3
"""Real packets in two isolated namespaces; never modify the host firewall/routes."""

import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(
    0,
    str(ROOT / "pompey/rootfs/usr/local/bin")
    if (ROOT / "pompey").exists()
    else "/usr/local/bin",
)
PING = ["/tests/journey/tool", "ping"] if Path("/test-tools").exists() else ["ping"]
SUDO = [] if os.geteuid() == 0 else ["sudo", "-n"]
from vpn_firewall import rules


class FirewallPackets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(
            shutil.which(x)
            for x in ["ip", "nft"]
            + ([] if Path("/test-tools").exists() else ["ping"])
            + ([] if not SUDO else ["sudo"])
        ):
            if os.environ.get("POMPEY_REQUIRE_NETWORK_TESTS") == "1":
                raise RuntimeError("Missing packet-test prerequisites")
            raise unittest.SkipTest(
                "packet tests require iproute2, nftables, ping and passwordless sudo"
            )
        if SUDO and subprocess.run(SUDO + ["true"], capture_output=True).returncode:
            if os.environ.get("POMPEY_REQUIRE_NETWORK_TESTS") == "1":
                raise RuntimeError("Missing passwordless sudo")
            raise unittest.SkipTest("packet tests require passwordless sudo")
        cls.client = "pompey-fw-c-" + str(os.getpid())
        cls.server = "pompey-fw-s-" + str(os.getpid())
        created = subprocess.run(
            [*SUDO, "ip", "netns", "add", cls.client], capture_output=True, text=True
        )
        if created.returncode:
            if os.environ.get("POMPEY_REQUIRE_NETWORK_TESTS") == "1":
                raise RuntimeError(created.stderr)
            raise unittest.SkipTest(
                "Host cannot create network namespaces: " + created.stderr.strip()
            )
        cls.addClassCleanup(
            lambda: subprocess.run(
                [*SUDO, "ip", "netns", "del", cls.client], capture_output=True
            )
        )
        subprocess.run([*SUDO, "ip", "netns", "add", cls.server], check=True)
        cls.addClassCleanup(
            lambda: subprocess.run(
                [*SUDO, "ip", "netns", "del", cls.server], capture_output=True
            )
        )
        cls.command(
            cls.client,
            "ip",
            "link",
            "add",
            "eth0",
            "type",
            "veth",
            "peer",
            "name",
            "other",
        )
        cls.command(cls.client, "ip", "link", "set", "other", "netns", cls.server)
        for ns, dev, addr in [
            (cls.client, "eth0", "198.18.0.1/24"),
            (cls.server, "other", "198.18.0.2/24"),
        ]:
            cls.command(ns, "ip", "addr", "add", addr, "dev", dev)
            cls.command(ns, "ip", "link", "set", dev, "up")
            cls.command(ns, "ip", "link", "set", "lo", "up")
        cls.command(
            cls.client,
            "ip",
            "-6",
            "addr",
            "add",
            "2001:db8:1::1/64",
            "dev",
            "eth0",
            "nodad",
        )
        cls.command(
            cls.server,
            "ip",
            "-6",
            "addr",
            "add",
            "2001:db8:1::2/64",
            "dev",
            "other",
            "nodad",
        )
        cls.config = (ROOT / "tests/fixtures/wg0.conf").read_text()
        cls.firewall = rules(cls.config, "")

    @staticmethod
    def command(ns, *cmd, **kw):
        return subprocess.run(
            [*SUDO, sys.executable, str(ROOT / "tests/netns_exec.py"), ns, *cmd],
            check=True,
            capture_output=True,
            text=True,
            **kw,
        )

    def install(self):
        self.command(self.client, "nft", "-f", "-", input=self.firewall)

    def ping(self, addr, success):
        proc = subprocess.run(
            [
                *SUDO,
                sys.executable,
                str(ROOT / "tests/netns_exec.py"),
                self.client,
                *PING,
                "-c",
                "1",
                "-W",
                "1",
                addr,
            ],
            capture_output=True,
        )
        self.assertEqual(proc.returncode == 0, success)

    def test_a_both_families_blocked_and_loopback_allowed(self):
        self.ping("198.18.0.2", True)
        self.ping("2001:db8:1::2", True)
        self.install()
        self.ping("198.18.0.2", False)
        self.ping("2001:db8:1::2", False)
        self.ping("127.0.0.1", True)

    def test_b_failed_replacement_preserves_firewall(self):
        self.install()
        with self.assertRaises(subprocess.CalledProcessError):
            self.command(
                self.client,
                "nft",
                "-f",
                "-",
                input=self.firewall + "not valid nft syntax\n",
            )
        self.ping("198.18.0.2", False)
        self.ping("2001:db8:1::2", False)

    def test_c_interface_loss_and_reconnect(self):
        self.command(self.client, "ip", "link", "set", "eth0", "down")
        self.command(self.client, "ip", "link", "set", "eth0", "name", "wg0")
        self.command(self.client, "ip", "link", "set", "wg0", "up")
        self.install()
        self.ping("198.18.0.2", True)
        self.command(self.client, "ip", "link", "set", "wg0", "down")
        self.command(self.client, "ip", "link", "set", "wg0", "name", "eth0")
        self.command(self.client, "ip", "link", "set", "eth0", "up")
        self.ping("198.18.0.2", False)
        self.install()
        self.ping("198.18.0.2", False)

    def process(self, ns, code):
        proc = subprocess.Popen(
            [
                *SUDO,
                sys.executable,
                str(ROOT / "tests/netns_exec.py"),
                ns,
                sys.executable,
                "-u",
                "-c",
                code,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

        def stop():
            subprocess.run(
                [*SUDO, "kill", "-TERM", "--", "-" + str(proc.pid)], capture_output=True
            )
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    [*SUDO, "kill", "-KILL", "--", "-" + str(proc.pid)],
                    capture_output=True,
                )
                proc.wait()
            for pipe in [proc.stdin, proc.stdout, proc.stderr]:
                pipe.close()

        self.addCleanup(stop)
        return proc

    def test_d_incoming_ui_replies_still_work(self):
        self.install()
        server = self.process(
            self.client,
            "import socket;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',5055));s.listen();print('ready');c,_=s.accept();c.sendall(b'UI');c.close()",
        )
        self.assertEqual(server.stdout.readline().strip(), "ready")
        result = self.command(
            self.server,
            sys.executable,
            "-c",
            "import socket;s=socket.create_connection(('198.18.0.1',5055),2);assert s.recv(2)==b'UI'",
        )
        self.assertEqual(result.returncode, 0)

    def test_e_existing_outbound_connection_does_not_bypass_new_firewall(self):
        self.command(self.client, "nft", "delete", "table", "inet", "pompey")
        server = self.process(
            self.server,
            "import socket;s=socket.socket();s.bind(('0.0.0.0',20000));s.listen();print('ready');c,_=s.accept();\nwhile True:\n data=c.recv(20)\n if not data:break\n c.sendall(data)",
        )
        self.assertEqual(server.stdout.readline().strip(), "ready")
        client = self.process(
            self.client,
            "import socket,sys;s=socket.create_connection(('198.18.0.2',20000),2);s.sendall(b'first');assert s.recv(5)==b'first';print('connected');input();s.sendall(b'after');\ntry:s.recv(5);print('leaked')\nexcept TimeoutError:print('blocked')",
        )
        self.assertEqual(client.stdout.readline().strip(), "connected")
        self.install()
        client.stdin.write("continue\n")
        client.stdin.flush()
        self.assertEqual(client.stdout.readline().strip(), "blocked")

    def test_f_only_configured_udp_endpoint_is_exempt(self):
        config = re.sub(r"Endpoint\s*=.*", "Endpoint = 198.18.0.2:51820", self.config)
        self.command(self.client, "nft", "-f", "-", input=rules(config, ""))
        server = self.process(
            self.server,
            "import socket;s=socket.socket(type=socket.SOCK_DGRAM);s.bind(('0.0.0.0',51820));print('ready');data,addr=s.recvfrom(100);s.sendto(data,addr)",
        )
        self.assertEqual(server.stdout.readline().strip(), "ready")
        self.command(
            self.client,
            sys.executable,
            "-c",
            "import socket;s=socket.socket(type=socket.SOCK_DGRAM);s.settimeout(2);s.setsockopt(socket.SOL_SOCKET,36,20559);s.sendto(b'endpoint',('198.18.0.2',51820));assert s.recv(100)==b'endpoint'",
        )
        self.ping("198.18.0.2", False)

    def test_g_root_application_dns_cannot_use_bootstrap_or_lan_exceptions(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(
                os.environ,
                POMPEY_BOOTSTRAP_RESOLV=str(Path(folder, "bootstrap-resolv.conf")),
            ),
        ):
            Path(folder, "bootstrap-resolv.conf").write_text("nameserver 198.18.0.2\n")
            firewall = rules(self.config, "198.18.0.0/24")
        self.command(self.client, "nft", "-f", "-", input=firewall)
        code = r"""import socket,threading
u=socket.socket(type=socket.SOCK_DGRAM);u.bind(('0.0.0.0',53))
t=socket.socket();t.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);t.bind(('0.0.0.0',53));t.listen()
def udp():
 while True:
  data,peer=u.recvfrom(1024)
  if data==b'DNS':response=data
  else:
   pos=12
   while data[pos]:pos+=data[pos]+1
   response=data[:2]+b'\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00'+data[12:pos+5]+b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\xc6\x12\x00\x02'
  u.sendto(response,peer)
threading.Thread(target=udp,daemon=True).start()
print('ready',flush=True)
while True:
 c,_=t.accept();c.sendall(b'DNS');c.close()
"""
        server = self.process(self.server, code)
        self.assertEqual(server.stdout.readline().strip(), "ready")
        self.command(
            self.client,
            sys.executable,
            "-c",
            "import os,socket;os.setgroups([]);os.setgid(65534);os.setuid(65534);u=socket.socket(type=socket.SOCK_DGRAM);u.settimeout(1);u.sendto(b'DNS',('198.18.0.2',53));assert u.recv(3)==b'DNS';t=socket.create_connection(('198.18.0.2',53),1);assert t.recv(3)==b'DNS'",
        )
        with tempfile.TemporaryDirectory() as folder:
            resolver = Path(folder, "resolv.conf")
            resolver.write_text("nameserver 198.18.0.2\n")
            self.command(
                self.client,
                sys.executable,
                "-c",
                "import os,sys;sys.path.insert(0,'/usr/local/bin');"
                f"os.environ['POMPEY_BOOTSTRAP_RESOLV']={str(resolver)!r};"
                "from vpn_config import resolve_endpoint;"
                "assert resolve_endpoint('vpn.fixture')=='198.18.0.2'",
            )
        engine = """import os,socket
assert os.getuid()==0
for protocol in [socket.SOCK_DGRAM,socket.SOCK_STREAM]:
 s=socket.socket(type=protocol);s.settimeout(.4)
 try:
  if protocol==socket.SOCK_DGRAM:s.sendto(b'DNS',('198.18.0.2',53))
  else:s.connect(('198.18.0.2',53))
  s.recv(3)
 except (TimeoutError,OSError):pass
 else:raise AssertionError('Engine DNS escaped through LAN')
 finally:s.close()
"""
        self.command(self.client, sys.executable, "-c", engine)
        self.command(self.client, "nft", "list", "table", "inet", "pompey")

    def test_h_root_applications_can_reach_local_setup(self):
        self.install()
        code = """import socket
s=socket.socket();s.bind(('127.0.0.1',8097));s.listen();print('ready',flush=True)
while True:
 c,_=s.accept();c.sendall(b'setup');c.close()
"""
        server = self.process(self.client, code)
        self.assertEqual(server.stdout.readline().strip(), "ready")
        self.command(
            self.client,
            sys.executable,
            "-c",
            "import socket;s=socket.create_connection(('127.0.0.1',8097),1);assert s.recv(5)==b'setup'",
        )

    def test_i_published_ui_replies_bypass_vpn_routes_without_outbound_access(self):
        self.install()
        self.command(self.client, "ip", "link", "add", "wg0", "type", "dummy")
        self.command(self.client, "ip", "link", "set", "wg0", "up")
        self.addCleanup(lambda: self.command(self.client, "ip", "link", "del", "wg0"))
        for family, router, local, remote, tunnel_net in [
            (4, "198.18.0.2", "198.18.0.1", "198.19.0.2", "198.19.0.0/16"),
            (6, "2001:db8:1::2", "2001:db8:1::1", "2001:db8:2::2", "2001:db8:2::/64"),
        ]:
            with self.subTest(family=family):
                flag = f"-{family}"
                self.command(self.client, "ip", flag, "addr", "replace", local + ("/24" if family == 4 else "/64"), "dev", "eth0", *([] if family == 4 else ["nodad"]))
                self.command(self.server, "ip", flag, "addr", "add", remote + ("/32" if family == 4 else "/128"), "dev", "lo")
                self.command(self.client, "ip", flag, "route", "replace", "default", "via", router, "dev", "eth0")
                self.command(self.client, "ip", flag, "route", "replace", tunnel_net, "dev", "wg0")
                server = self.process(self.client, f"""import socket,threading
def serve(port):
 s=socket.socket(socket.AF_INET if {family}==4 else socket.AF_INET6)
 s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
 s.bind(('{local}',port));s.listen()
 def accept():
  while True:
   c,_=s.accept();c.sendall(b'UI');c.close()
 threading.Thread(target=accept,daemon=True).start()
for port in [5055,9696,8099]:serve(port)
print('ready',flush=True)
threading.Event().wait()
""")
                ready = server.stdout.readline().strip()
                if ready != "ready":
                    self.fail(server.stderr.read())
                connect = f"""import socket
s=socket.socket(socket.AF_INET if {family}==4 else socket.AF_INET6)
s.settimeout(.4);s.bind(('{remote}',0))
"""
                self.command(self.server, sys.executable, "-c", connect + f"""
try:s.connect(('{local}',5055))
except OSError:pass
else:raise AssertionError('Expected UI return traffic to be lost in wg0')
finally:s.close()
""")
                self.command(self.client, sys.executable, "-c", f"""
import sys
sys.path.insert(0,'/usr/local/bin')
from vpn_routes import preserve_ui_replies
preserve_ui_replies({family},{{'gateway':'{router}','dev':'eth0'}})
preserve_ui_replies({family},{{'gateway':'{router}','dev':'eth0'}})
""")
                for port in [5055, 9696, 8099]:
                    self.command(self.server, sys.executable, "-c", connect + f"s.connect(('{local}',{port}));assert s.recv(2)==b'UI';s.close()")
                server.terminate()
                server.wait(3)
                listener = self.process(self.server, f"""import socket
s=socket.socket(socket.AF_INET if {family}==4 else socket.AF_INET6)
s.bind(('{router}',18080));s.listen();print('ready',flush=True)
while True:
 c,_=s.accept();c.sendall(b'escaped');c.close()
""")
                self.assertEqual(listener.stdout.readline().strip(), "ready")
                self.command(self.client, sys.executable, "-c", f"""import socket
s=socket.socket(socket.AF_INET if {family}==4 else socket.AF_INET6)
s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.settimeout(.4);s.bind(('{local}',5055))
try:s.connect(('{router}',18080))
except OSError:pass
else:raise AssertionError('New outbound connection escaped using a UI source port')
finally:s.close()
""")

    def test_j_connected_container_services_work_but_dns_stays_blocked(self):
        # Earlier interface-down coverage can remove its IPv6 address.
        self.command(self.client, "ip", "-6", "addr", "replace", "2001:db8:1::1/64", "dev", "eth0", "nodad")
        result = self.command(self.client, sys.executable, "-c", """
import sys
sys.path.insert(0,'/usr/local/bin')
from vpn_firewall import rules,container_networks
print(rules('', '', container_networks()))
""")
        self.command(self.client, "nft", "-f", "-", input=result.stdout)
        for family, address in [(4, "198.18.0.2"), (6, "2001:db8:1::2")]:
            with self.subTest(family=family):
                server = self.process(self.server, f"""import socket,threading
kind=socket.AF_INET if {family}==4 else socket.AF_INET6
def tcp(port,reply):
 s=socket.socket(kind);s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('{address}',port));s.listen()
 def accept():
  while True:
   c,_=s.accept();c.sendall(reply);c.close()
 threading.Thread(target=accept,daemon=True).start()
tcp(32400,b'Plex');tcp(53,b'DNS')
u=socket.socket(kind,socket.SOCK_DGRAM);u.bind(('{address}',53))
def dns():
 while True:
  data,peer=u.recvfrom(100);u.sendto(data,peer)
threading.Thread(target=dns,daemon=True).start()
print('ready',flush=True)
threading.Event().wait()
""")
                self.assertEqual(server.stdout.readline().strip(), "ready")
                self.command(self.client, sys.executable, "-c", f"""import socket
kind=socket.AF_INET if {family}==4 else socket.AF_INET6
s=socket.create_connection(('{address}',32400),1)
assert s.recv(4)==b'Plex';s.close()
for protocol in [socket.SOCK_DGRAM,socket.SOCK_STREAM]:
 s=socket.socket(kind,protocol);s.settimeout(.4)
 try:
  if protocol==socket.SOCK_DGRAM:s.sendto(b'DNS',('{address}',53))
  else:s.connect(('{address}',53))
  s.recv(3)
 except OSError:pass
 else:raise AssertionError('Container-network access allowed cleartext DNS')
 finally:s.close()
""")


if __name__ == "__main__":
    unittest.main()
