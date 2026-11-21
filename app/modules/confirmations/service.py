from hashlib import sha256

from app.errors import AuditError
from app.manifest import canonical, now

FACTS = ('plan', 'category', 'start_utc', 'end_utc', 'sku', 'facings', 'price_cents')


def identity(dataset, key, plan, digest):
    """同一档件、柜位、陈列版本和内容恒得同一确认编号，重复提交不生成新结论。"""
    return sha256(canonical({'dataset': dataset, 'key': key, 'plan': plan, 'digest': digest}).encode()).hexdigest()


def plan_digest(plan):
    """只取陈列图事实，源行位置变化不改变已确认的版本内容。"""
    return sha256(canonical({field: plan[field] for field in FACTS}).encode()).hexdigest()


def candidates_of(decision):
    """已定版柜位把两份原件留在 overlap_candidates，未定版柜位仍在 candidates。"""
    return decision.get('overlap_candidates') or decision.get('candidates') or []


def applicable(record, pool):
    return any(row['plan'] == record['plan'] and plan_digest(row) == record['digest'] for row in pool)


def build_confirmation(decisions, existing, payload):
    """把一条确认精确落到单柜单版本；整店或整类不会被写成逐柜结论。"""
    if payload['scope'] != 'slot':
        raise AuditError('SCOPE_TOO_BROAD', '叠期确认只能逐柜登记，整店或整类会改写未签柜位', 422, payload['scope'])
    store, _, slot = payload['key'].partition(':')
    if not store or not slot:
        raise AuditError('SCOPE_TOO_BROAD', '叠期确认只能逐柜登记，整店或整类会改写未签柜位', 422, payload['scope'])
    decision = next((row for row in decisions if row['store'] == store and row['slot'] == slot), None)
    if decision is None:
        raise AuditError('SHELF_NOT_FOUND', '该柜位不在本期名册', 404, payload['key'])
    pool = candidates_of(decision)
    candidate = next((row for row in pool if row['plan'] == payload['plan']), None)
    prior = existing.get(payload['key'])
    if prior is not None and applicable(prior, pool):
        same = (prior['plan'] == payload['plan'] and prior['digest'] == plan_digest(candidate)
                and (prior['actor'], prior['note'], prior['evidence_ref'])
                == (payload['actor'], payload['note'], payload['evidence_ref']))
        if not same:
            raise AuditError('CONFIRMATION_CONFLICT', '该柜位已按其他版本或依据确认', 409, payload['key'])
        return prior, 'reused'
    if decision['reason'] != 'PLAN_OVERLAP':
        raise AuditError('NO_OVERLAP', '该柜位没有需要裁决的叠期陈列图', 409, decision['reason'])
    if candidate is None:
        raise AuditError('PLAN_NOT_CANDIDATE', '所选陈列图不是该柜位的叠期候选', 409,
                         [row['plan'] for row in pool])
    digest = plan_digest(candidate)
    return {'confirmation_id': identity(payload['dataset'], payload['key'], payload['plan'], digest),
            'key': payload['key'], 'plan': payload['plan'], 'digest': digest,
            'actor': payload['actor'], 'note': payload['note'], 'evidence_ref': payload['evidence_ref'],
            'created_at': now()}, 'new'


def resolve(decision, record):
    """命中确认时单柜定版，两份原件都留在依据里；确认与现状不符时原样待判。"""
    if record is None:
        return decision
    candidate = next((row for row in decision['candidates']
                      if row['plan'] == record['plan'] and plan_digest(row) == record['digest']), None)
    if decision['reason'] != 'PLAN_OVERLAP' or candidate is None:
        return {**decision, 'confirmation': {**record, 'applied': False, 'stale': True}}
    return {**decision, 'plan': candidate, 'reason': None, 'confirmed': record,
            'candidates': [], 'overlap_candidates': decision['candidates']}


def _basis(row):
    return {'plan': row['plan'], 'sku': row['sku'], 'facings': row['facings'], 'price_cents': row['price_cents'],
            'start_utc': row['start_utc'], 'end_utc': row['end_utc'], 'source': row['source']['id']}


def overlaps(decisions):
    """逐柜列出叠期柜位：已按确认定版、仍待判、确认已失效三类。"""
    rows = []
    for decision in decisions:
        confirmation = decision.get('confirmed') or decision.get('confirmation')
        unresolved = decision['reason'] == 'PLAN_OVERLAP'
        if not unresolved and confirmation is None:
            continue
        status = 'CONFIRMED' if decision.get('confirmed') else ('STALE' if confirmation else 'UNRESOLVED')
        rows.append({'key': f"{decision['store']}:{decision['slot']}", 'store': decision['store'],
                     'slot': decision['slot'], 'category': decision['category'], 'status': status,
                     'plan': decision['plan']['plan'] if decision.get('plan') else None,
                     'confirmation': confirmation,
                     'candidates': [_basis(row) for row in candidates_of(decision)]})
    return rows
