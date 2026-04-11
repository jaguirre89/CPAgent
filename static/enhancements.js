/* Tax Dashboard enhancements — layered on top of app.js to wire the round-1
   features (readiness strip, findings transitions, teach loop, filing tab,
   AI Settings + budget ceiling + model switcher).  Keeps the original app.js
   untouched for easier reverts.
*/

(function () {
  const $ = (sel) => document.querySelector(sel);

  function currentYear() {
    const sel = document.querySelector('#year-select');
    if (sel && sel.value) return parseInt(sel.value, 10);
    return new Date().getFullYear() - 1; // default: last completed year
  }

  async function fetchJSON(url, opts) {
    const r = await fetch(url, opts || {});
    if (!r.ok) throw new Error(`${r.status} ${url}`);
    return r.json();
  }

  /* ─── Readiness strip (Phase E) ─────────────────────────────────────────── */
  async function refreshReadiness() {
    const strip = $('#readiness-strip');
    if (!strip) return;
    const year = currentYear();
    try {
      const r = await fetchJSON(`/api/readiness/${year}`);
      if (!r || r.readiness_pct == null) { strip.style.display = 'none'; return; }
      strip.style.display = 'flex';
      strip.className = 'readiness-strip ' + (r.color || '');
      $('#readiness-pct').textContent = r.readiness_pct + '%';
      const c = r.severity_counts || {};
      const docs = r.breakdown?.documents;
      $('#readiness-sub').innerHTML =
        `${docs ? `${docs.found}/${docs.expected} docs` : ''} · ` +
        `<span class="badge BLOCKED">${c.BLOCKED || 0} BLOCKED</span> ` +
        `<span class="badge REQUIRED">${c.REQUIRED || 0} REQUIRED</span> ` +
        `<span class="badge REVIEW">${c.REVIEW || 0} REVIEW</span>` +
        (r.can_generate_filing ? ' · <span class="ok">ready to file</span>' : '');
      const bars = $('#readiness-bars');
      if (bars && r.breakdown) {
        bars.innerHTML = Object.entries(r.breakdown).map(([k, v]) =>
          `<div class="readiness-bar"><span>${k} (${v.weight}%)</span><div class="bar-track"><div class="bar-fill" style="width:${v.value || 0}%"></div></div></div>`
        ).join('');
      }
    } catch (e) {
      strip.style.display = 'none';
    }
  }

  /* ─── Findings: transitions summary + teach button ──────────────────────── */
  async function enhanceFindings() {
    const container = $('#findings-content');
    if (!container) return;
    const year = currentYear();
    try {
      const data = await fetchJSON(`/api/findings/${year}`);
      const summary = data.summary || '';
      const transitions = data.transitions || [];
      const current = data.current || [];

      const header = document.createElement('div');
      header.className = 'findings-summary';
      header.innerHTML = `<h3>${summary}</h3>
        <div class="findings-counts">${Object.entries(data.counts || {}).map(([k, v]) => `<span class="badge ${k}">${k}: ${v}</span>`).join(' ')}</div>`;
      container.innerHTML = '';
      container.appendChild(header);

      if (transitions.length) {
        const tblock = document.createElement('div');
        tblock.className = 'findings-transitions';
        tblock.innerHTML = `<h4>Recent transitions</h4>` + transitions.map(t =>
          `<div class="transition"><span class="badge ${t.previous_severity}">${t.previous_severity}</span> → <span class="badge ${t.severity}">${t.severity}</span> ${t.topic}: ${t.title}</div>`
        ).join('');
        container.appendChild(tblock);
      }

      // "Resolve All with AI" button — shown when REQUIRED GL findings exist
      const requiredGL = current.filter(f =>
        f.priority === 'REQUIRED' &&
        ((f.topic || '').toLowerCase().includes('gl reconciliation') ||
         (f.topic || '').toLowerCase().includes('uncategorized'))
      );
      if (requiredGL.length > 0) {
        const aiBar = document.createElement('div');
        aiBar.className = 'ai-resolve-bar';
        aiBar.innerHTML = `
          <button id="btn-resolve-all-ai" class="btn btn-primary" style="background:#7c3aed">
            Resolve All with AI (${requiredGL.length} findings)
          </button>
          <span class="muted" style="margin-left:8px;font-size:12px">Send uncategorized transactions to AI for classification suggestions</span>
        `;
        container.appendChild(aiBar);
        document.getElementById('btn-resolve-all-ai').addEventListener('click', () => openBatchResolveModal(requiredGL));
      }

      const list = document.createElement('div');
      list.className = 'findings-list';
      const active = current.filter(f => f.priority !== 'LOGGED');
      active.forEach(f => {
        const card = document.createElement('div');
        card.className = 'finding-card';
        const isUncategorized = (f.topic || '').toLowerCase().includes('uncategorized') ||
                                (f.topic || '').toLowerCase().includes('gl reconciliation');
        card.innerHTML = `
          <div class="finding-head">
            <span class="badge ${f.priority}">${f.priority}</span>
            <span class="finding-topic">${f.topic || ''}</span>
          </div>
          <div class="finding-body">${f.finding || f.title || ''}</div>
          <div class="finding-actions">
            ${isUncategorized ? `<button class="btn btn-sm teach-btn"
              data-memo="${(f.memo_pattern || '').replace(/"/g, '&quot;')}"
              data-entity="${(f.entity || '').replace(/"/g, '&quot;')}"
              data-finding="${(f.finding || f.title || '').replace(/"/g, '&quot;')}">Teach</button>` : ''}
            ${f.fingerprint ? `<button class="btn btn-sm dismiss-btn" data-fp="${f.fingerprint}" title="Dismiss this finding — mark as reviewed and resolved">Dismiss</button>` : ''}
          </div>
        `;
        list.appendChild(card);
      });
      container.appendChild(list);

      list.querySelectorAll('.teach-btn').forEach(btn => {
        btn.addEventListener('click', () => openTeachModal(btn.dataset.memo, btn.dataset.entity, btn.dataset.finding));
      });
      list.querySelectorAll('.dismiss-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const fp = btn.dataset.fp;
          try {
            window.__suppressRefresh = Date.now() + 8000;
            await fetchJSON(`/api/findings/${currentYear()}/dismiss`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ fingerprint: fp, reason: 'User dismissed from Findings tab' }),
            });
            const card = btn.closest('.finding-card');
            if (card) {
              card.style.transition = 'opacity 0.3s, max-height 0.3s';
              card.style.opacity = '0';
              card.style.maxHeight = '0';
              card.style.overflow = 'hidden';
              setTimeout(() => card.remove(), 350);
            }
            toast('Finding dismissed');
          } catch (e) {
            toast('Dismiss failed: ' + e.message);
          }
        });
      });
    } catch (e) {
      console.warn('enhanceFindings failed:', e);
    }
  }

  /* ─── Batch AI Resolve modal ──────────────────────────────────────────── */
  async function openBatchResolveModal(findings) {
    const existing = $('#batch-resolve-modal');
    if (existing) existing.remove();
    // Suppress background refreshes while modal is open
    window.__suppressRefresh = Date.now() + 300000; // 5 min

    const modal = document.createElement('div');
    modal.id = 'batch-resolve-modal';
    modal.className = 'modal-overlay';
    modal.innerHTML = `
      <div class="modal" style="max-width:820px;max-height:85vh;overflow:auto">
        <h3>AI Classification</h3>
        <p class="muted">Sending ${findings.length} uncategorized groups to AI for analysis...</p>
        <div id="batch-resolve-status" style="margin:12px 0"><div class="spinner"></div> Analyzing transactions...</div>
        <div id="batch-resolve-results" style="display:none"></div>
        <div id="batch-resolve-actions" style="display:none;margin-top:16px;text-align:right">
          <button id="batch-cancel" class="btn">Cancel</button>
          <button id="batch-apply" class="btn btn-primary" style="margin-left:8px">Apply Selected Rules</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });

    const items = findings.map(f => ({
      memo_pattern: f.memo_pattern || '',
      samples: f.samples || [],
      group_total: f.group_total || 0,
      group_count: f.group_count || 0,
      account: f.account || '',
      entity: f.entity || '',
    }));

    try {
      const year = currentYear();
      const result = await fetchJSON('/api/rules/suggest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items, year }),
      });

      const suggestions = result.suggestions || [];
      const costLabel = result.cost_usd ? `$${result.cost_usd.toFixed(4)}` : '';

      document.getElementById('batch-resolve-status').innerHTML =
        `AI returned ${suggestions.length} suggestions` +
        (costLabel ? ` (cost: ${costLabel})` : '') + '.';

      const rows = findings.map((f, idx) => {
        const s = suggestions.find(s =>
          s.memo_pattern === (f.memo_pattern || '') ||
          (s.memo_pattern && (f.memo_pattern || '').includes(s.memo_pattern))
        ) || suggestions[idx] || {};

        const cat = s.category || '';
        const conf = s.confidence || 0;
        const confPct = Math.round(conf * 100);
        const autoAccept = conf >= 0.8;
        const needsInput = conf < 0.6;
        const rationale = s.rationale || '';
        const patternSuggestion = s.pattern_suggestion || f.memo_pattern || '';

        const samples = (f.samples || []).map(t =>
          `<div style="font-size:10px;color:#64748b;margin-top:2px">${t.date || ''} · ${(t.memo || '').substring(0, 80)} · $${Math.abs(t.amount || 0).toFixed(2)}</div>`
        ).join('');

        return `
          <tr data-idx="${idx}" data-memo="${(patternSuggestion).replace(/"/g, '&quot;')}"
              data-entity="${(f.entity || '').replace(/"/g, '&quot;')}">
            <td><input type="checkbox" class="batch-check" ${autoAccept ? 'checked' : ''}></td>
            <td>
              <strong>${f.memo_pattern || '(no memo)'}</strong>
              <div class="muted" style="font-size:11px">${f.group_count || 0} txns · $${(f.group_total || 0).toFixed(2)} · ${f.account || ''}</div>
              ${samples}
            </td>
            <td>
              <select class="batch-category" style="font-size:12px;padding:4px">
                ${SCHEDULE_C_CATEGORIES.map(c => {
                  const val = c === '(Exclude from Schedule C)' ? '' : c;
                  const selected = (val === cat || (val === '' && !cat)) ? 'selected' : '';
                  return `<option value="${val}" ${selected}>${c}</option>`;
                }).join('')}
              </select>
            </td>
            <td>
              <span class="badge ${conf >= 0.8 ? 'REVIEW' : conf >= 0.6 ? 'REQUIRED' : 'BLOCKED'}"
                    style="font-size:11px">${confPct}%</span>
              ${needsInput ? '<div style="color:#d97706;font-size:11px;margin-top:2px">Needs your input</div>' : ''}
            </td>
            <td style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis"
                title="${rationale.replace(/"/g, '&quot;')}">${rationale}</td>
          </tr>
        `;
      }).join('');

      document.getElementById('batch-resolve-results').innerHTML = `
        <table class="data-table" style="font-size:13px">
          <thead><tr>
            <th style="width:30px"><input type="checkbox" id="batch-select-all" checked></th>
            <th>Transaction</th>
            <th>Category</th>
            <th style="width:70px">Conf.</th>
            <th>Rationale</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
      document.getElementById('batch-resolve-results').style.display = 'block';
      document.getElementById('batch-resolve-actions').style.display = 'block';

      document.getElementById('batch-select-all').addEventListener('change', function () {
        modal.querySelectorAll('.batch-check').forEach(cb => { cb.checked = this.checked; });
      });

      document.getElementById('batch-cancel').addEventListener('click', () => modal.remove());

      document.getElementById('batch-apply').addEventListener('click', async () => {
        const applyBtn = document.getElementById('batch-apply');
        applyBtn.disabled = true;
        applyBtn.textContent = 'Applying...';

        const rules = [];
        modal.querySelectorAll('tr[data-idx]').forEach(row => {
          const cb = row.querySelector('.batch-check');
          if (!cb || !cb.checked) return;
          const catSel = row.querySelector('.batch-category');
          const category = catSel ? catSel.value : '';
          rules.push({
            pattern_type: 'memo',
            pattern: row.dataset.memo,
            schedule_c_category: category || null,
            entity_scope: row.dataset.entity || null,
            reasoning: 'AI-suggested, user-approved via batch resolve',
          });
        });

        if (rules.length === 0) {
          toast('No rules selected');
          applyBtn.disabled = false;
          applyBtn.textContent = 'Apply Selected Rules';
          return;
        }

        try {
          const r = await fetchJSON('/api/rules/batch-learn', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rules }),
          });
          modal.remove();
          toast(`${r.count} rules applied. Findings refreshing...`);
          window.__suppressRefresh = Date.now() + 8000;
          setTimeout(() => {
            enhanceFindings();
            refreshReadiness();
          }, 2500);
        } catch (e) {
          toast('Batch apply failed: ' + e.message);
          applyBtn.disabled = false;
          applyBtn.textContent = 'Apply Selected Rules';
        }
      });

    } catch (e) {
      document.getElementById('batch-resolve-status').innerHTML =
        `<span style="color:#dc2626">AI analysis failed: ${e.message}</span>`;
    }
  }

  /* ─── Teach modal (Phase F) ─────────────────────────────────────────────── */
  const SCHEDULE_C_CATEGORIES = [
    'Advertising', 'Car and truck expenses', 'Commissions and fees', 'Contract labor',
    'Depreciation', 'Employee benefit programs', 'Insurance (other than health)',
    'Interest (mortgage)', 'Interest (other)', 'Legal and professional',
    'Office expense', 'Pension and profit-sharing', 'Rent - vehicles/equipment',
    'Rent - other', 'Repairs and maintenance', 'Supplies', 'Taxes and licenses',
    'Travel', 'Meals (50% limit)', 'Utilities', 'Wages', 'Other expenses',
    'Gross receipts', 'Returns and allowances', '(Exclude from Schedule C)',
  ];

  function openTeachModal(memo, entity, finding) {
    const existing = $('#teach-modal');
    if (existing) existing.remove();
    const modal = document.createElement('div');
    modal.id = 'teach-modal';
    modal.className = 'modal-overlay';
    modal.innerHTML = `
      <div class="modal">
        <h3>Teach a rule</h3>
        <label>Pattern type</label>
        <select id="teach-type">
          <option value="memo">Memo contains</option>
          <option value="account_name">Account name</option>
          <option value="vendor">Vendor</option>
        </select>
        <label>Pattern</label>
        <input type="text" id="teach-pattern" value="${(memo || '').replace(/"/g, '&quot;')}">
        <label>Schedule C category</label>
        <select id="teach-category">
          ${SCHEDULE_C_CATEGORIES.map(c => `<option value="${c === '(Exclude from Schedule C)' ? '' : c}">${c}</option>`).join('')}
        </select>
        <label>Scope (optional entity)</label>
        <input type="text" id="teach-scope" value="${(entity || '').replace(/"/g, '&quot;')}" placeholder="Leave blank for all entities">
        <div class="modal-actions">
          <button id="teach-cancel" class="btn">Cancel</button>
          <button id="teach-save" class="btn btn-primary">Save rule</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    $('#teach-cancel').addEventListener('click', () => modal.remove());
    $('#teach-save').addEventListener('click', async () => {
      const body = {
        pattern_type: $('#teach-type').value,
        pattern: $('#teach-pattern').value,
        schedule_c_category: $('#teach-category').value || null,
        entity_scope: $('#teach-scope').value || null,
      };
      try {
        const r = await fetchJSON('/api/rules/learn', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        modal.remove();
        toast(`Rule saved (id=${r.rule_id}) → ${body.schedule_c_category || 'Excluded'}. Findings updating…`);
        window.__suppressRefresh = Date.now() + 8000;
        setTimeout(() => { enhanceFindings(); refreshReadiness(); }, 2000);
      } catch (e) {
        toast(`Teach failed: ${e.message}`);
      }
    });
  }

  function toast(msg) {
    const t = document.createElement('div');
    t.className = 'toast';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3000);
  }

  /* ─── AI Settings section (Phase K) ────────────────────────────────────── */
  async function renderAISettings() {
    const root = $('#ai-settings-section');
    if (!root) return;
    try {
      const [budget, modelInfo] = await Promise.all([
        fetchJSON('/api/llm/budget'),
        fetchJSON('/api/llm/model'),
      ]);
      root.innerHTML = `
        <h3>AI Settings</h3>
        <div class="ai-panel">
          <div class="form-row">
            <div class="form-group">
              <label>Anthropic API key</label>
              <input type="password" id="ai-key" placeholder="sk-ant-...">
              <button id="ai-key-save" class="btn btn-sm">Save to Keychain</button>
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label>Model</label>
              <select id="ai-model">
                ${Object.entries(modelInfo.models).map(([id, info]) =>
                  `<option value="${id}" ${id === modelInfo.active_model ? 'selected' : ''}>${info.label} ($${info.in_per_mtok}/M in · $${info.out_per_mtok}/M out)</option>`
                ).join('')}
              </select>
            </div>
            <div class="form-group">
              <label>Monthly ceiling (USD)</label>
              <input type="number" id="ai-ceiling" value="${budget.ceiling_usd}" step="0.50" min="0">
            </div>
            <div class="form-group">
              <label>Period</label>
              <select id="ai-period">
                <option value="month" ${budget.period === 'month' ? 'selected' : ''}>Monthly</option>
                <option value="session" ${budget.period === 'session' ? 'selected' : ''}>Session</option>
              </select>
            </div>
          </div>
          <div class="ai-usage">
            <div>Used this period: <strong>$${budget.spent_usd.toFixed(4)}</strong> / $${budget.ceiling_usd.toFixed(2)} (${budget.percent_used}%)</div>
            <div class="bar-track"><div class="bar-fill" style="width:${budget.percent_used}%"></div></div>
          </div>
          <button id="ai-save" class="btn btn-primary">Save AI Settings</button>
          <div id="ai-status" class="muted"></div>
        </div>
      `;
      $('#ai-key-save').addEventListener('click', async () => {
        const key = $('#ai-key').value.trim();
        if (!key) return;
        try {
          await fetchJSON('/api/llm/key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: key }),
          });
          $('#ai-status').textContent = 'Key stored in Keychain ✓';
          $('#ai-key').value = '';
        } catch (e) {
          $('#ai-status').textContent = `Error: ${e.message}`;
        }
      });
      $('#ai-save').addEventListener('click', async () => {
        try {
          await fetchJSON('/api/llm/model', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_id: $('#ai-model').value }),
          });
          await fetchJSON('/api/llm/budget', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              ceiling_usd: parseFloat($('#ai-ceiling').value),
              period: $('#ai-period').value,
            }),
          });
          $('#ai-status').textContent = 'Saved ✓';
          renderAISettings();
        } catch (e) {
          $('#ai-status').textContent = `Error: ${e.message}`;
        }
      });
    } catch (e) {
      root.innerHTML = `<p class="muted">AI Settings unavailable: ${e.message}</p>`;
    }
  }

  /* ─── Filing tab ────────────────────────────────────────────────────────── */
  function wireFilingTab() {
    const btn = $('#btn-generate-filing');
    if (btn) {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        btn.textContent = 'Generating…';
        try {
          const year = currentYear();
          const result = await fetchJSON(`/api/filing/generate/${year}`, { method: 'POST' });
          $('#filing-result').innerHTML = `
            <div class="filing-result ${result.watermarked ? 'draft' : 'final'}">
              <p>PDF generated at: <code>${result.path}</code></p>
              <p>Pages: ${result.pages} · Gross: $${Number(result.gross).toLocaleString()} · Net: $${Number(result.net_profit).toLocaleString()}</p>
              <p>${result.watermarked ? '<strong>DRAFT watermark applied</strong> — clear all BLOCKED/REQUIRED findings and reach 95% readiness to remove it.' : 'No watermark — ready for filing review.'}</p>
            </div>
          `;
        } catch (e) {
          $('#filing-result').innerHTML = `<p class="error">Generation failed: ${e.message}</p>`;
        } finally {
          btn.disabled = false;
          btn.textContent = 'Generate IRS Draft PDF';
        }
      });
    }
    const extBtn = $('#btn-generate-extension');
    if (extBtn) {
      extBtn.addEventListener('click', async () => {
        if (!confirm('Generate a Form 4868 extension request?\n\nThis gives you until October 15 to FILE. It does NOT extend the payment deadline — any estimated balance due should still be paid by April 15 to avoid late-payment penalty.')) return;
        extBtn.disabled = true;
        extBtn.textContent = 'Generating…';
        try {
          const year = currentYear();
          const result = await fetchJSON(`/api/filing/extension/${year}`, { method: 'POST' });
          $('#filing-result').innerHTML = `
            <div class="filing-result draft">
              <h4>Form 4868 Extension generated</h4>
              <p>Saved to: <code>${result.path}</code></p>
              <p>Est. total tax: $${Number(result.estimated_total_tax || 0).toLocaleString()}<br>
                 Payments made: $${Number(result.total_payments || 0).toLocaleString()}<br>
                 <strong>Balance due: $${Number(result.balance_due || 0).toLocaleString()}</strong></p>
              <p class="muted">${result.instructions || ''}</p>
            </div>
          `;
        } catch (e) {
          $('#filing-result').innerHTML = `<p class="error">Extension generation failed: ${e.message}</p>`;
        } finally {
          extBtn.disabled = false;
          extBtn.textContent = 'Generate Extension (Form 4868)';
        }
      });
    }
    const stateBtn = $('#btn-generate-state');
    if (stateBtn) {
      stateBtn.addEventListener('click', async () => {
        stateBtn.disabled = true;
        stateBtn.textContent = 'Generating…';
        try {
          const year = currentYear();
          const result = await fetchJSON(`/api/filing/state/${year}`, { method: 'POST' });
          if (result.error) throw new Error(result.error);
          $('#filing-result').innerHTML = `
            <div class="filing-result draft">
              <h4>${result.form} — ${result.state_name} Draft</h4>
              <p>Saved to: <code>${result.path}</code></p>
              <p>State taxable income: $${Number(result.state_taxable_income || 0).toLocaleString()}<br>
                 State tax before payments: $${Number(result.state_tax_before_payments || 0).toLocaleString()}<br>
                 <strong>State tax owed: $${Number(result.state_tax_owed || 0).toLocaleString()}</strong></p>
              <p class="muted">${result.instructions || ''}</p>
            </div>
          `;
        } catch (e) {
          $('#filing-result').innerHTML = `<p class="error">State filing failed: ${e.message}</p>`;
        } finally {
          stateBtn.disabled = false;
          stateBtn.textContent = 'Generate State Filing PDF';
        }
      });
    }
    const stateExtBtn = $('#btn-generate-state-extension');
    if (stateExtBtn) {
      stateExtBtn.addEventListener('click', async () => {
        if (!confirm('Generate a state extension request (CO DR 0158-I)?\n\nColorado auto-grants a 6-month filing extension if at least 90% of tax liability is paid by April 15. File this only if you owe a balance.')) return;
        stateExtBtn.disabled = true;
        stateExtBtn.textContent = 'Generating…';
        try {
          const year = currentYear();
          const result = await fetchJSON(`/api/filing/state-extension/${year}`, { method: 'POST' });
          if (result.error) throw new Error(result.error);
          $('#filing-result').innerHTML = `
            <div class="filing-result draft">
              <h4>${result.form} — ${result.state} Extension</h4>
              <p>Saved to: <code>${result.path}</code></p>
              <p>Est. total tax: $${Number(result.estimated_total_tax || 0).toLocaleString()}<br>
                 Payments made: $${Number(result.total_payments || 0).toLocaleString()}<br>
                 <strong>Balance due: $${Number(result.balance_due || 0).toLocaleString()}</strong></p>
              <p class="muted">${result.instructions || ''}</p>
            </div>
          `;
        } catch (e) {
          $('#filing-result').innerHTML = `<p class="error">State extension failed: ${e.message}</p>`;
        } finally {
          stateExtBtn.disabled = false;
          stateExtBtn.textContent = 'Generate State Extension';
        }
      });
    }
  }

  /* ─── Overview enhancements: tax cards + strategy strip ─────────────────── */
  async function renderOverviewEnhancements() {
    const kpiRow = $('#kpi-row');
    if (!kpiRow) return;
    const year = currentYear();
    try {
      const [data, strategy] = await Promise.all([
        fetchJSON(`/api/overview/${year}`),
        fetchJSON(`/api/strategy/${year}`).catch(() => ({ recommendations: [] })),
      ]);
      const k = data.kpis || {};
      const fmt = (n) => n == null ? '<span class="na">—</span>' : '$' + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });

      // Insert / update the "What you owe" card row below the existing KPI row
      let owedCard = $('#owed-card');
      if (!owedCard) {
        owedCard = document.createElement('div');
        owedCard.id = 'owed-card';
        owedCard.className = 'owed-card';
        kpiRow.insertAdjacentElement('afterend', owedCard);
      }
      const stateTax = k.state_tax || {};
      const stateLabel = stateTax.state_name || stateTax.state || 'State';
      const totalOwed = (k.estimated_tax_owed || 0) + (k.state_tax_owed || 0);
      const stateNote = stateTax.status === 'no_income_tax'
        ? `${stateLabel}: no state income tax`
        : stateTax.status === 'unsupported'
          ? `${stateLabel} (${stateTax.state}): not yet supported — compute manually`
          : stateTax.status === 'ok'
            ? `${stateLabel}: ${Math.round((stateTax.flat_rate || 0) * 10000) / 100}% flat rate`
            : '';

      owedCard.innerHTML = `
        <div class="owed-head">
          <h3>Estimated Total Tax Owed</h3>
          <span class="muted">Filing status: ${k.filing_status || 'MFJ'} · Marginal rate: ${Math.round((k.marginal_rate || 0) * 100)}% · ${stateNote}</span>
        </div>
        <div class="owed-big">${fmt(totalOwed)}</div>
        <div class="owed-subtitle">federal + state, after subtracting payments already made and withholdings</div>
        <div class="owed-splits">
          <div class="owed-split federal">
            <div class="owed-split-label">Federal (IRS)</div>
            <div class="owed-split-value">${fmt(k.estimated_tax_owed)}</div>
          </div>
          <div class="owed-split state">
            <div class="owed-split-label">State (${stateTax.state || 'n/a'})</div>
            <div class="owed-split-value">${fmt(k.state_tax_owed)}</div>
          </div>
        </div>
        <div class="owed-grid">
          <div>
            <div class="owed-label">Self-Employment Tax</div>
            <div class="owed-value">${fmt(k.self_employment_tax)}</div>
            <div class="muted">15.3% of 92.35% of net SE earnings (Social Security + Medicare)</div>
          </div>
          <div>
            <div class="owed-label">Federal Income Tax</div>
            <div class="owed-value">${fmt(k.federal_income_tax)}</div>
            <div class="muted">rough estimate at ${Math.round((k.marginal_rate || 0) * 100)}% marginal rate</div>
          </div>
          <div>
            <div class="owed-label">State Income Tax (${stateTax.state || 'n/a'})</div>
            <div class="owed-value">${fmt(k.state_tax_before_payments)}</div>
            <div class="muted">${stateTax.rate_source || stateNote}</div>
          </div>
          <div>
            <div class="owed-label">Federal payments made</div>
            <div class="owed-value positive">−${fmt((k.estimated_payments_made || 0) + (k.spouse_federal_withholding || 0))}</div>
            <div class="muted">${fmt(k.estimated_payments_made)} IRS est. · ${fmt(k.spouse_federal_withholding)} spouse W/H</div>
          </div>
          <div>
            <div class="owed-label">QBI Deduction</div>
            <div class="owed-value">${fmt(k.qbi_deduction)}</div>
            <div class="muted">20% of qualified business income</div>
          </div>
          <div>
            <div class="owed-label">Standard Deduction</div>
            <div class="owed-value">${fmt(k.standard_deduction)}</div>
            <div class="muted">${k.filing_status || 'MFJ'} filing status</div>
          </div>
        </div>
      `;

      // Strategy recommendations strip below the owed card
      let recs = $('#recs-strip');
      if (!recs) {
        recs = document.createElement('div');
        recs.id = 'recs-strip';
        recs.className = 'recs-strip';
        owedCard.insertAdjacentElement('afterend', recs);
      }
      const rlist = strategy.recommendations || [];
      if (rlist.length === 0) {
        recs.innerHTML = `<h3>Strategy Recommendations</h3><p class="muted">No recommendations yet — parse more data or adjust your entities in Settings.</p>`;
      } else {
        const totalSavings = strategy.total_estimated_savings || 0;
        recs.innerHTML = `
          <div class="recs-head">
            <h3>Strategy Recommendations</h3>
            <div class="muted">${rlist.length} ideas · Est. combined savings: <strong>${fmt(totalSavings)}</strong></div>
          </div>
          <div class="recs-grid">
            ${rlist.map(r => `
              <div class="rec-card">
                <div class="rec-title">${r.title || r.id || 'Untitled'}</div>
                <div class="rec-savings">${fmt(r.estimated_savings)}</div>
                <div class="rec-category muted">${r.category || ''}</div>
                <div class="rec-summary">${r.summary || ''}</div>
              </div>
            `).join('')}
          </div>
        `;
      }
    } catch (e) {
      console.warn('overview enhancements failed:', e);
    }
  }

  /* ─── Shutdown button ────────────────────────────────────────────────────── */
  function wireShutdown() {
    const btn = $('#btn-shutdown');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      if (!confirm('Shut down CPAgent and disable auto-restart?\n\nThis will:\n  • Unload the LaunchAgent (if installed) so launchd won\'t respawn it\n  • Stop the Flask server\n\nYou can restart by double-clicking the CPAgent shortcut.')) return;
      btn.disabled = true;
      btn.textContent = 'Shutting down…';
      try {
        const r = await fetch('/api/shutdown', { method: 'POST' });
        const body = await r.json().catch(() => ({}));
        document.body.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif;color:#475569">
          <div style="text-align:center">
            <h1 style="margin:0 0 12px">CPAgent is shut down</h1>
            <p style="margin:0 0 8px">${body.unloaded_launchagents && body.unloaded_launchagents.length ? 'LaunchAgent unloaded: ' + body.unloaded_launchagents.join(', ') : 'No LaunchAgent was running.'}</p>
            <p class="muted">Double-click <code>CPAgent.command</code> to start again.</p>
          </div>
        </div>`;
      } catch (e) {
        // Expected: fetch fails because server is dying. Show the same message.
        document.body.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif;color:#475569">
          <div style="text-align:center">
            <h1 style="margin:0 0 12px">CPAgent is shut down</h1>
            <p class="muted">Double-click <code>CPAgent.command</code> to start again.</p>
          </div>
        </div>`;
      }
    });
  }

  /* ─── Real-time refresh on SSE events ───────────────────────────────────── */
  let _refreshDebounce = null;
  async function refreshAll() {
    clearTimeout(_refreshDebounce);
    _refreshDebounce = setTimeout(async () => {
      const year = currentYear();
      try {
        // Invalidate the server cache and force a re-parse so all cached endpoints
        // return fresh data on the next tick.
        await fetch(`/api/refresh/${year}`, { method: 'POST' });
      } catch (e) { /* ignore, SSE will retry */ }

      refreshReadiness();
      renderOverviewEnhancements();

      // Re-fetch data for whichever tab is currently active so the user sees
      // the update without needing to click anything. We dispatch a synthetic
      // change event on the year selector — the legacy app.js treats that as
      // "reload everything for this year" which is exactly what we want.
      const sel = $('#year-select');
      if (sel) sel.dispatchEvent(new Event('change'));

      // Findings tab has its own render path layered on top of app.js
      const activeTab = document.querySelector('.tab-content.active');
      if (activeTab && activeTab.id === 'tab-findings') enhanceFindings();
    }, 350);
  }

  /* ─── Boot ───────────────────────────────────────────────────────────────── */
  function boot() {
    refreshReadiness();
    renderOverviewEnhancements();
    wireFilingTab();
    wireShutdown();

    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        if (tab === 'findings') setTimeout(enhanceFindings, 200);
        if (tab === 'settings') setTimeout(renderAISettings, 200);
        if (tab === 'overview') setTimeout(renderOverviewEnhancements, 200);
      });
    });

    // SSE: real-time refresh on any data change
    try {
      const es = new EventSource('/api/stream');
      ['file_changed', 'parse_complete', 'rule_learned'].forEach(evt => {
        es.addEventListener(evt, refreshAll);
      });
      es.addEventListener('new_year_detected', () => {
        refreshYearList();
        refreshAll();
      });
      es.addEventListener('error', () => {
        // SSE drops on idle; reconnect automatically via EventSource's built-in retry
      });
    } catch (e) { /* SSE optional */ }

    // Periodic refresh fallback (every 10s — near real time)
    setInterval(() => {
      refreshReadiness();
      renderOverviewEnhancements();
      refreshYearList();
    }, 10000);
  }

  /* ─── Auto-detect new year folders ─────────────────────────────────────── */
  async function refreshYearList() {
    try {
      const years = await fetchJSON('/api/years');
      const sel = $('#year-select');
      if (!sel || !Array.isArray(years)) return;
      const current = sel.value;
      const existing = new Set(Array.from(sel.options).map(o => o.value));
      const incoming = new Set(years.map(y => String(y.year)));
      // Only mutate if a year was added or removed
      let changed = false;
      for (const y of years) {
        if (!existing.has(String(y.year))) { changed = true; break; }
      }
      if (existing.size !== incoming.size) changed = true;
      if (!changed) return;
      sel.innerHTML = years.map(y => `<option value="${y.year}">${y.year}</option>`).join('');
      if (existing.has(current)) sel.value = current;
      sel.dispatchEvent(new Event('change'));
    } catch (e) { /* ignore */ }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
