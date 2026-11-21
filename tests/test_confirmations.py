import shutil
import sqlite3

import pytest

from app.config import Options
from app.errors import AuditError
from app.modules.settlement.service import run_audit
from app.store import Store


def payload(**changes):
    return {'dataset': 'field', 'key': '002:A04', 'plan': 'HOL-802', 'scope': 'slot',
            'request_id': 'CONF-202611-01', 'actor': '运营甲', 'note': '节日图优先', 'evidence_ref': '确认单-21',
            **changes}


def confirm(client, **changes):
    return client.post('/api/confirmations', json=payload(**changes))


def second_overlap(settings, tmp_path):
    """本期在 001 店当天再加一版常规图，让另外几个已抽中柜位也进入待判。"""
    duplicate = tmp_path / 'data-two'
    shutil.copytree(settings.sample_root, duplicate)
    path = duplicate / 'field' / 'plans.csv'
    path.write_bytes(path.read_bytes() + b'TEMP-777,SPIRIT,2026-11-07T00:00:00Z,2026-11-08T00:00:00Z,GR-209,4,13800\r\n')
    settings.sample_root = duplicate
    return duplicate


def test_unconfirmed_overlap_keeps_both_plan_versions_for_judgement(field):
    row = next(item for item in field.coverage['slots'] if item['key'] == '002:A04')
    assert (row['evidence_status'], row['reason'], row['plan']) == ('ISOLATED', 'PLAN_OVERLAP', None)
    assert [item['plan'] for item in row['plan_candidates']] == ['HOL-801', 'HOL-802']
    listed = next(item for item in field.plans['overlaps'] if item['key'] == '002:A04')
    assert listed['status'] == 'UNRESOLVED' and listed['plan'] is None
    assert [item['source'] for item in listed['candidates']] == ['plans.csv:4', 'plans.csv:5']


def test_confirmed_scope_forms_conclusion_and_leaves_the_rest_pending(client, settings, tmp_path):
    second_overlap(settings, tmp_path)
    open_slots = [row['key'] for row in client.get('/api/overlaps').json()['overlaps']]
    assert sorted(open_slots) == ['001:A01', '001:A02', '001:A03', '001:A04', '002:A04']
    assert confirm(client).status_code == 200
    result = client.post('/api/settlement/runs', json={}).json()
    rows = {row['key']: row for row in result['penalties']['rows']}
    assert (rows['002:A04']['status'], rows['002:A04']['plan']['plan']) == ('VIOLATION', 'HOL-802')
    assert rows['002:A04']['original_cents'] == 5500
    assert (rows['001:A01']['status'], rows['001:A01']['reason']) == ('ISOLATED', 'PLAN_OVERLAP')
    assert [item['plan'] for item in rows['001:A01']['plan_candidates']] == ['OLD-210', 'TEMP-777']
    overlaps = {row['key']: row for row in result['plans']['overlaps']}
    assert overlaps['002:A04']['status'] == 'CONFIRMED'
    assert [item['plan'] for item in overlaps['002:A04']['candidates']] == ['HOL-801', 'HOL-802']
    assert overlaps['001:A01']['status'] == 'UNRESOLVED' and overlaps['001:A01']['plan'] is None
    assert result['plans']['overlap_summary'] == {'total': 5, 'confirmed': 1, 'unresolved': 4, 'stale': 0}


def test_confirmation_cannot_be_widened_to_store_or_category(client):
    store_scope = client.post('/api/confirmations', json=payload(scope='store', key='002:A04'))
    assert (store_scope.status_code, store_scope.json()['detail']['code']) == (422, 'SCOPE_TOO_BROAD')
    not_overlap = client.post('/api/confirmations', json=payload(key='002:A03', plan='NEW-212'))
    assert (not_overlap.status_code, not_overlap.json()['detail']['code']) == (409, 'NO_OVERLAP')
    absent = client.post('/api/confirmations', json=payload(key='002:A09'))
    assert (absent.status_code, absent.json()['detail']['code']) == (404, 'SHELF_NOT_FOUND')
    outsider = client.post('/api/confirmations', json=payload(plan='OLD-210'))
    assert (outsider.status_code, outsider.json()['detail']['code']) == (409, 'PLAN_NOT_CANDIDATE')


