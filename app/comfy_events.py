"""Optional stdlib WebSocket reader for ComfyUI node/sampler events.
History remains the authority for completed renders; this connection only reports progress.
"""
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
import urllib.parse


class ProgressEvents:
    def __init__(self, base, client_id, callback):
        self.base, self.client_id, self.callback = base, client_id, callback
        self.stop = threading.Event()
        self.socket = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.stop.set()
        if self.socket:
            try: self.socket.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            self.socket.close()

    def run(self):
        try:
            url = urllib.parse.urlsplit(self.base)
            raw = socket.create_connection((url.hostname, url.port or (443 if url.scheme == 'https' else 80)), timeout=3)
            if url.scheme == 'https':
                context = ssl.create_default_context()
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                self.socket = context.wrap_socket(raw, server_hostname=url.hostname)
            else:
                self.socket = raw
            ws = self.socket
            key = base64.b64encode(os.urandom(16)).decode()
            path = url.path.rstrip('/')+'/ws?'+urllib.parse.urlencode({'clientId': self.client_id})
            ws.sendall((f'GET {path} HTTP/1.1\r\nHost: {url.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
            header = bytearray()
            while not header.endswith(b'\r\n\r\n') and len(header) < 16384:
                chunk = ws.recv(1)
                if not chunk: return
                header.extend(chunk)
            accept = base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
            if b' 101 ' not in header.split(b'\r\n',1)[0] or accept not in header: return
            ws.settimeout(2)
            def read(size):
                data = bytearray()
                while len(data) < size and not self.stop.is_set():
                    try: chunk = ws.recv(size-len(data))
                    except socket.timeout: continue
                    if not chunk: raise EOFError()
                    data.extend(chunk)
                if len(data) != size: raise EOFError()
                return bytes(data)
            while not self.stop.is_set():
                first, second = read(2)
                opcode, length = first & 15, second & 127
                if length == 126: length = struct.unpack('!H', read(2))[0]
                elif length == 127: length = struct.unpack('!Q', read(8))[0]
                if length > 16*1024*1024: return
                mask = read(4) if second & 128 else None
                data = read(length)
                if mask: data = bytes(c ^ mask[i%4] for i,c in enumerate(data))
                if opcode == 8: return
                if opcode == 9:
                    nonce=os.urandom(4)
                    ws.sendall(bytes([0x8a,0x80|len(data)])+nonce+bytes(c^nonce[i%4] for i,c in enumerate(data)))
                if opcode == 1 and first & 128:
                    event=json.loads(data)
                    if event.get('type') in ('progress','executing'):
                        self.callback(event)
        except (OSError, ValueError, EOFError):
            pass  # Older/mock servers may not support events; history polling continues.
        finally:
            if self.socket: self.socket.close()
