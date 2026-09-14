#!/usr/bin/env python3
"""Real packets in two isolated namespaces; never modify the host firewall/routes."""
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pompey/rootfs/usr/local/bin'))
from vpn_firewall import rules


class FirewallPackets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(shutil.which(x) for x in ['ip','nft','ping','sudo']):
            if os.environ.get('POMPEY_REQUIRE_NETWORK_TESTS') == '1':raise RuntimeError('Missing packet-test prerequisites')
            raise unittest.SkipTest('packet tests require iproute2, nftables, ping and passwordless sudo')
        if subprocess.run(['sudo','-n','true'],capture_output=True).returncode:
            if os.environ.get('POMPEY_REQUIRE_NETWORK_TESTS') == '1':raise RuntimeError('Missing passwordless sudo')
            raise unittest.SkipTest('packet tests require passwordless sudo')
        cls.client='pompey-fw-c-'+str(os.getpid());cls.server='pompey-fw-s-'+str(os.getpid())
        created = subprocess.run(['sudo','-n','ip','netns','add',cls.client],capture_output=True,text=True)
        if created.returncode:
            if os.environ.get('POMPEY_REQUIRE_NETWORK_TESTS') == '1':
                raise RuntimeError(created.stderr)
            raise unittest.SkipTest('Host cannot create network namespaces: ' + created.stderr.strip())
        cls.addClassCleanup(lambda:subprocess.run(['sudo','-n','ip','netns','del',cls.client],capture_output=True))
        subprocess.run(['sudo','-n','ip','netns','add',cls.server],check=True)
        cls.addClassCleanup(lambda:subprocess.run(['sudo','-n','ip','netns','del',cls.server],capture_output=True))
        cls.command(cls.client,'ip','link','add','eth0','type','veth','peer','name','other')
        cls.command(cls.client,'ip','link','set','other','netns',cls.server)
        for ns,dev,addr in [(cls.client,'eth0','198.18.0.1/24'),(cls.server,'other','198.18.0.2/24')]:
            cls.command(ns,'ip','addr','add',addr,'dev',dev)
            cls.command(ns,'ip','link','set',dev,'up');cls.command(ns,'ip','link','set','lo','up')
        cls.command(cls.client,'ip','-6','addr','add','2001:db8:1::1/64','dev','eth0','nodad')
        cls.command(cls.server,'ip','-6','addr','add','2001:db8:1::2/64','dev','other','nodad')
        cls.config=(ROOT/'tests/fixtures/wg0.conf').read_text()
        cls.firewall=rules(cls.config,'')

    @staticmethod
    def command(ns,*cmd,**kw):
        return subprocess.run(['sudo','-n','ip','netns','exec',ns,*cmd],check=True,capture_output=True,text=True,**kw)

    def install(self):self.command(self.client,'nft','-f','-',input=self.firewall)
    def ping(self,addr,success):
        proc=subprocess.run(['sudo','-n','ip','netns','exec',self.client,'ping','-c','1','-W','1',addr],capture_output=True)
        self.assertEqual(proc.returncode==0,success)

    def test_a_both_families_blocked_and_loopback_allowed(self):
        self.ping('198.18.0.2',True);self.ping('2001:db8:1::2',True)
        self.install()
        self.ping('198.18.0.2',False);self.ping('2001:db8:1::2',False);self.ping('127.0.0.1',True)

    def test_b_failed_replacement_preserves_firewall(self):
        self.install()
        with self.assertRaises(subprocess.CalledProcessError):
            self.command(self.client,'nft','-f','-',input=self.firewall+'not valid nft syntax\n')
        self.ping('198.18.0.2',False);self.ping('2001:db8:1::2',False)

    def test_c_interface_loss_and_reconnect(self):
        self.command(self.client,'ip','link','set','eth0','down')
        self.command(self.client,'ip','link','set','eth0','name','wg0')
        self.command(self.client,'ip','link','set','wg0','up')
        self.install();self.ping('198.18.0.2',True)
        self.command(self.client,'ip','link','set','wg0','down')
        self.command(self.client,'ip','link','set','wg0','name','eth0')
        self.command(self.client,'ip','link','set','eth0','up')
        self.ping('198.18.0.2',False)
        self.install();self.ping('198.18.0.2',False)

    def process(self, ns, code):
        proc=subprocess.Popen(['sudo','-n','ip','netns','exec',ns,sys.executable,'-u','-c',code],
                              stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        def stop():
            subprocess.run(['sudo','-n','kill','-TERM','--','-'+str(proc.pid)],capture_output=True)
            try:proc.wait(5)
            except subprocess.TimeoutExpired:
                subprocess.run(['sudo','-n','kill','-KILL','--','-'+str(proc.pid)],capture_output=True)
                proc.wait()
            for pipe in [proc.stdin,proc.stdout,proc.stderr]:pipe.close()
        self.addCleanup(stop)
        return proc

    def test_d_incoming_ui_replies_still_work(self):
        self.install()
        server=self.process(self.client,"import socket;s=socket.socket();s.bind(('0.0.0.0',5055));s.listen();print('ready');c,_=s.accept();c.sendall(b'UI');c.close()")
        self.assertEqual(server.stdout.readline().strip(),'ready')
        result=self.command(self.server,sys.executable,'-c',"import socket;s=socket.create_connection(('198.18.0.1',5055),2);assert s.recv(2)==b'UI'")
        self.assertEqual(result.returncode,0)

    def test_e_existing_outbound_connection_does_not_bypass_new_firewall(self):
        self.command(self.client,'nft','delete','table','inet','pompey')
        server=self.process(self.server,"import socket;s=socket.socket();s.bind(('0.0.0.0',20000));s.listen();print('ready');c,_=s.accept();\nwhile True:\n data=c.recv(20)\n if not data:break\n c.sendall(data)")
        self.assertEqual(server.stdout.readline().strip(),'ready')
        client=self.process(self.client,"import socket,sys;s=socket.create_connection(('198.18.0.2',20000),2);s.sendall(b'first');assert s.recv(5)==b'first';print('connected');input();s.sendall(b'after');\ntry:s.recv(5);print('leaked')\nexcept TimeoutError:print('blocked')")
        self.assertEqual(client.stdout.readline().strip(),'connected')
        self.install()
        client.stdin.write('continue\n');client.stdin.flush()
        self.assertEqual(client.stdout.readline().strip(),'blocked')

    def test_f_only_configured_udp_endpoint_is_exempt(self):
        config=re.sub(r'Endpoint\s*=.*','Endpoint = 198.18.0.2:51820',self.config)
        self.command(self.client,'nft','-f','-',input=rules(config,''))
        server=self.process(self.server,"import socket;s=socket.socket(type=socket.SOCK_DGRAM);s.bind(('0.0.0.0',51820));print('ready');data,addr=s.recvfrom(100);s.sendto(data,addr)")
        self.assertEqual(server.stdout.readline().strip(),'ready')
        self.command(self.client,sys.executable,'-c',"import socket;s=socket.socket(type=socket.SOCK_DGRAM);s.settimeout(2);s.sendto(b'endpoint',('198.18.0.2',51820));assert s.recv(100)==b'endpoint'")
        self.ping('198.18.0.2',False)


if __name__=='__main__':unittest.main()
