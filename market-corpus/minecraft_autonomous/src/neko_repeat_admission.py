"""Conservative transport provenance for explicit-output admission only."""

from dataclasses import dataclass

from agent_mc_autonomy import host_recent
from agent_mc_autonomy.commentary import commentary_observation
from agent_mc_autonomy.mailbox import observation
from agent_mc_autonomy.review import review_observation

from .neko_recent_context import owned_runtime


def correlation_key(review):
    return tuple(review.get(k) for k in ('id', 'session', 'scope', 'revision', 'baseline')) + (review.get('opportunity', {}).get('id', review.get('id')),)


def record_origin(plugin, settings, target, port, generation, review, origin, note=None):
    """Metadata only. Manual/unknown delivery of the same opportunity is sticky."""
    rows = getattr(plugin, '_review_origins', {})
    key = review.get('id')
    if not key:
        return
    previous = rows.get(key)
    bound = owned_runtime(plugin, settings, target, port, generation)
    item = {'key': correlation_key(review), 'generation': generation,
            'binding': host_recent.encoded(bound) if bound else None,
            'origin': origin, 'urgent': note.get('urgent') if note else None}
    if previous:
        if previous['key'] != item['key'] or previous['binding'] != item['binding'] or previous['generation'] != generation:
            item['origin'] = 'unknown'
        elif previous['origin'] != origin:
            item['origin'] = 'mixed'
        if previous['urgent'] is True:
            item['urgent'] = True
        elif note is None:
            item['urgent'] = previous['urgent']
    rows[key] = item
    plugin._review_origins = dict(list(rows.items())[-32:])


@dataclass(frozen=True)
class Admission:
    key: tuple
    binding_json: str
    ordinary: bool

    def matches(self, correlation):
        key = tuple(correlation.get(k) for k in ('review_id', 'session', 'scope', 'revision', 'baseline', 'opportunity_id'))
        return key == self.key


def classification(plugin, settings, target, port, generation):
    review = port.state().get('review', {})
    bound = owned_runtime(plugin, settings, target, port, generation)
    if not bound:
        return None
    key = correlation_key(review)
    current = review_observation(observation(port.directory), port.now())
    marker = getattr(plugin, '_review_origins', {}).get(review.get('id'))
    from .neko_commentary import commentary_plan
    snapshot = observation(port.directory)
    event = commentary_observation(snapshot, port.now())
    note = None
    if event:
        event['payload']['opportunity'] = review.get('opportunity', {})
        if review.get('evidence', {}).get('death_episode'):
            event['payload']['death_episode'] = review['evidence']['death_episode']
        # Canonical typed commentary policy, evaluated against fresh status.
        _, _, note = commentary_plan(plugin, settings, target, event, port.now(), 60)
    ordinary = bool(marker and marker['key'] == key and marker['generation'] == generation
                    and marker['binding'] == host_recent.encoded(bound) and marker['origin'] == 'automatic'
                    and marker['urgent'] is False and not review.get('urgent')
                    and not review.get('evidence', {}).get('death_episode')
                    and note and note['urgent'] is False and current and not current['urgent']
                    and all(current[k] == review.get(k) for k in ('session', 'scope', 'baseline', 'intent')))
    evidence = review.get('evidence', {})
    # Public lineage has no submission time. A newly observed user id gets a
    # conservative 300s bypass, never inferred from wording or origin alone.
    user = evidence.get('current_user_goal') or {}
    active = evidence.get('current_goal') or {}
    if active.get('id') and active.get('origin') == 'unknown':
        ordinary = False
    if user.get('id'):
        user_key = (generation, bound['session'], bound['run'], user['id'])
        previous = getattr(plugin, '_speech_user_observed', None)
        if previous is None or previous[0] != user_key:
            previous = (user_key, port.now())
            plugin._speech_user_observed = previous
        if not 300 < port.now() - previous[1]:
            ordinary = False
    return Admission(key, host_recent.encoded(bound), ordinary)