def test_same_confirmation_rerun_does_not_generate_a_second_result(client):
    initial = client.post('/api/settlement/runs', json={}).json()
    assert confirm(client).json()['reused'] is False
    confirmed = client.post('/api/settlement/runs', json={}).json()
    assert confirmed['manifest']['run_id'] != initial['manifest']['run_id']
    replay = confirm(client)
    assert replay.json()['reused'] is True
    same_batch = client.post('/api/settlement/runs', json={}).json()
    assert same_batch['reused'] is True and same_batch['manifest']['run_id'] == confirmed['manifest']['run_id']
    assert len(client.get('/api/settlement/batches').json()['batches']) == 2


def test_changed_confirmation_basis_and_other_plan_are_refused(client):
    assert confirm(client).status_code == 200
    other = confirm(client, request_id='CONF-202611-02', plan='HOL-801')
    assert (other.status_code, other.json()['detail']['code']) == (409, 'CONFIRMATION_CONFLICT')
    changed_basis = confirm(client, request_id='CONF-202611-03', evidence_ref='另一张单')
    assert (changed_basis.status_code, changed_basis.json()['detail']['code']) == (409, 'CONFIRMATION_CONFLICT')
    still = client.post('/api/settlement/runs', json={}).json()
    assert [row['plan'] for row in still['plans']['overlaps'] if row['key'] == '002:A04'] == ['HOL-802']


def test_failed_generation_keeps_previous_batch_and_starts_a_new_one_after_repair(client, settings, run):
    old_id = run['manifest']['run_id']
    source = settings.sample_root / 'field' / 'observations.csv'
    baseline = source.read_bytes()
    source.write_bytes(baseline + b'V001,A01,"cut')
    try:
        failed = client.post('/api/settlement/runs', json={})
        assert failed.status_code == 422 and failed.json()['detail']['code'] == 'MALFORMED_CSV'
        with sqlite3.connect(settings.database) as connection:
            assert connection.execute('SELECT COUNT(*) FROM runs').fetchone() == (1,)
    finally:
        source.write_bytes(baseline)
    assert confirm(client).status_code == 200
    repaired = client.post('/api/settlement/runs', json={}).json()
    assert repaired['reused'] is False and repaired['manifest']['run_id'] != old_id
    assert client.get(f'/api/settlement/runs/{old_id}').json()['settlement']['original_cents'] == 29500
    assert repaired['settlement']['original_cents'] == 35000
    batches = [row['run_id'] for row in client.get('/api/settlement/batches').json()['batches']]
    assert batches == [repaired['manifest']['run_id'], old_id]


def test_confirmation_stops_applying_when_plan_facts_change(settings, tmp_path):
    source = tmp_path / 'data-stale'
    shutil.copytree(settings.sample_root, source)
    settings.sample_root = source
    store = Store(settings.database)
    store.initialize()
    store.save_confirmation(payload(), _builder(settings))
    source = source / 'field' / 'plans.csv'
    baseline = source.read_bytes()
    source.write_bytes(baseline.replace(b'HOL-802,HOLIDAY,2026-11-06T00:00:00Z,2026-11-15T00:00:00Z,GR-211,4,15800',
                                        b'HOL-802,HOLIDAY,2026-11-06T00:00:00Z,2026-11-15T00:00:00Z,GR-211,5,15800'))
    try:
        snapshot, reusable = run_audit(settings, store, Options())
        assert reusable is False
        listed = next(row for row in snapshot['plans']['overlaps'] if row['key'] == '002:A04')
        assert listed['status'] == 'STALE' and listed['plan'] is None
        assert [item['plan'] for item in listed['candidates']] == ['HOL-801', 'HOL-802']
        judgement = next(row for row in snapshot['penalties']['rows'] if row['key'] == '002:A04')
        assert (judgement['status'], judgement['reason']) == ('ISOLATED', 'PLAN_OVERLAP')
    finally:
        source.write_bytes(baseline)


def _builder(settings):
    from functools import partial

    from app.modules.confirmations.service import build_confirmation
    from app.pipeline import Audit

    plans = Audit(settings, Options(), Store(settings.database)).plans['decisions']
    return partial(build_confirmation, plans)


def test_confirmation_of_missing_shelf_raises_before_writing(settings):
    store = Store(settings.database)
    store.initialize()
    with pytest.raises(AuditError) as error:
        store.save_confirmation(payload(key='009:A01'), _builder(settings))
    assert error.value.code == 'SHELF_NOT_FOUND'
    with sqlite3.connect(settings.database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM confirmations').fetchone() == (0,)
