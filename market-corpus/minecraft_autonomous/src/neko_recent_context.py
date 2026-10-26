"""Public same-character snapshots; canonical code owns selection and storage."""

import asyncio
import hashlib
import inspect
from collections.abc import Mapping
from datetime import datetime
from itertools import islice

from agent_mc_autonomy import host_recent
from agent_mc_autonomy.death_memory import scope_key
from agent_mc_autonomy.host_context import HostContextPort
from agent_mc_autonomy.mailbox import observation
from agent_mc_autonomy.ownership import session_is_owned
from plugin.sdk.plugin import Err, Ok, TransportError

from .neko_commentary import load_commentary
from .neko_host_context import HostContextSettings, character_profile


async def bounded_read(operation, valid):
    """Own and drain the wrapper, including cancellation on stop/config change.

    The genuine SDK executor worker can finish its bounded RPC after cancellation.
    This wait belongs to the existing observer, never an independent refresh loop.
    """
    task = asyncio.create_task(operation)
    try:
        async with asyncio.timeout(0.5):
            while valid():
                done, _ = await asyncio.wait({task}, timeout=0.05)
                if done:
                    return task.result()
            raise ValueError('binding_changed')
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def owned_runtime(plugin, settings, target, port, generation):
    """Validate authoritative status independently of optional actor leases."""
    from .neko_heartbeat import is_stopped
    try:
        if (not settings.enabled or not target or plugin._generation != generation
                or plugin._configuration_writes or is_stopped(plugin) or port.stopping()
                or not session_is_owned(port.directory)):
            return None
        snapshot = observation(port.directory)
        age = port.now() - datetime.fromisoformat(snapshot['updated_at']).timestamp()
        if (not 0 <= age <= 30 or snapshot.get('phase') != 'running'
                or snapshot.get('game_connected') is not True
                or snapshot.get('settings_identity') != settings.identity):
            return None
        value = {'identity': settings.identity, 'actor': target,
                 'session': snapshot.get('runtime_session'), 'run': snapshot.get('native_run'),
                 'scope': snapshot.get('game_identity'), 'intent_revision': port.mailbox.intent_revision(),
                 'death_episode_id': snapshot.get('body_death_episode_id')}
        if settings.context_mode == 'full':
            value['context_mode'] = 'full'
        host_recent.binding(value)
        return value
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return None


def current_request(plugin, settings, target, port, generation):
    owned = owned_runtime(plugin, settings, target, port, generation)
    try:
        request = observation(port.directory).get('host_recent_request')
        if host_recent.binding(request, request=True) != owned or not 0 <= port.now() - request['as_of'] <= 30:
            return None
        base = HostContextPort(port.directory, now=port.now).read(request['intent_revision'])
        if not base or base['stable']['agent_name'] != target:
            return None
        return request
    except (OSError, ValueError, TypeError, KeyError):
        return None


async def refresh_profile(plugin, raw, settings, target, port, generation):
    config = HostContextSettings.parse(raw)
    owned = owned_runtime(plugin, settings, target, port, generation)
    if not config.enabled or not config.character_api_url or owned is None:
        return
    key = (generation, config.character_api_url, host_recent.encoded({k: owned[k] for k in ('identity', 'actor', 'session', 'run', 'scope')}))
    profile = load_commentary(port.directory / 'host-character.json')
    if (profile.get('scope') == scope_key(owned['scope'])
            and profile.get('stable', {}).get('agent_name') == target
            and isinstance(profile.get('expires_at'), (int, float))
            and profile.get('issued_at', float('inf')) <= port.now() < profile['expires_at'] - 120):
        return
    previous = getattr(plugin, '_recent_profile_attempt', None)
    if previous and previous[0] == key and 0 <= port.now() - previous[1] < 60:
        return
    plugin._recent_profile_attempt = (key, port.now())
    try:
        fresh = await bounded_read(character_profile(plugin, config, target, fresh=True, timeout=0.35),
                                   lambda: owned_runtime(plugin, settings, target, port, generation) == owned)
    except (TimeoutError, OSError, ValueError):
        return
    with plugin._control_lock:
        if fresh and owned_runtime(plugin, settings, target, port, generation) == owned:
            HostContextPort(port.directory, now=port.now).publish_static(fresh, scope=scope_key(owned['scope']))


