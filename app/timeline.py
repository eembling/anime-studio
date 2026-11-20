"""Canonical output-frame timing shared by assembly, cues and previews."""
import math


def frames(seconds, fps):
    return max(1, int(math.floor(float(seconds)*int(fps)+0.5)))


def layout(shots, fps):
    cursor = 0
    rows = []
    for shot in shots:
        count = frames(shot['duration'], fps)
        rows.append({'id': shot['id'], 'start_frame': cursor, 'frames': count,
                     'start': cursor/int(fps), 'duration': count/int(fps)})
        cursor += count
    return rows


def upgrade_legacy_fps(settings):
    """Move legacy 24 fps projects to the 16 fps default once, including old episodes."""
    upgraded = dict(settings)
    if not upgraded.get('studio_16fps_v1'):
        if str(upgraded.get('out_fps', '16')) == '24':
            upgraded['out_fps'] = '16'
        upgraded['studio_16fps_v1'] = '1'
    return upgraded
