from collections import defaultdict

from app.modules.intake.values import InvalidRow, identifier, sku
from app.reasons import mark


def routes_from(table, store_map, aliases):
    """解析门店级换码路由。同店同旧名去向矛盾时整组隔离，不静默择一。"""
    accepted, issues = [], []
    groups = defaultdict(list)
    for row in table['rows']:
        source = row['source']
        try:
            store = identifier(row['store'])
            if store not in store_map:
                raise InvalidRow('SKUMAP_STORE_UNKNOWN')
            name = sku(row['alias'])
            if name not in aliases:
                raise InvalidRow('SKUMAP_ALIAS_UNKNOWN')
            target = sku(row['sku'])
        except InvalidRow as error:
            issues.append(mark(source, error.code, row_key=[row['store'], row['alias']]))
            continue
        groups[(store, name)].append((source, target))
    for key, entries in sorted(groups.items()):
        sources = [source for source, _ in entries]
        targets = {target for _, target in entries}
        if len(targets) > 1:
            for source, _ in entries:
                issues.append(mark(source, 'SKUMAP_ROUTE_CONFLICT', row_key=list(key),
                                   conflicts=[item['id'] for item in sources]))
            continue
        source, target = entries[0]
        accepted.append({'source': source, 'store': key[0], 'alias': key[1], 'sku': target})
        for extra, _ in entries[1:]:
            issues.append(mark(extra, 'DUPLICATE_ROW', row_key=list(key), kept=source['id']))
    return accepted, issues
