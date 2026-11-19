import base64
import hashlib
import json
import ssl
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from comfy_events import ProgressEvents

class Socket:
    def __init__(self):self.buffer=b'';self.closed=False
    def sendall(self,data):
        if not data.startswith(b'GET'):return
        key=data.split(b'Sec-WebSocket-Key: ')[1].split(b'\r\n')[0]
        accept=base64.b64encode(hashlib.sha1(key+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
        event=json.dumps({'type':'progress','data':{'value':3,'max':10,'prompt_id':'render'}}).encode()
        self.buffer=b'HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n'+bytes([0x81,len(event)])+event+b'\x88\x00'
    def recv(self,count):data=self.buffer[:count];self.buffer=self.buffer[count:];return data
    def settimeout(self,value):pass
    def shutdown(self,*args):pass
    def close(self):self.closed=True

class EventTests(unittest.TestCase):
    def test_https_events_require_tls_12_or_newer(self):
        transport=Socket();events=[]
        context=SimpleNamespace(minimum_version=None,wrap_socket=lambda raw,server_hostname:raw)
        with patch('comfy_events.socket.create_connection',return_value=transport), patch('comfy_events.ssl.create_default_context',return_value=context):
            ProgressEvents('https://local:8188','client',events.append).run()
        self.assertEqual(context.minimum_version,ssl.TLSVersion.TLSv1_2)
        self.assertEqual(events[0]['data']['value'],3)

    def test_progress_event_is_received_and_connection_closed(self):
        transport=Socket();events=[]
        with patch('comfy_events.socket.create_connection',return_value=transport):
            ProgressEvents('http://local:8188','client',events.append).run()
        self.assertEqual(events[0]['data']['value'],3)
        self.assertTrue(transport.closed)
    def test_unavailable_events_are_optional(self):
        with patch('comfy_events.socket.create_connection',side_effect=OSError('unavailable')):
            ProgressEvents('http://local:8188','client',lambda _:self.fail('unexpected event')).run()

if __name__=='__main__':unittest.main()
