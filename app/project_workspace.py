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
SHARED_SETTINGS = frozenset({
    'default_duration', 'draft_width', 'draft_height', 'draft_max_frames',
    'kf_width', 'kf_height', 'sheet_width', 'sheet_height', 'vid_width', 'vid_height',
    'default_voice', 'tts_model', 'tts_language', 'job_timeout', 'video_min_free_vram',
})
SERVER_FIELDS = ('name', 'url', 'roles', 'enabled')


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
    def __init__(self, studio, config, command, default_servers):
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
        self.configuration_path = self.config / 'generation-settings.json'
        self.configuration_lock = self.config / 'generation-settings.lock'
        self.configuration_revision = None
        with storage_lock(self.configuration_lock):
            if not self.configuration_path.exists():
                self.write_configuration(self.initial_configuration(catalog, default_servers))
            else:
                self.load_configuration()

    def initial_configuration(self, catalog, default_servers):
        defaults = {tuple(server) + (1,) for server in default_servers}
        projects = [self.studio.db.q]
        databases = []
        profiles = []
        try:
            for key, item in catalog.items():
                if key == self.current:
                    continue
                path = Path(item['path']) / 'studio.db'
                try:
                    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
                    connection.row_factory = sqlite3.Row
                    databases.append(connection)
                    projects.append(connection.execute)
                except (OSError, sqlite3.Error):
                    continue
            for query in projects:
                try:
                    servers = [dict(row) for row in query('SELECT name,url,roles,enabled FROM instances ORDER BY id')]
                    workflows = {row['stage']:dict(row) for row in query('SELECT * FROM workflows')}
                    settings = {row['key']:row['value'] for row in query('SELECT key,value FROM settings') if row['key'] in SHARED_SETTINGS}
                except sqlite3.Error:
                    continue
                customized = sum(tuple(row[key] for key in SERVER_FIELDS) not in defaults for row in servers)
                profiles.append((customized, servers, workflows, settings))
        finally:
            for connection in databases:
                connection.close()
        # Keep the current project's setup when it is already configured.
        if not profiles[0][0]:
            profiles.sort(key=lambda profile: -profile[0])
        _, preferred, workflows, settings = profiles[0]
        servers = {row['url']:row for row in preferred}
        for _, rows, _, _ in profiles[1:]:
            for row in rows:
                if tuple(row[key] for key in SERVER_FIELDS) not in defaults:
                    servers.setdefault(row['url'], row)
        return {'servers':list(servers.values()), 'workflows':workflows, 'settings':settings}

    def configuration_stamp(self):
        stat = self.configuration_path.stat()
        return stat.st_ino, stat.st_mtime_ns, stat.st_size

    def refresh_configuration(self):
        if self.configuration_revision == self.configuration_stamp():
            return
        # Keep ordinary state polling independent of server readiness checks.
        with self.studio.production.lock, storage_lock(self.configuration_lock):
            if self.configuration_revision != self.configuration_stamp():
                self.load_configuration()

    def load_configuration(self):
        self.apply_configuration(json.loads(self.configuration_path.read_text(encoding='utf-8')))

    def write_configuration(self, configuration):
        atomic_text(self.configuration_path, json.dumps(configuration, indent=2))
        self.apply_configuration(configuration)

    def apply_configuration(self, configuration):
        st = self.studio
        if self.configuration_revision is None or self.configuration['servers'] != configuration['servers']:
            with st.db.transaction():
                rows = {row['url']: row for row in st.db.q('SELECT * FROM instances ORDER BY shared_retired DESC,id DESC')}
                st.db.x('UPDATE instances SET shared_retired=1 WHERE shared_retired=0')
                for server in configuration['servers']:
                    values = tuple(server[key] for key in SERVER_FIELDS)
                    prior = rows.get(server['url'])
                    if prior:
                        st.db.x('UPDATE instances SET name=?,url=?,roles=?,enabled=?,shared_retired=0 WHERE id=?', (*values, prior['id']))
                    else:
                        st.db.x('INSERT INTO instances(name,url,roles,enabled) VALUES(?,?,?,?)', values)
            if hasattr(st, 'worker'):
                st.worker.last_health = 0
        self.configuration = configuration
        self.configuration_revision = self.configuration_stamp()
        st.production.readiness = {}

    def change_configuration(self, update):
        with storage_lock(self.configuration_lock):
            configuration = json.loads(self.configuration_path.read_text(encoding='utf-8'))
            update(configuration)
            self.write_configuration(configuration)

    def save_server(self, server_id, values):
        def update(configuration):
            servers = configuration['servers']
            if server_id:
                prior = self.studio.db.one('SELECT url FROM instances WHERE id=? AND shared_retired=0', (server_id,))
                if not prior or not any(row['url'] == prior['url'] for row in servers):
                    raise ValueError('This server has changed. Reload the server list and try again.')
                servers[:] = [row for row in servers if row['url'] != prior['url']]
            if values:
                servers[:] = [row for row in servers if row['url'] != values['url']]
                servers.append(values)
        self.change_configuration(update)

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
