import json
from datetime import datetime, timezone
from hashlib import sha256

from app.config import RULE_VERSION


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def confirmation_basis(confirmations):
    """参与运行键的确认只留事实字段，并按柜位排序，重排登记顺序不产生新批次。"""
    return [{'key': row['key'], 'plan': row['plan'], 'confirmation_id': row['confirmation_id'], 'digest': row['digest']}
            for row in sorted(confirmations.values(), key=lambda item: (item['key'], item['plan']))]


def manifest(audit):
    parameters = {'dataset': audit.options.dataset, 'sample_size': audit.sample_size}
    key = {'parameters': parameters, 'rule_version': RULE_VERSION,
           'inputs': {name: sha256(raw).hexdigest() for name, raw in audit.bundle.items()},
           'confirmations': confirmation_basis(audit.confirmations)}
    return {**key, 'run_id': sha256(canonical(key).encode()).hexdigest(), 'created_at': now(),
            'version': audit.intake['campaign']['version']}
