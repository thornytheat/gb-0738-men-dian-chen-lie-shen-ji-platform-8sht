from collections import Counter

from app.errors import AuditError


def reconcile_attribution(payload):
    """归属勾稽：总数＝各店条目之和＝各合并商品条目之和；确认前后均不下罚额。"""
    entries = payload['entries']
    totals = payload['totals']
    if totals['entries_total'] != len(entries):
        raise AuditError('ATTRIBUTION_LEDGER_MISMATCH', '归属总数与条目数不符', 500)
    if totals['fine_cents'] != 0 or any(entry['fine_cents'] != 0 for entry in entries):
        raise AuditError('ATTRIBUTION_LEDGER_MISMATCH', '换码确认不得产生罚额', 500)
    store_total = sum(row['entries'] for row in payload['stores'])
    routed = [entry for entry in entries if entry['final_sku']]
    product_total = sum(row['entry_count'] for row in payload['products'])
    unrouted = [entry['entry_id'] for entry in entries if entry['is_legacy'] and not entry['final_sku']]
    if unrouted:
        raise AuditError('ATTRIBUTION_TARGET_MISSING', '旧名在该门店无归属路由，不能发布归属结论', 422, unrouted)
    if store_total != len(entries) or product_total != len(routed):
        raise AuditError('ATTRIBUTION_LEDGER_MISMATCH', '各店或各商品条目与总数对不上', 500)
    by_store = Counter(entry['store'] for entry in entries)
    for row in payload['stores']:
        if row['entries'] != by_store[row['store']] \
                or row['legacy_entries'] != sum(1 for entry in entries
                                                 if entry['store'] == row['store'] and entry['is_legacy']):
            raise AuditError('ATTRIBUTION_LEDGER_MISMATCH', '门店条目对账失败', 500, row['store'])
    final_skus = Counter(entry['final_sku'] for entry in routed)
    if set(final_skus) != {product['sku'] for product in payload['products']}:
        raise AuditError('ATTRIBUTION_LEDGER_MISMATCH', '合并商品集合与条目去向不符', 500)
    for product in payload['products']:
        breakdown = sum(item['entries'] for item in product['store_entries'])
        if product['entry_count'] != final_skus[product['sku']] or breakdown != product['entry_count']:
            raise AuditError('ATTRIBUTION_LEDGER_MISMATCH', '跨区合并商品对账失败', 500, product['sku'])
        for item in product['store_entries']:
            actual = sum(1 for entry in routed
                         if entry['final_sku'] == product['sku'] and entry['store'] == item['store'])
            if item['entries'] != actual:
                raise AuditError('ATTRIBUTION_LEDGER_MISMATCH', '商品分账到门店的条目数不符', 500, product['sku'])
    if totals['legacy_total'] != sum(1 for entry in entries if entry['is_legacy']):
        raise AuditError('ATTRIBUTION_LEDGER_MISMATCH', '旧名条目计数不符', 500)
    return True
