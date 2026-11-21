from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Options, Settings
from app.errors import AuditError
from app.main import create_app
from app.modules.attribution.reconcile import reconcile_attribution
from app.modules.attribution.service import batch_key, build_attribution
from app.pipeline import Audit
from app.store import Store
from conftest import locate_or_fail


def build(audit):
    return build_attribution(audit.intake, audit.plans, audit.photos, batch_key(audit.settings, audit.options))


def confirm_form(**changes):
    return {'request_id': 'ATTR-CONF-001', 'actor': '林归属', 'note': '换码归属确认发布',
            'evidence_ref': '路由簿-7', **changes}


def test_same_old_name_splits_into_two_skus_by_store_without_merging(field):
    payload = build(field)
    legacy = [entry for entry in payload['entries'] if entry['is_legacy']]
    assert {entry['normalized_name'] for entry in legacy} == {'谷雨陈酿'}
    by_store = {(entry['store'], entry['legacy_name']): entry['final_sku'] for entry in legacy}
    assert by_store[('001', '谷雨陈酿')] == 'GR-209'
    assert by_store[('006', '谷雨陈酿')] == 'GR-211'
    products = {product['sku']: product for product in payload['products']}
    assert set(products) == {'GR-209', 'GR-211', 'MALT-900'}
    assert products['GR-209']['legacy_names'] == ['谷雨陈酿']
    assert products['GR-211']['legacy_names'] == ['谷雨陈酿']
    assert locate_or_fail(products['GR-209']['store_entries'], store='001')['entries'] == 2
    assert locate_or_fail(products['GR-211']['store_entries'], store='006')['entries'] == 2


def test_draft_keeps_old_name_and_candidates_and_never_fines(field):
    payload = build(field)
    assert payload['status'] == 'DRAFT' and payload['totals']['fine_cents'] == 0
    entry = locate_or_fail(payload['entries'], entry_id='observations.csv:47')
    assert entry['legacy_name'] == '谷雨陈酿' and entry['final_sku'] == 'GR-211'
    assert entry['candidates'] == ['GR-209', 'GR-211'] and entry['fine_cents'] == 0
    assert all(row['fine_cents'] == 0 for row in payload['entries'])


def test_totals_reconcile_with_store_entries_and_products(field):
    payload = build(field)
    assert reconcile_attribution(payload) is True
    assert payload['totals']['entries_total'] == len(payload['entries'])
    assert sum(row['entries'] for row in payload['stores']) == payload['totals']['entries_total']
    assert sum(product['entry_count'] for product in payload['products']) == payload['totals']['entries_total']


def test_missing_store_route_blocks_publication_and_keeps_legacy_name(field):
    field.bundle['skumap.csv'] = field.bundle['skumap.csv'].replace('006,谷雨陈酿,GR-211\n'.encode(), b'')
    with pytest.raises(AuditError) as failure:
        build(field)
    assert failure.value.code == 'ATTRIBUTION_TARGET_MISSING'
    assert 'observations.csv:47' in failure.value.evidence


def test_conflicting_route_quarantines_all_copies(field):
    from app.modules.attribution.routes import routes_from
    field.bundle['skumap.csv'] += '001,谷雨陈酿,GR-211\r\n'.encode()
    routes, issues = routes_from(field.intake['tables']['skumap'],
                                 {row['store']: row for row in field.intake['stores']}, field.plans['aliases'])
    conflicts = [row for row in issues if row['reason'] == 'SKUMAP_ROUTE_CONFLICT']
    assert len(conflicts) == 2
    assert not [row for row in routes if row['store'] == '001']
    assert len([row for row in routes if row['store'] == '006']) == 1
    with pytest.raises(AuditError) as failure:
        build(field)
    assert failure.value.code == 'ATTRIBUTION_TARGET_MISSING'


def test_unknown_store_and_alias_in_route_table_are_quarantined(field):
    field.bundle['skumap.csv'] += '099,谷雨陈酿,GR-209\r\n001,不存在旧名,GR-209\r\n'.encode()
    payload = build(field)
    codes = sorted(row['reason'] for row in payload['quarantine'])
    assert codes == ['SKUMAP_ALIAS_UNKNOWN', 'SKUMAP_STORE_UNKNOWN']


def test_malformed_route_row_is_quarantined_once_and_does_not_block_valid_routes(field):
    field.bundle['skumap.csv'] += '001,谷雨陈酿\r\n'.encode()
    payload = build(field)
    bad = [row for row in payload['quarantine'] if row['reason'] == 'COLUMN_COUNT']
    assert len(bad) == 1 and bad[0]['source']['id'] == 'skumap.csv:6'
    assert len(payload['routes']) == 4 and reconcile_attribution(payload) is True