def normalize_record(value, actor, cutoff, sampled, *, full=False):
    # SDK's public dump preserves normalized fields. Mapping records are kept
    # intact: from_raw would confuse delivery id with metadata.conversation_id.
    if not isinstance(value, Mapping):
        value = value.dump() if callable(getattr(value, 'dump', None)) else None
    if not isinstance(value, Mapping) or not isinstance(value.get('metadata'), dict):
        return None
    meta = value['metadata']
    def field(name):
        top, nested = value.get(name), meta.get(name)
        if top is not None and nested is not None and top != nested:
            raise ValueError('conflicting_provenance')
        return top if top is not None else nested
    try:
        source, turn, target = field('source'), field('turn_type'), field('lanlan_name')
        role = field('role')
        if target != actor:
            return None
        count = field('message_count')
        if source == 'main_logic.core' and type(count) is int and count == 1:
            if turn == 'user_message' and role == 'master':
                normalized_role = 'user'
            elif turn in {'assistant_message', 'proactive_reply'} and role == 'cat':
                normalized_role = 'assistant'
            else:
                return None
        elif source == 'proactive' and turn == 'proactive_reply' and role in {None, 'cat'}:
            normalized_role = 'assistant'
        else:
            return None
        observed = host_recent.timestamp(value.get('timestamp'))
        occurred = host_recent.timestamp(meta['ts']) if 'ts' in meta else observed
        if not cutoff <= occurred <= observed <= sampled:
            return None
        content = value.get('content')
        if not isinstance(content, str) or not content.strip() or '\x00' in content:
            return None
        digest = hashlib.sha256(content.encode()).hexdigest()
        identity = hashlib.sha256(host_recent.encoded([actor, source, turn, normalized_role, occurred, digest]).encode()).hexdigest()
        # Composite identity deliberately survives SDK loss of raw delivery id.
        conversation = field('conversation_id')
        if conversation is not None:
            host_recent.text(conversation)
        clipped = host_recent.clip(content, host_recent.FULL_STORAGE_BYTES if full else 1600)
        return {'id': identity, 'derived_id': True, 'conversation_id': conversation,
                'actor': actor, 'role': normalized_role, 'source': source, 'turn_type': turn,
                'observed_at': observed, 'occurred_at': occurred,
                'clock_source': 'producer' if 'ts' in meta else 'arrival',
                'text': clipped, 'text_sha256': digest, 'truncated': clipped != content,
                'original_ref': {'source_id': identity, 'full_text_available': clipped == content}}
    except (ValueError, TypeError, OverflowError):
        return None


def public_records(result):
    if isinstance(result, Ok):
        result = result.value
    if isinstance(result, Err):
        raise ValueError('query_error')
    if isinstance(result, Mapping):
        if 'metadata' not in result or 'content' not in result:
            raise ValueError('unknown_result_shape')
        return [result]
    if isinstance(result, (str, bytes)):
        raise ValueError('unknown_result_shape')
    return list(islice(iter(result), 60))


def speech_records(port, binding):
    rows = []
    for receipt in load_commentary(port.directory / 'speech-output.json').get('records', {}).values():
        bound = receipt.get('runtime_binding')
        if not isinstance(bound, dict) or any(bound.get(k) != binding[k] for k in ('identity', 'actor', 'session', 'run', 'scope')):
            continue
        state = receipt.get('status')
        if state not in {'accepted', 'queued', 'reserved_unknown', 'unknown', 'rejected'}:
            continue
        offered = receipt.get('offered_at')
        if type(offered) not in {int, float} or not port.now() - 300 <= offered <= port.now():
            continue
        rows.append({**{k: bound[k] for k in ('actor', 'session', 'run', 'scope')},
                     'source': 'neko_owned_explicit_output', 'opportunity_id': receipt['opportunity_id'],
                     'decision_id': receipt['decision_id'], 'speech_id': receipt.get('speech_id'),
                     'offered_at': offered, 'receipt_state': 'accepted' if state in {'accepted', 'queued'} else 'rejected' if state == 'rejected' else 'unknown',
                     'text_sha256': receipt['text_sha256'], 'text_available': False, 'playback_verified': False})
    return sorted(rows, key=lambda r: r['offered_at'])[-(60 if binding.get('context_mode') == 'full' else 3):]


