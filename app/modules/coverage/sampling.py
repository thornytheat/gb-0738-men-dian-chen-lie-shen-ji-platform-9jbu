from collections import defaultdict
from hashlib import sha256


def apportion(groups, total):
    """按权重以最大余数分配目标柜位，余数并列时按店组编号升序。"""
    weighted = [(group['code'], group['weight']) for group in groups]
    denominator = sum(weight for _, weight in weighted)
    quotas = {code: total * weight // denominator for code, weight in weighted}
    seats = total - sum(quotas.values())
    order = sorted(weighted, key=lambda item: (-(total * item[1] % denominator), item[0]))
    for code, _ in order[:seats]:
        quotas[code] += 1
    return quotas


def rank_slots(slots, stores, seed):
    """按 SHA-256(种子 NUL 门店 NUL 柜位) 排序；并列用柜位键。"""
    ranked = defaultdict(list)
    for slot in slots:
        store = stores.get(slot['store'])
        if store is None:
            continue
        score = sha256(f"{seed}\0{slot['store']}\0{slot['slot']}".encode()).hexdigest()
        ranked[store['group']].append({'key': f"{slot['store']}:{slot['slot']}", 'store': slot['store'],
                                       'slot': slot['slot'], 'category': slot['category'],
                                       'group': store['group'], 'source': slot['source'], 'score': score})
    return {group: sorted(rows, key=lambda row: (row['score'], row['key'])) for group, rows in ranked.items()}


def choose_slots(intake, total):
    campaign = intake['campaign']
    stores = {row['store']: row for row in intake['stores']}
    ranked = rank_slots(intake['slots'], stores, campaign['seed'])
    quotas = apportion(campaign['groups'], total)
    rows, strata = [], []
    for group in campaign['groups']:
        pool = ranked.get(group['code'], [])
        quota = quotas[group['code']]
        chosen = pool[:quota]
        rows.extend(chosen)
        strata.append({'code': group['code'], 'title': group['title'], 'weight': group['weight'],
                       'target': quota, 'pool': len(pool), 'selected': len(chosen),
                       'unfilled': quota - len(chosen), 'ranked_candidates': pool})
    return rows, strata
