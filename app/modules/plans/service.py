from app.modules.confirmations.service import overlaps, resolve
from app.modules.plans.aliases import aliases_from
from app.modules.plans.windows import PlanIndex, latest_visits, plan_versions


def plan_shelf(intake, confirmations=None):
    tables = intake['tables']
    alias_rows, aliases, alias_issues = aliases_from(tables['aliases'])
    plans, conflicting, plan_issues = plan_versions(tables['plans'], aliases)
    selected, replaced = latest_visits(intake['visits'])
    index = PlanIndex(plans, conflicting)
    confirmations = confirmations or {}
    decisions = []
    for slot in intake['slots']:
        visit = selected.get(slot['store'])
        result = (index.at(slot['category'], visit['at_utc']) if visit else
                  {'plan': None, 'reason': 'VISIT_MISSING', 'candidates': []})
        key = f"{slot['store']}:{slot['slot']}"
        resolved = resolve({'store': slot['store'], 'slot': slot['slot'], 'category': slot['category'],
                            'roster_source': slot['source'], 'visit': visit, **result}, confirmations.get(key))
        decisions.append(resolved)
    pending = [row for row in decisions if row['reason'] == 'PLAN_OVERLAP']
    confirmed = [row for row in decisions if row.get('confirmed')]
    stale = [row for row in decisions if row.get('confirmation', {}).get('stale')]
    return {'decisions': decisions, 'plans': plans, 'alias_rows': alias_rows, 'aliases': aliases,
            'replaced_visits': replaced, 'quarantine': alias_issues + plan_issues,
            'overlaps': overlaps(decisions),
            'overlap_summary': {'total': len(pending) + len(confirmed) + len(stale),
                                'confirmed': len(confirmed), 'unresolved': len(pending), 'stale': len(stale)}}
