"""Compile authored fictional scenes against one frozen, timezone-aware clock."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import re


SCENARIO = Path(__file__).resolve().parent.parent / 'examples' / 'showcase.json'
DATE_TOKEN = re.compile(r'\{\{date:([+-]?\d+)\}\}')


def build_showcase(anchor=None):
    data = json.loads(SCENARIO.read_text(encoding='utf-8'))
    if isinstance(anchor, str):
        anchor = datetime.fromisoformat(anchor)
    anchor = anchor or datetime.now().astimezone()
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError('Demo anchor must include a timezone, for example +08:00.')
    anchor = anchor.replace(microsecond=0)
    groups = set(data['groups'])
    names = {name:'demo_'+name+('@chatroom' if name in groups else '') for name in data['contacts']}
    phases, mid, previous_end = [], 1, None
    for phase in data['phases']:
        rows = []
        for scene in phase['scenes']:
            start = anchor + timedelta(days=scene['day'], minutes=scene['minute'])
            for position, (sender, text) in enumerate(scene['lines']):
                if sender in groups:
                    raise ValueError('A group cannot be a message sender')
                timestamp = start + timedelta(seconds=position*15)
                if timestamp > anchor:
                    raise ValueError('Demo messages must not occur after the demonstration clock')
                text = DATE_TOKEN.sub(lambda match:(anchor.date()+timedelta(days=int(match[1]))).isoformat(), text)
                if '{{' in text:
                    raise ValueError('Unresolved demo date token')
                rows.append([mid, names[scene['chat']], names[sender], timestamp.isoformat(), text])
                mid += 1
        rows.sort(key=lambda row:(datetime.fromisoformat(row[3]), row[0]))
        if previous_end is not None and datetime.fromisoformat(rows[0][3]) <= previous_end:
            raise ValueError('Demo phases must be chronological')
        previous_end = datetime.fromisoformat(rows[-1][3])
        phases.append({'name':phase['name'], 'title':phase['title'], 'messages':rows})
    return {'version':data['version'], 'provenance':data['provenance'], 'account':names['me'],
            'anchor':anchor.isoformat(), 'contacts':{names[k]:v for k,v in data['contacts'].items()},
            'messages':phases[0]['messages'],
            'increment':[row for phase in phases[1:] for row in phase['messages']], 'phases':phases}
