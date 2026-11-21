from collections import Counter, defaultdict
from hashlib import sha256

from app.config import RULE_VERSION
from app.manifest import canonical, now
from app.modules.attribution.reconcile import reconcile_attribution
from app.modules.attribution.routes import routes_from
from app.modules.intake.service import acquire
from app.modules.intake.values import identifier, sku


def batch_key(settings, options):
    """归属批次键：只由路由相关原件、参数与规则版本决定，重跑得到同一批次。"""
    names = ('campaign.json', 'stores.csv', 'visits.csv', 'aliases.csv', 'skumap.csv', 'observations.csv', 'photos.csv')
    bundle = acquire(settings.sample_root, options.dataset)
    digest = {name: sha256(bundle[name]).hexdigest() for name in names}
    key = {'parameters': {'dataset': options.dataset}, 'rule_version': RULE_VERSION, 'inputs': digest}
    return sha256(canonical(key).encode()).hexdigest()


def build_attribution(intake, plans, photos, batch_id):
    tables = intake['tables']
    aliases = plans['aliases']
    routes, route_issues = routes_from(tables['skumap'], {row['store']: row for row in intake['stores']}, aliases)
    route_index = {(row['store'], row['alias']): row for row in routes}
    # 进入换码期的旧名：出现在门店路由表里的别名；同店路由冲突或缺路由时不得回落到全局旧名册
    managed_aliases = {row['alias'] for row in routes} | {
        row['source']['raw'][1] for row in route_issues if row['reason'] == 'SKUMAP_ROUTE_CONFLICT'}
    candidates_by_name = defaultdict(set)
    for row in routes:
        candidates_by_name[row['alias']].add(row['sku'])
    conflict_pairs = {(row['row_key'][0], row['row_key'][1])
                      for row in route_issues if row['reason'] == 'SKUMAP_ROUTE_CONFLICT'}
    for row in tables['skumap']['rows']:
        pair = (identifier(row['source']['raw'][0]), sku(row['source']['raw'][1]))
        if pair in conflict_pairs:
            candidates_by_name[pair[1]].add(sku(row['source']['raw'][2]))
    visit_store = {row['visit']: row['store'] for row in intake['visits']}
    entries = []
    for item in photos['observations']:
        if item['kind'] != 'ITEM':
            continue
        raw_name = item['source']['raw'][4]
        normalized = sku(raw_name)
        store = visit_store.get(item['visit'])
        override = route_index.get((store, normalized))
        # 换码期待确认旧名：在旧名册中且属于门店换码路由管理范围
        is_legacy = normalized in aliases and (normalized in managed_aliases)
        if override is not None:
            final_sku, route_source, routed = override['sku'], override['source']['id'], True
        elif is_legacy:
            # 路由缺失或同店冲突：保留旧名与候选，不给出去向，由勾稽阻断发布
            final_sku, route_source, routed = None, None, False
        elif normalized in aliases:
            # 不在换码路由范围的旧名沿用全局旧名册
            final_sku, route_source, routed = aliases[normalized], None, False
        else:
            final_sku, route_source, routed = normalized, None, False
        entries.append({'entry_id': item['source']['id'], 'store': store, 'visit': item['visit'], 'slot': item['slot'],
                        'photo': item['photo'], 'legacy_name': raw_name, 'normalized_name': normalized,
                        'is_legacy': is_legacy, 'final_sku': final_sku, 'routed_by_store': routed,
                        'route_source': route_source,
                        'candidates': sorted(candidates_by_name.get(normalized, ())),
                        'facings': item['facings'], 'price_cents': item['price_cents'],
                        'source': item['source'], 'fine_cents': 0})
    store_rows = []
    for store in sorted({entry['store'] for entry in entries}):
        members = [entry for entry in entries if entry['store'] == store]
        store_rows.append({'store': store, 'entries': len(members),
                           'legacy_entries': sum(1 for entry in members if entry['is_legacy'])})
    grouped = defaultdict(list)
    for entry in entries:
        if entry['final_sku']:
            grouped[entry['final_sku']].append(entry)
    products = []
    for sku_name in sorted(grouped):
        members = grouped[sku_name]
        per_store = Counter(entry['store'] for entry in members)
        legacy_names = sorted({entry['normalized_name'] for entry in members if entry['is_legacy']})
        products.append({'sku': sku_name, 'entry_count': len(members),
                         'legacy_names': legacy_names,
                         'store_entries': [{'store': store, 'entries': per_store[store]}
                                           for store in sorted(per_store)]})
    totals = {'entries_total': len(entries),
              'legacy_total': sum(1 for entry in entries if entry['is_legacy']),
              'product_total': len(products), 'fine_cents': 0}
    payload = {'batch_id': batch_id, 'dataset': intake['campaign']['code'],
               'status': 'DRAFT', 'created_at': now(), 'published_at': None,
               'rule_version': RULE_VERSION, 'entries': entries, 'stores': store_rows,
               'products': products, 'totals': totals,
               'routes': routes, 'quarantine': route_issues + list(tables['skumap']['quarantine']),
               'events': [], 'balanced': True}
    reconcile_attribution(payload)
    return payload