async def refresh_recent(plugin, raw, settings, target, port, generation):
    if not HostContextSettings.parse(raw).enabled:
        return host_recent.unavailable('disabled')
    await refresh_profile(plugin, raw, settings, target, port, generation)
    request = current_request(plugin, settings, target, port, generation)
    if request is None:
        return host_recent.unavailable('binding_unavailable')
    binding = host_recent.binding(request, request=True)
    key = (generation, host_recent.encoded(request))
    previous = getattr(plugin, '_recent_attempt', None)
    if previous and previous[0] == key and 0 <= port.now() - previous[1] < 5:
        return host_recent.diagnostics(HostContextPort(port.directory, now=port.now).read_recent(request), port.now())
    plugin._recent_attempt = (key, port.now())
    floor_key = (generation, host_recent.encoded({k: binding[k] for k in ('identity', 'actor', 'session', 'run', 'scope')}))
    floor = getattr(plugin, '_recent_session_floor', None)
    if floor is None or floor[0] != floor_key:
        floor = (floor_key, host_recent.timestamp(port.now()))
        plugin._recent_session_floor = floor
    cutoff = port.now() - 600 if binding.get('context_mode') == 'full' else max(floor[1], port.now() - 600)
    async def query():
        result = plugin.bus.conversations.get(max_count=60, since_ts=cutoff, timeout=0.35)
        return await result if inspect.isawaitable(result) else result
    def valid():
        current = current_request(plugin, settings, target, port, generation)
        return current == request
    try:
        result = await bounded_read(query(), valid)
        rows = public_records(result)
    except (TimeoutError, ValueError, TypeError, AttributeError, OSError, TransportError):
        return host_recent.unavailable('query_unavailable')
    with plugin._control_lock:
        current = current_request(plugin, settings, target, port, generation)
        if current != request:
            return host_recent.unavailable('binding_changed')
        sampled = port.now()
        candidates = {}
        for raw_row in rows:
            row = normalize_record(raw_row, target, max(cutoff, sampled - 600), sampled, full=binding.get('context_mode') == 'full')
            if row:
                candidates[(row['source'], row['id'])] = row
        selected = host_recent.select_recent_messages(list(candidates.values()), binding=binding, sampled_at=sampled, source='neko_public_conversations')
        speech = speech_records(port, binding)
        received, count = len(candidates) + len(speech), len(selected) + len(speech)
        projection = {'schema': host_recent.SCHEMA, 'binding': binding, 'source': 'neko_public_conversations',
                      'sampled_at': sampled, 'expires_at': sampled + 30,
                      'coverage': {'state': 'partial' if count else 'unavailable', 'received_count': received,
                                   'limit': max(63, received), 'saturated': len(rows) == 60, 'selected_count': count,
                                   'omitted_count': received - count, 'arrival_cursor': cutoff,
                                   'reasons': [('public_recent_window' if binding.get('context_mode') == 'full' else 'session_observed_from'), 'best_effort_publication'] if rows else ['empty_snapshot', 'coverage_unknown']},
                      'recent_messages': selected, 'recent_speech': speech}
        return HostContextPort(port.directory, now=port.now).publish_recent(projection, binding=current)


def historical_hint(plugin, settings, target, port, generation):
    request = current_request(plugin, settings, target, port, generation)
    value = HostContextPort(port.directory, now=port.now).read_recent(request) if request else {}
    result, seen = [], set()
    for row in reversed(value.get('recent_messages', [])):
        if row['role'] != 'assistant' or row['text_sha256'] in seen:
            continue
        seen.add(row['text_sha256'])
        # A prefix-only excerpt of the host's concatenated tool-round reply
        # retained nothing except repeated inspection preludes. Keep the tail
        # for topic continuity; originals and their hash stay unchanged.
        encoded = row['text'].encode('utf-8')
        clipped = len(encoded) > 70
        excerpt = '…' + encoded[-67:].decode('utf-8', errors='ignore') if clipped else row['text']
        result.append({'text': excerpt, 'source': row['source'],
                       'at': row['occurred_at'], 'sha256': row['text_sha256'],
                       'excerpt': 'tail' if clipped else 'complete'})
        if len(result) == 2:
            break
    hint = {'historical_only': True, 'audio_verified': False, 'purpose': 'avoid_repetition',
            'instruction': '仅供防复读和衔接；不是口播模板，不要复述片段。', 'utterances': list(reversed(result))}
    while host_recent.size(hint) > 600 and hint['utterances']:
        hint['utterances'].pop(0)
    return hint
