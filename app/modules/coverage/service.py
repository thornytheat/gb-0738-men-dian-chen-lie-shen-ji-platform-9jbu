from app.modules.coverage.sampling import choose_slots

PLAN_BLOCKS = ('VISIT_MISSING', 'PLAN_MISSING', 'PLAN_OVERLAP')
EVIDENCE_BLOCKS = ('PHOTO_ID_CONFLICT', 'PHOTO_REUSED', 'OBSERVATION_CONFLICT', 'BAD_OBSERVATION')


def coverage(intake, plans, photos, sample_size):
    chosen, strata = choose_slots(intake, sample_size)
    plan_index = {(row['store'], row['slot']): row for row in plans['decisions']}
    bindings = {(row['visit'], row['slot']): row for row in photos['bindings']}
    slots, counted = [], {row['code']: {'verified': 0, 'pending': 0, 'isolated': 0} for row in strata}
    for candidate in chosen:
        decision = plan_index[candidate['store'], candidate['slot']]
        visit = decision['visit']
        binding = bindings.get((visit['visit'], candidate['slot'])) if visit else None
        if decision['reason'] in PLAN_BLOCKS:
            status, reason = 'ISOLATED', decision['reason']
        elif binding is None:
            status, reason = 'PENDING', 'MISSING_PHOTO'
        elif binding['reason'] in EVIDENCE_BLOCKS:
            status, reason = 'ISOLATED', binding['reason']
        elif binding['reason']:
            status, reason = 'PENDING', binding['reason']
        else:
            status, reason = 'VERIFIED', None
        counted[candidate['group']][status.lower()] += 1
        stale = (decision.get('confirmation') or {})
        slots.append({**candidate, 'visit': visit, 'plan': decision['plan'], 'evidence': binding,
                      'plan_candidates': decision.get('overlap_candidates') or decision['candidates'],
                      'evidence_status': status, 'reason': reason,
                      'confirmed': decision.get('confirmed'),
                      'confirmation_stale': stale if stale.get('stale') else None})
    for row in strata:
        tally = counted[row['code']]
        row.update(verified=tally['verified'], pending=tally['pending'], isolated=tally['isolated'],
                   gap=row['target'] - tally['verified'])
    selected = len(slots)
    verified = sum(row['verified'] for row in strata)
    return {'target': sample_size, 'selected': selected, 'unfilled': sample_size - selected,
            'verified': verified, 'pending': sum(row['pending'] for row in strata),
            'isolated': sum(row['isolated'] for row in strata), 'gap': sample_size - verified,
            'strata': strata, 'slots': slots}
