from hashlib import sha256


def apportion(groups, target):
    """按店组正整数权重以最大余数法分配目标名额；余数并列按组编号排序。"""
    weights = {group['code']: group['weight'] for group in groups}
    total_weight = sum(weights.values())
    quotas = {}
    fractions = []
    for code in sorted(weights):
        share = target * weights[code] / total_weight
        quotas[code] = int(share)
        fractions.append((-(share - quotas[code]), code))
    for _, code in sorted(fractions):
        if sum(quotas.values()) >= target:
            break
        quotas[code] += 1
    return quotas


def _score(seed, key):
    digest = sha256(f'{seed}\x00{key}'.encode()).hexdigest()
    return digest


def choose_slots(intake, target):
    """组内按 SHA-256(种子+NUL+门店+NUL+柜位) 排序抽取；哈希并列用柜位键。"""
    seed = intake['campaign']['seed']
    groups = intake['campaign']['groups']
    store_map = {row['store']: row for row in intake['stores']}
    quota = apportion(groups, target)
    pool = {}
    for slot in intake['slots']:
        pool.setdefault(store_map[slot['store']]['group'], []).append(slot)
    chosen, quotas = [], []
    for group in groups:
        code = group['code']
        candidates = pool.get(code, [])
        ranked = sorted(candidates,
                        key=lambda slot: (_score(seed, f"{slot['store']}\x00{slot['slot']}"),
                                          f"{slot['store']}:{slot['slot']}"))
        picked = ranked[:quota[code]]
        selected = len(picked)
        quotas.append({'code': code, 'target': quota[code], 'pool': len(candidates),
                       'selected': selected, 'unfilled': quota[code] - selected,
                       'ranked_candidates': [
                               {'key': f"{slot['store']}:{slot['slot']}", 'store': slot['store'], 'slot': slot['slot'],
                                'score': _score(seed, f"{slot['store']}\x00{slot['slot']}")}
                               for slot in ranked]})
        chosen.extend(picked)
    chosen.sort(key=lambda slot: (slot['store'], slot['slot']))
    return chosen, quotas
