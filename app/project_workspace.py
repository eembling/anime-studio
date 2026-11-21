"""Local project catalog and discovery of separately running render controllers."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import threading
import urllib.parse
import urllib.request
import uuid

from platform_runtime import storage_lock
from project_storage import atomic_text

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def identity(folder):
    return hashlib.sha256(str(folder.resolve()).encode()).hexdigest()


def read_catalog(config):
    path = config / 'projects.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def saved_port(folder):
    try:
        port = int((folder / '.controller-port').read_text(encoding='utf-8'))
        return port if 0 < port < 65536 else None
    except (OSError, ValueError):
        return None


def controller_status(folder):
    port = saved_port(folder)
    if not port:
        return None
    try:
        with OPENER.open(f'http://127.0.0.1:{port}/api/ping', timeout=.4) as response:
            status = json.load(response)
        if status.get('app') == 'anime-studio' and status.get('project') == identity(folder):
            return dict(status, port=port)
    except (OSError, ValueError):
        pass
    return None


def launch_port(folder, preferred):
    existing = controller_status(folder)
    if existing:
        return existing['port']
    with socket.socket() as probe:
        for port in dict.fromkeys((saved_port(folder), int(preferred), 0)):
            if port is None:
                continue
            try:
                probe.bind(('127.0.0.1', port))
                break
            except OSError:
                if port == 0:
                    raise
        return probe.getsockname()[1]


class ProjectWorkspace:
    def __init__(self, studio, config, command):
        self.studio = studio
        self.config = config.resolve()
        self.config.mkdir(parents=True, exist_ok=True)
        self.command = command
        self.current = identity(studio.data)
        with storage_lock(self.config / 'projects.lock'):
            catalog = read_catalog(self.config)
            if self.current not in catalog:
                self.register(catalog, studio.data, studio.settings().get('title') or studio.data.name)
            self.name = catalog[self.current]['name']

    def register(self, catalog, folder, name):
        key = identity(folder)
        catalog[key] = {'name': name, 'path': str(folder)}
        atomic_text(self.config / 'projects.json', json.dumps(catalog, indent=2))
        return key

    def state(self):
        projects = []
        for key, item in read_catalog(self.config).items():
            folder = Path(item['path'])
            current = key == self.current
            status = controller_status(folder)
            projects.append(dict(item, id=key, current=current, available=(folder/'studio.db').is_file(),
                                 running=status is not None, counts=status.get('job_counts', {}) if status else {}))
        return sorted(projects, key=lambda project: (not project['current'], project['name'].casefold()))

    def action(self, body):
        action = body.get('action')
        if action not in ('create', 'open', 'switch'):
            raise ValueError('Choose a project action.')
        with storage_lock(self.config / 'projects.lock'):
            catalog = read_catalog(self.config)
            if action == 'switch':
                item = catalog.get(body.get('id'))
                if not item:
                    raise ValueError('Project no longer appears in the list.')
                folder, name = Path(item['path']), item['name']
            elif action == 'open':
                raw = str(body.get('path') or '').strip()
                if not raw or not Path(raw).expanduser().is_absolute():
                    raise ValueError('Enter the full path of a project folder.')
                folder = Path(raw).expanduser().resolve()
                item = catalog.get(identity(folder), {})
                name = item.get('name', folder.name)
            else:
                name = str(body.get('name') or '').strip()[:120]
                if not name:
                    raise ValueError('Name the project.')
                folder = self.config / 'Projects' / uuid.uuid4().hex
                folder.mkdir(parents=True)
            if action != 'create':
                self.validate_project(folder)
            key = identity(folder)
            if key == self.current:
                atomic_text(self.config / 'data-folder', str(folder))
                return {'ok':True, 'current':True}
            # The child may register only after this lock is released.
            self.register(catalog, folder, name)
        status = self.connect(folder)
        with storage_lock(self.config / 'projects.lock'):
            atomic_text(self.config / 'data-folder', str(folder))
        return {'ok':True, 'port':status['port']}

    @staticmethod
    def validate_project(folder):
        database = folder/'studio.db'
        if not database.is_file():
            raise ValueError('Choose an existing Anime Studio folder containing studio.db.')
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'shots', 'settings', 'jobs'} <= tables:
            raise ValueError('The selected database is not an Anime Studio project.')

    def connect(self, folder):
        status = controller_status(folder)
        if status:
            return status
        # A per-folder launch lock handles simultaneous requests from two project windows.
        with storage_lock(folder / '.controller-start.lock'):
            status = controller_status(folder)
            if status:
                return status
            options = ({'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
                       if sys.platform == 'win32' else {'start_new_session':True})
            env = dict(os.environ, ANIME_STUDIO_CONFIG=str(self.config), PYINSTALLER_RESET_ENVIRONMENT='1')
            with (folder/'app.log').open('ab') as log:
                process = subprocess.Popen(self.command + ['--projects', '--host', '127.0.0.1', '--port', '0', '--data', str(folder)],
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, **options)
            for _ in range(100):
                status = controller_status(folder)
                if status:
                    # Reap a child that later exits while this controller is still alive.
                    threading.Thread(target=process.wait, daemon=True).start()
                    return status
                if process.poll() is not None:
                    break
                time.sleep(.1)
            if process.poll() is None:
                threading.Thread(target=process.wait, daemon=True).start()
            raise RuntimeError(f'Could not open {folder.name}. See its app.log for startup details.')

    def server_lock(self, url):
        from platform_runtime import project_controller
        parsed = urllib.parse.urlsplit(url)
        hostname = parsed.hostname or ''
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        key = hashlib.sha256(f'{hostname}:{port}{parsed.path.rstrip("/")}'.encode()).hexdigest()
        directory = self.config / 'Render Workers'
        directory.mkdir(exist_ok=True)
        return project_controller(directory / (key+'.lock'))
