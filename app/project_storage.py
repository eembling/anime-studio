"""Resolve app-owned storage and copy legacy projects without replacing their originals."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path
from platform_runtime import storage_lock, config_directory


def atomic_text(path, text):
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        temporary.write_text(text, encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fingerprint(folder):
    result = {}
    for name in ('studio.db', 'studio.db-wal', 'studio.db-journal'):
        path = folder / name
        if path.exists():
            stat = path.stat()
            result[name] = [stat.st_size, stat.st_mtime_ns]
    return result


def inside(path, parent):
    return path == parent or parent in path.parents


def migrate_project(source, config):
    source, config = source.resolve(), config.resolve()
    if inside(source, config):
        return source
    if not (source / 'studio.db').is_file():
        raise ValueError('The remembered project has no studio.db: ' + str(source) + '. It was not replaced with an empty project.')
    destination = config / 'Projects' / hashlib.sha256(str(source).encode()).hexdigest()[:16]
    signature = fingerprint(source)
    if destination.exists():
        marker = destination / '.migration.json'
        saved = json.loads(marker.read_text(encoding='utf-8')) if marker.is_file() else {}
        if saved.get('source') == str(source) and saved.get('fingerprint') == signature and (destination/'studio.db').is_file():
            return destination
        raise ValueError('An existing migrated project differs from the legacy source. Both copies were preserved: ' + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name('.migrating-' + uuid.uuid4().hex)
    try:
        # SQLite backup includes committed WAL contents; never copy a live DB as an ordinary file.
        def ignore(folder, names):
            return [n for n in names if Path(folder) == source and n in ('studio.db', 'studio.db-wal', 'studio.db-shm', 'studio.db-journal')]
        shutil.copytree(source, staging, symlinks=True, ignore=ignore)
        original = sqlite3.connect((source/'studio.db').as_uri()+'?mode=ro', uri=True)
        copied = sqlite3.connect(str(staging/'studio.db'))
        try:
            original.backup(copied)
            if copied.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('The migrated database failed its integrity check. The original was preserved.')
        finally:
            copied.close()
            original.close()
        if fingerprint(source) != signature:
            raise ValueError('The project changed during migration. Close the other Anime Studio instance and retry. The original was preserved.')
        (staging/'.migration.json').write_text(json.dumps({'source':str(source),'fingerprint':signature},indent=2), encoding='utf-8')
        os.replace(staging, destination)
        return destination
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def resolve_storage(config, app_dir=None, override=None, home=None):
    config = Path(config).expanduser().resolve()
    config.mkdir(parents=True, exist_ok=True)
    # An explicit advanced override is intentional, not an automatic fallback or preference rewrite.
    if override:
        result = Path(override).expanduser().resolve()
        result.mkdir(parents=True, exist_ok=True)
        return result
    with storage_lock(config/'storage.lock'):
        remembered = config/'data-folder'
        if remembered.is_file():
            saved_path = remembered.read_text(encoding='utf-8').strip()
            if not saved_path:
                raise ValueError('The saved project location is empty. No project was replaced.')
            source = Path(saved_path).expanduser().resolve()
            from project_workspace import identity, read_catalog
            if inside(source, config) or identity(source) in read_catalog(config):
                if not source.is_dir():
                    raise ValueError('The saved project folder is missing: ' + str(source))
                return source
            result = migrate_project(source, config)
            if not (config/'data-folder.before-managed-storage').exists():
                shutil.copy2(remembered, config/'data-folder.before-managed-storage')
        else:
            candidates = []
            if app_dir:
                candidates.append(Path(app_dir)/'AnimeStudio Data')
            home = Path(home) if home else Path.home()
            if sys.platform != 'win32':
                candidates.extend([home/'Documents/AnimeStudio Data', home/'Documents/AnimeStudio'])
            source = next((p for p in candidates if (p/'studio.db').is_file()), None)
            if source:
                result = migrate_project(source, config)
            else:
                result = config/'Project Data'
                result.mkdir(exist_ok=True)
        atomic_text(remembered, str(result))
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config-dir', default=str(config_directory()))
    parser.add_argument('--app-dir')
    parser.add_argument('--override')
    parser.add_argument('--controller-port', type=int)
    args = parser.parse_args()
    try:
        project = resolve_storage(args.config_dir,args.app_dir,args.override)
        if args.controller_port is not None:
            from project_workspace import launch_port
            print(launch_port(project, args.controller_port))
        else:
            print(project)
    except Exception as error:
        print('Could not load the saved Anime Studio project: ' + str(error),file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
