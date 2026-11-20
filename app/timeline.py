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
