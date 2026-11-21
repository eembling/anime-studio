#!/usr/bin/env python3
"""Fetch or inventory the pinned Anime Studio server recipe assets."""
import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path


MANIFEST = Path(__file__).with_name('recipe_manifest.json')


def verifies(path, asset):
    if not path.is_file() or path.stat().st_size != asset['bytes']:
        return False
    digest = hashlib.sha256() if 'sha256' in asset else hashlib.sha1()
    if 'git_blob' in asset:
        digest.update(f"blob {asset['bytes']}\0".encode())
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest() == (asset.get('sha256') or asset['git_blob'])


def install(path, asset):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + '.part')
    try:
        with urllib.request.urlopen(asset['url'], timeout=120) as response, partial.open('wb') as stream:
            shutil.copyfileobj(response, stream, length=1 << 20)
        if not verifies(partial, asset):
            raise ValueError(f"Downloaded size or checksum mismatch: {asset['path']}")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def revision(repo):
    result = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('verify', 'fetch', 'node'))
    parser.add_argument('root', type=Path)
    parser.add_argument('group', choices=('all', 'picture', 'lipsync', 'tts', 'ti2v'), default='all', nargs='?')
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    if not (root/'main.py').is_file():
        parser.error(f'ComfyUI main.py not found at {root}')
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))

    if args.command == 'node':
        node = manifest['tts_node']
        target = root/'custom_nodes'/'ComfyUI-Qwen-TTS'
        if not target.exists():
            subprocess.run(['git', 'clone', '--no-checkout', node['url'], str(target)], check=True)
            subprocess.run(['git', '-C', str(target), 'checkout', '--detach', node['commit']], check=True)
        status = subprocess.check_output(['git', '-C', str(target), 'status', '--porcelain'], text=True)
        if revision(target) != node['commit'] or status:
            parser.error(f'TTS node pack differs from {node["commit"]}. Review the local checkout before switching versions.')
        return

    failures = 0
    if args.command == 'verify':
        expected = manifest['comfyui']
        actual = revision(root)
        print(f"ComfyUI checkout: {actual or 'unavailable'} (reference {expected['version']} · {expected['commit']})")
        failures += actual != expected['commit']
        if args.group in ('all', 'tts'):
            actual = revision(root/'custom_nodes'/'ComfyUI-Qwen-TTS')
            expected = manifest['tts_node']['commit']
            print(f'Qwen TTS node: {actual or "unavailable"} (pinned {expected})')
            failures += actual != expected

    for asset in manifest['assets']:
        group = asset['group']
        if args.group not in ('all', group) and not (group == 'shared' and args.group in ('picture', 'lipsync', 'ti2v')):
            continue
        destination = root/'models'/asset['path']
        if verifies(destination, asset):
            print(f"Verified {asset['path']}", flush=True)
        elif args.command == 'fetch':
            print(f"Fetching {asset['path']}", flush=True)
            install(destination, asset)
        else:
            print(f"{'Checksum or size mismatch' if destination.exists() else 'Missing'}: {asset['path']}")
            failures += 1
    raise SystemExit(bool(failures))


if __name__ == '__main__':
    main()
