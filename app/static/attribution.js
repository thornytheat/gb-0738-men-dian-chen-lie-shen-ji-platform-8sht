import {element, flag, money, reason} from './figures.js';

function table(columns, values) {
  if (!values.length) return element('p', '当前没有记录。', 'empty');
  const box = element('div', null, 'table-scroll');
  const tab = element('table', null, 'records-table');
  const head = tab.createTHead().insertRow();
  for (const [heading, local] of columns) {
    const label = element('th', heading, local);
    label.scope = 'col';
    head.append(label);
  }
  const body = tab.createTBody();
  for (const record of values) {
    const line = body.insertRow();
    for (const [index, value] of record.entries()) {
      const cell = line.insertCell();
      cell.className = columns[index][1] || '';
      if (value instanceof Node) cell.append(value);
      else cell.textContent = value ?? '—';
    }
  }
  box.append(tab);
  return box;
}

const group = (root, title, child) => root.append(element('h3', title), child);

function post(url, body) {
  return fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
}

async function readJson(response) {
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(data?.detail?.message || '操作失败，请重试。');
  return data;
}

export function attribution(root, payload, options) {
  const controls = element('div', null, 'run-controls');
  const draftButton = element('button', payload.status === 'CONFIRMED' ? '重新取回已发布批次' : '生成归属批次', 'button primary');
  draftButton.type = 'button';
  const confirmButton = element('button', '确认发布归属结论', 'button primary');
  confirmButton.type = 'button';
  const exportLink = document.createElement('a');
  exportLink.className = 'button quiet';
  exportLink.textContent = '↓ 追回原凭据';
  exportLink.target = '_blank';
  exportLink.rel = 'noopener';
  exportLink.href = `/api/attribution/batches/${payload.batch_id}/evidence`;
  const feedback = element('p', null, 'feedback');
  feedback.hidden = true;
  controls.append(draftButton, confirmButton, exportLink, feedback);
  root.append(controls);

  const status = element('p', null, 'feedback');
  status.append(element('strong', `批次 ${payload.batch_id.slice(0, 12)} · `), flag(payload.status),
    element('span', ` 旧名条目 ${payload.totals.legacy_total} / 总条目 ${payload.totals.entries_total} / 合并商品 ${payload.totals.product_total} / 罚额 ${money(payload.totals.fine_cents)} 元`));
  root.append(status);

  group(root, '跨区合并商品（同名旧装两种去向分列）', table(
    [['合并 SKU'], ['旧名'], ['条目数', 'num'], ['分门店条目']],
    payload.products.map(product => [product.sku, product.legacy_names.join(' / ') || '—', product.entry_count,
      product.store_entries.map(item => `${item.store} 店 ${item.entries} 条`).join(' / ')])));
  group(root, '各店归属条目（总数勾稽）', table(
    [['门店'], ['条目数', 'num'], ['其中换码旧名', 'num']],
    payload.stores.map(row => [row.store, row.entries, row.legacy_entries])));
  group(root, '旧名条目与候选去向（确认前保留旧名和候选，不下罚额）', table(
    [['源行'], ['门店'], ['柜位'], ['现场旧名'], ['候选去向'], ['归属 SKU'], ['路由依据'], ['罚额 · 元', 'num']],
    payload.entries.filter(entry => entry.is_legacy).map(entry => [
      entry.entry_id, entry.store, `${entry.visit}:${entry.slot}`, entry.legacy_name,
      entry.candidates.join(' / '), entry.final_sku, entry.route_source || '沿用全局旧名册', money(entry.fine_cents)])));
  group(root, '门店换码路由原件', table(
    [['源行'], ['门店'], ['旧名'], ['去向 SKU']],
    payload.routes.map(row => [row.source.id, row.store, row.alias, row.sku])));
  if (payload.quarantine.length) {
    group(root, '路由表隔离', table([['源行'], ['原因码'], ['原因'], ['录入原值']],
      payload.quarantine.map(row => [row.source.id, row.reason, reason(row.reason), row.source.raw.join(' | ')])));
  }
  if (payload.events.length) {
    group(root, '发布时间线', table([['动作'], ['版本', 'num'], ['经办人'], ['依据编号', 'clip'], ['说明', 'clip'], ['UTC 时间']],
      payload.events.map(event => [event.action, event.version, event.actor, event.evidence_ref, event.note, event.created_at])));
  }

  function note(message, problem = false) {
    feedback.hidden = !message;
    feedback.textContent = message || '';
    feedback.classList.toggle('error', problem);
  }

  draftButton.addEventListener('click', async () => {
    draftButton.disabled = true;
    try {
      const data = await readJson(await post('/api/attribution/batches', options.params()));
      note(data.reused ? '已取回同一归属批次，未重复生成。' : '归属批次已生成，仍为草稿，未下罚额。');
      root.dispatchEvent(new CustomEvent('attribution:update', {bubbles: true, detail: data}));
    } catch (error) {
      note(error.message, true);
    } finally {
      draftButton.disabled = false;
    }
  });

  confirmButton.addEventListener('click', async () => {
    if (payload.status === 'CONFIRMED') {
      note('该批次已发布，已保留原凭据，可直接追回。', true);
      return;
    }
    confirmButton.disabled = true;
    try {
      const data = await readJson(await post(`/api/attribution/batches/${payload.batch_id}/confirm`, {
        request_id: (crypto.randomUUID && crypto.randomUUID()) || `ATTR-${Date.now()}`,
        expected_version: payload.version || 0, actor: options.actor, note: '换码归属确认发布',
        evidence_ref: `skumap/${payload.dataset}`,
      }));
      note(data.reused ? '同一确认已受理，重放未重复生成。' : '归属结论已发布，总数与各店条目勾稽通过。');
      root.dispatchEvent(new CustomEvent('attribution:update', {bubbles: true, detail: data.batch}));
    } catch (error) {
      note(error.message, true);
    } finally {
      confirmButton.disabled = false;
    }
  });
}
