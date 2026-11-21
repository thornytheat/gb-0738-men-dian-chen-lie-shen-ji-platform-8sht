from app.modules.coverage.sampling import choose_slots

VERIFIED = 'VERIFIED'


def _plan_decision(plans, store, slot):
    return next(row for row in plans['decisions'] if row['store'] == store and row['slot'] == slot)


def _evidence_state(decision, binding, visit):
    if decision['reason'] is not None:
        return 'ISOLATED', decision['reason']
    if binding is None:
        return ('PENDING', 'VISIT_MISSING') if visit is None else ('PENDING', 'MISSING_PHOTO')
    if binding['reason'] is not None:
        return ('ISOLATED', binding['reason']) if binding['reason'] in (
            'OBSERVATION_CONFLICT', 'PHOTO_ID_CONFLICT', 'PHOTO_REUSED', 'BAD_OBSERVATION') \
            else ('PENDING', binding['reason'])
    return VERIFIED, None


def coverage(intake, plans, photos, target):
    slots, quota_rows = choose_slots(intake, target)
    decisions = {(row['store'], row['slot']): row for row in plans['decisions']}
    bindings = {(row['visit'], row['slot']): row for row in photos['bindings']}
    selected_visits = {row['store']: row for row in intake['visits']}
    rows = []
    for slot in slots:
        store, shelf = slot['store'], slot['slot']
        decision = decisions[(store, shelf)]
        visit = selected_visits.get(store)
        binding = bindings.get((visit['visit'], shelf)) if visit else None
        status, reason = _evidence_state(decision, binding, visit)
        rows.append({'key': f'{store}:{shelf}', 'store': store, 'slot': shelf,
                     'group': next(item['group'] for item in intake['stores'] if item['store'] == store),
                     'category': slot['category'], 'visit': visit['visit'] if visit else None,
                     'evidence_status': status, 'reason': reason,
                     'plan': decision['plan'], 'plan_candidates': decision['candidates'],
                     'evidence': None if binding is None else {
                         'photos': binding['photos'], 'products': binding['products'],
                         'completed_sources': binding['completed_sources'],
                         'conflicting_skus': binding['conflicting_skus']},
                     'source': slot['source'],
                     'score': next(item['score'] for item in quota_rows
                                   for item in item['ranked_candidates']
                                   if item['store'] == store and item['slot'] == shelf)})
    groups = intake['campaign']['groups']
    strata = []
    for quota in quota_rows:
        group = next(item for item in groups if item['code'] == quota['code'])
        members = [row for row in rows if row['group'] == quota['code']]
        verified = sum(row['evidence_status'] == VERIFIED for row in members)
        pending = sum(row['evidence_status'] == 'PENDING' for row in members)
        isolated = sum(row['evidence_status'] == 'ISOLATED' for row in members)
        strata.append({**quota, 'title': group['title'], 'weight': group['weight'],
                       'verified': verified, 'pending': pending, 'isolated': isolated,
                       'gap': quota['unfilled'] + pending + isolated,
                       'ranked_candidates': quota['ranked_candidates']})
    selected = len(rows)
    verified_total = sum(row['evidence_status'] == VERIFIED for row in rows)
    pending_total = sum(row['evidence_status'] == 'PENDING' for row in rows)
    isolated_total = sum(row['evidence_status'] == 'ISOLATED' for row in rows)
    return {'target': target, 'selected': selected, 'unfilled': target - selected,
            'verified': verified_total, 'pending': pending_total, 'isolated': isolated_total,
            'slots': rows, 'strata': strata}
