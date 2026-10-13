"""Preserve local message windows without inferring topics or reply relations."""


def merge_evidence(existing, incoming):
    rows = {s['evidence_id']: dict(s) for s in existing}
    for source in incoming:
        key = source['evidence_id']
        old = rows.get(key, {})
        merged = {**old, **source}
        if old.get('evidence_role') == 'primary':
            merged['evidence_role'] = 'primary'
        for field in ('context_for', 'context_ids'):
            values = list(dict.fromkeys([*old.get(field, []), *source.get(field, [])]))
            if values:
                merged[field] = values
        rows[key] = merged
    return list(rows.values())


def select_evidence(sources, selected_ids=None, limit=None, char_limit=None):
    """Select whole direct windows, never recursively merge adjacent topics.

    References remain individual messages. Windows with missing dependencies
    are omitted and reported, including context removed by an earlier budget.
    """
    by_id = {s['evidence_id']: s for s in sources}
    requested = selected_ids if selected_ids is not None else list(by_id)
    chosen, seen, omitted = [], set(), False
    characters = 0
    for key in requested:
        if key not in by_id:
            omitted = True
            continue
        source = by_id[key]
        # Selecting a neighbour also keeps its anchors. Expand those anchors
        # once, not neighbours-of-neighbours across an entire busy group.
        anchors = [key] if source.get('context_ids') else source.get('context_for', []) or [key]
        # Legacy explicit surrounding results carry the link on neighbours.
        direct = [s['evidence_id'] for s in sources if key in s.get('context_for', [])]
        members = list(dict.fromkeys([key, *anchors, *[
            child for anchor in anchors for child in by_id.get(anchor, {}).get('context_ids', [])], *direct]))
        missing = any(m not in by_id for m in members)
        new = [m for m in members if m in by_id and m not in seen]
        cost = sum(len(str(by_id[m].get('text', ''))) for m in new)
        if (missing or (limit is not None and len(chosen)+len(new) > limit) or
                (char_limit is not None and characters+cost > char_limit)):
            omitted = True
            continue
        for member in new:
            item = dict(by_id[member])
            chosen.append(item)
            seen.add(member)
        characters += cost
    return chosen, omitted


def context_windows(sources):
    """A chronological view using the same frozen citation numbers as sources."""
    refs = {s['evidence_id']: i+1 for i, s in enumerate(sources) if s.get('evidence_id')}
    rows = {s['evidence_id']: s for s in sources if s.get('evidence_id')}
    windows = []
    for source in sources:
        if not source.get('context_ids') or not source.get('evidence_id'):
            continue
        ids = [source['evidence_id'], *source['context_ids']]
        present = [rows[k] for k in dict.fromkeys(ids) if k in rows]
        present.sort(key=lambda s: (s.get('timestamp') or 0, s.get('index_row_id') or 0))
        windows.append({'anchor_ref': refs[source['evidence_id']],
                        'chat_id': source.get('chat_id'),
                        'ordered_refs': [refs[s['evidence_id']] for s in present],
                        'incomplete': any(k not in refs for k in ids) or bool(source.get('context_incomplete'))})
    return windows


def ordered_source_groups(sources):
    """Render each chat in time order while preserving frozen source numbers.

    A long pause starts a new display block, not a claimed topic boundary.
    Relevance order across individual short messages is not reading order.
    """
    chats = {}
    for ref, source in enumerate(sources, 1):
        key = source.get('chat_id') or ('summary', ref)
        chats.setdefault(key, []).append((ref, source))
    blocks = []
    for rows in chats.values():
        rows.sort(key=lambda pair: (pair[1].get('timestamp') or 0, pair[1].get('index_row_id') or 0))
        block = []
        previous = None
        for pair in rows:
            stamp = pair[1].get('timestamp') or 0
            if previous is not None and stamp-previous > 1800:
                blocks.append(block)
                block = []
            block.append(pair)
            previous = stamp
        if block:
            blocks.append(block)
    return blocks