def test_batch_id_is_deterministic_and_creation_is_idempotent(client):
    first = client.post('/api/attribution/batches', json={})
    second = client.post('/api/attribution/batches', json={})
    assert first.status_code == second.status_code == 200
    assert first.json()['reused'] is False and second.json()['reused'] is True
    assert first.json()['batch_id'] == second.json()['batch_id']
    preview = client.get('/api/attribution')
    assert preview.json()['batch_id'] == first.json()['batch_id']


def test_confirmation_is_idempotent_and_repeat_publish_is_rejected(client):
    batch = client.post('/api/attribution/batches', json={}).json()
    bid = batch['batch_id']
    ok = client.post(f'/api/attribution/batches/{bid}/confirm', json=confirm_form())
    assert ok.json()['batch']['status'] == 'CONFIRMED' and ok.json()['reused'] is False
    replay = client.post(f'/api/attribution/batches/{bid}/confirm', json=confirm_form())
    assert replay.status_code == 200 and replay.json()['reused'] is True
    assert replay.json()['event']['version'] == 1
    again = client.post(f'/api/attribution/batches/{bid}/confirm',
                        json=confirm_form(request_id='ATTR-CONF-OTHER'))
    assert again.status_code == 409 and again.json()['detail']['code'] == 'ATTRIBUTION_NOT_DRAFT'
    fetched = client.get(f'/api/attribution/batches/{bid}').json()
    assert fetched['status'] == 'CONFIRMED' and len(fetched['events']) == 1
    assert fetched['published_at'] == fetched['events'][0]['created_at']


def test_same_request_id_with_different_body_conflicts(client):
    bid = client.post('/api/attribution/batches', json={}).json()['batch_id']
    client.post(f'/api/attribution/batches/{bid}/confirm', json=confirm_form())
    clash = client.post(f'/api/attribution/batches/{bid}/confirm', json=confirm_form(note='改写说明'))
    assert clash.status_code == 409 and clash.json()['detail']['code'] == 'REQUEST_CONFLICT'


def test_published_batch_evidence_remains_recoverable(client):
    bid = client.post('/api/attribution/batches', json={}).json()['batch_id']
    client.post(f'/api/attribution/batches/{bid}/confirm', json=confirm_form())
    evidence = client.get(f'/api/attribution/batches/{bid}/evidence')
    assert evidence.status_code == 200
    disposition = evidence.headers['content-disposition']
    assert disposition.startswith('attachment; filename="attribution-')
    assert disposition.endswith('.json"')
    packet = evidence.json()
    assert packet['format'] == 'shelf-evidence/attribution-1'
    assert packet['status'] == 'CONFIRMED' and packet['batch_id'] == bid
    assert packet['totals']['fine_cents'] == 0 and packet['reason_dictionary']['SKUMAP_ROUTE_CONFLICT']


def test_failed_confirmation_keeps_draft_and_its_original_evidence(client, monkeypatch):
    bid = client.post('/api/attribution/batches', json={}).json()['batch_id']
    stale = client.post(f'/api/attribution/batches/{bid}/confirm',
                        json=confirm_form(expected_version=1))
    assert stale.status_code == 409 and stale.json()['detail']['code'] == 'STALE_ATTRIBUTION'

    # 发布前勾稽若失败：事务回滚，草稿与原凭据原样保留
    from app.modules.attribution import handler as handler_module

    def broken(_payload):
        raise AuditError('ATTRIBUTION_LEDGER_MISMATCH', '模拟对账失败', 500)

    monkeypatch.setattr(handler_module, 'reconcile_attribution', broken, raising=False)
    failed = client.post(f'/api/attribution/batches/{bid}/confirm', json=confirm_form())
    assert failed.status_code == 500 and failed.json()['detail']['code'] == 'ATTRIBUTION_LEDGER_MISMATCH'
    draft = client.get(f'/api/attribution/batches/{bid}').json()
    assert draft['status'] == 'DRAFT' and draft['events'] == [] and draft['version'] == 0
    evidence = client.get(f'/api/attribution/batches/{bid}/evidence')
    assert evidence.status_code == 200 and evidence.json()['status'] == 'DRAFT'


def test_concurrent_creation_publishes_one_batch(settings):
    store = Store(settings.database)
    store.initialize()

    def publish(_):
        audit = Audit(settings, Options())
        return store.save_attribution(build(audit))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(publish, range(4)))
    assert sum(not reused for _, reused in results) == 1


def test_unknown_batch_returns_not_found(client):
    missing = client.get('/api/attribution/batches/' + '0' * 64)
    assert missing.status_code == 404 and missing.json()['detail']['code'] == 'ATTRIBUTION_NOT_FOUND'


@pytest.fixture
def settings(tmp_path):
    return Settings(ROOT / 'sample_data', tmp_path / 'attribution.sqlite3')


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as session:
        yield session
