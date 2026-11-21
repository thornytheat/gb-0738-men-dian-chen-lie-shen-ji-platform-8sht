from app.errors import AuditError
from app.manifest import now


def next_confirmation(payload, form):
    if payload['status'] != 'DRAFT':
        raise AuditError('ATTRIBUTION_NOT_DRAFT', '归属批次已发布，不能重复确认', 409, payload['status'])
    expected = payload.get('version', 0)
    if form['expected_version'] != expected:
        raise AuditError('STALE_ATTRIBUTION', '归属版本已变化，请刷新', 409, expected)
    return {'version': 1, 'request_id': form['request_id'], 'action': 'CONFIRM',
            'actor': form['actor'], 'note': form['note'], 'evidence_ref': form['evidence_ref'],
            'created_at': now(), 'entries_total': payload['totals']['entries_total'],
            'fine_cents': payload['totals']['fine_cents']}
