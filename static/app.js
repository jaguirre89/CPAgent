/* Tax Dashboard — Frontend Logic */

const state = {
  config: null,
  currentYear: null,
  currentTab: 'overview',
  data: null,
  notes: {},
  sseConnected: false,
  charts: {},
  noteTimer: null,
};

// ─── Utilities ─────────────────────────────────────────────────────────────

function $(sel, ctx) { return (ctx || document).querySelector(sel); }
function $$(sel, ctx) { return Array.from((ctx || document).querySelectorAll(sel)); }

function fmt(n) {
  if (n == null || isNaN(n)) return '—';
  const abs = Math.abs(n);
  const str = abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return n < 0 ? `($${str})` : `$${str}`;
}

function fmtInt(n) {
  if (n == null || isNaN(n)) return '—';
  return n.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function showToast(msg, type = 'success') {
  const container = $('#toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

async function api(path, opts = {}) {
  try {
    const res = await fetch(`/api/${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    });
    return await res.json();
  } catch (e) {
    console.error(`API error: ${path}`, e);
    showToast(`API error: ${e.message}`, 'error');
    return null;
  }
}

// ─── Initialization ────────────────────────────────────────────────────────

async function init() {
  const cfg = await api('config');
  if (!cfg || cfg.setup_required) {
    showSetup();
    return;
  }
  state.config = cfg;

  // Populate year selector
  const years = await api('years');
  const yearSel = $('#year-select');
  yearSel.innerHTML = '';
  if (years && years.length) {
    years.forEach(y => {
      const opt = document.createElement('option');
      opt.value = y.year;
      opt.textContent = `${y.year} Tax Year`;
      yearSel.appendChild(opt);
    });
    state.currentYear = cfg.current_year || years[0].year;
    yearSel.value = state.currentYear;
  }

  yearSel.addEventListener('change', () => {
    state.currentYear = parseInt(yearSel.value);
    state.data = null;
    loadTab(state.currentTab);
  });

  // Tab buttons
  $$('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      $$('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      $$('.tab-content').forEach(tc => tc.classList.remove('active'));
      const tabId = btn.dataset.tab;
      $(`#tab-${tabId}`).classList.add('active');
      state.currentTab = tabId;
      loadTab(tabId);
    });
  });

  // New year button
  $('#btn-new-year').addEventListener('click', createNewYear);
  // Refresh button
  $('#btn-refresh').addEventListener('click', async () => {
    state.data = null;
    await api(`refresh/${state.currentYear}`, { method: 'POST' });
    loadTab(state.currentTab);
    showToast('Data refreshed');
  });

  // SSE
  connectSSE();

  // Load default tab
  $('#setup-overlay').style.display = 'none';
  loadTab('overview');
}

// ─── Setup ─────────────────────────────────────────────────────────────────

function showSetup() {
  $('#setup-overlay').style.display = 'flex';
  const defaultRoot = window.location.pathname.includes('tax_dashboard')
    ? window.location.pathname.replace('/tax_dashboard/', '/')
    : '/Users/batman/Documents/01_PERSONAL/00_Tax Agent';

  $('#setup-root').value = defaultRoot;
}

async function saveSetup() {
  const root = $('#setup-root').value.trim();
  const filing = $('#setup-filing').value;
  const entityName = $('#setup-entity-name').value.trim();
  const entityType = $('#setup-entity-type').value;
  const entityState = $('#setup-entity-state').value.trim();
  const entityAccounting = $('#setup-entity-accounting').value;

  if (!root || !entityName) {
    showToast('Root path and entity name are required', 'warning');
    return;
  }

  const cfg = {
    root_path: root,
    filing_status: filing,
    entities: [{
      name: entityName,
      entity_type: entityType,
      state: entityState,
      accounting_system: entityAccounting,
      ein: '',
      folder_name: '',
      bank_accounts: [],
      credit_cards: [],
      is_parent: false,
    }],
    current_year: new Date().getFullYear() - 1,
    preferences: {
      estimated_marginal_rate: 0.22,
      home_office_total: 0,
    },
    csv_mappings: {},
  };

  await api('config', { method: 'POST', body: JSON.stringify(cfg) });
  location.reload();
}

// ─── Data Loading ──────────────────────────────────────────────────────────

async function ensureData() {
  if (!state.data) {
    $('#loading-indicator').style.display = 'block';
    state.data = await api(`data/${state.currentYear}`);
    state.notes = state.data?.notes || {};
    $('#loading-indicator').style.display = 'none';

    // Update warning badge
    const errors = state.data?.parse_errors || [];
    const badge = $('#error-badge');
    if (errors.length) {
      badge.textContent = `${errors.length} errors`;
      badge.style.display = 'inline';
    } else {
      badge.style.display = 'none';
    }
  }
  return state.data;
}

async function loadTab(tabId) {
  const data = await ensureData();
  if (!data) return;

  switch (tabId) {
    case 'overview': renderOverview(data); break;
    case 'schedule-c': await loadScheduleCNotes(state.currentYear); renderScheduleC(data); break;
    case 'findings': renderFindings(data); break;
    case 'bank-recon': renderBankRecon(data); break;
    case 'checklist': renderChecklist(data); break;
    case 'strategy': renderStrategy(data); break;
    case 'settings': renderSettings(); break;
  }
}

// ─── Overview Tab ──────────────────────────────────────────────────────────

function renderOverview(data) {
  const kpis = data.overview?.kpis || {};
  const charts = data.overview?.charts || {};

  // KPI cards
  const kpiHtml = `
    <div class="kpi-card blue">
      <div class="kpi-label">Gross Receipts</div>
      <div class="kpi-value">${kpis.gross_receipts ? fmt(kpis.gross_receipts) : '<span class="na">Not yet available</span>'}</div>
    </div>
    <div class="kpi-card green">
      <div class="kpi-label">Total Deductions</div>
      <div class="kpi-value">${kpis.total_deductions ? fmt(kpis.total_deductions) : '<span class="na">Not yet available</span>'}</div>
    </div>
    <div class="kpi-card ${kpis.net_profit >= 0 ? 'blue' : 'red'}">
      <div class="kpi-label">Net Profit</div>
      <div class="kpi-value">${kpis.net_profit != null ? fmt(kpis.net_profit) : '<span class="na">Not yet available</span>'}</div>
    </div>
    <div class="kpi-card yellow">
      <div class="kpi-label">Est. SE Tax</div>
      <div class="kpi-value">${kpis.se_tax_estimate ? fmt(kpis.se_tax_estimate) : '<span class="na">Not yet available</span>'}</div>
    </div>
    <div class="kpi-card green">
      <div class="kpi-label">Home Office Deduction</div>
      <div class="kpi-value">${kpis.home_office_deduction ? fmt(kpis.home_office_deduction) : '<span class="na">Not yet available</span>'}</div>
    </div>
    <div class="kpi-card green">
      <div class="kpi-label">Vehicle Deduction</div>
      <div class="kpi-value">${kpis.vehicle_deduction ? fmt(kpis.vehicle_deduction) : '<span class="na">Not yet available</span>'}</div>
    </div>
    <div class="kpi-card purple">
      <div class="kpi-label">Spouse W-2 Income</div>
      <div class="kpi-value">${kpis.spouse_income ? fmt(kpis.spouse_income) : '<span class="na">Not yet available</span>'}</div>
    </div>
    <div class="kpi-card red">
      <div class="kpi-label">Unclassified Expenses</div>
      <div class="kpi-value">${kpis.unclassified_total ? fmt(kpis.unclassified_total) : '$0.00'}</div>
    </div>
  `;
  $('#kpi-row').innerHTML = kpiHtml;

  // Charts
  renderRevenueChart(charts.revenue_by_entity || {});
  renderExpenseChart(charts.expense_categories || {});
}

function renderRevenueChart(data) {
  const canvas = $('#chart-revenue');
  if (state.charts.revenue) state.charts.revenue.destroy();

  const labels = Object.keys(data);
  const values = Object.values(data);
  const colors = ['#3b82f6', '#8b5cf6', '#ec4899', '#f59e0b'];

  state.charts.revenue = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: colors.slice(0, labels.length) }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { font: { size: 12 } } },
        tooltip: { callbacks: { label: ctx => `${ctx.label}: ${fmt(ctx.raw)}` } },
      },
    },
  });
}

function renderExpenseChart(data) {
  const canvas = $('#chart-expenses');
  if (state.charts.expenses) state.charts.expenses.destroy();

  const entries = Object.entries(data).slice(0, 10);
  const labels = entries.map(e => e[0]);
  const values = entries.map(e => e[1]);

  state.charts.expenses = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: '#3b82f6',
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => fmt(ctx.raw) } },
      },
      scales: {
        x: { ticks: { callback: v => '$' + (v / 1000).toFixed(0) + 'k' } },
      },
    },
  });
}

// ─── Schedule C Tab ────────────────────────────────────────────────────────

function renderScheduleC(data) {
  const scData = data.schedule_c || {};
  const schedules = scData.schedules || [];
  const combined = scData.combined || {};
  const container = $('#schedule-c-content');

  if (!schedules.length) {
    container.innerHTML = '<div class="loading">No Schedule C data available. Upload P&L exports to entity folders.</div>';
    return;
  }

  let html = '';

  // Combined summary
  html += `
    <div class="kpi-row" style="margin-bottom:20px">
      <div class="kpi-card blue"><div class="kpi-label">Combined Revenue</div><div class="kpi-value">${fmt(combined.total_revenue)}</div></div>
      <div class="kpi-card green"><div class="kpi-label">Combined Expenses</div><div class="kpi-value">${fmt(combined.total_expenses)}</div></div>
      <div class="kpi-card ${combined.net_profit >= 0 ? 'blue' : 'red'}"><div class="kpi-label">Combined Net Profit</div><div class="kpi-value">${fmt(combined.net_profit)}</div></div>
      <div class="kpi-card yellow"><div class="kpi-label">Est. SE Tax</div><div class="kpi-value">${fmt(combined.se_tax)}</div></div>
    </div>
  `;

  // Per-entity Schedule C
  schedules.forEach((sc, idx) => {
    html += `<h3 class="section-header">Schedule C — ${sc.entity} (${sc.entity_type || 'LLC'})</h3>`;
    html += `<table class="data-table" style="margin-bottom:20px"><thead><tr>
      <th style="width:60px">Line</th><th>Description</th><th class="num">Amount</th><th>Source Accounts</th><th style="width:200px">Notes</th>
    </tr></thead><tbody>`;

    let currentSection = '';
    for (const line of sc.lines) {
      if (line.section !== currentSection) {
        currentSection = line.section;
        const sectionLabel = currentSection === 'income' ? 'Part I — Income' :
                             currentSection === 'expenses' ? 'Part II — Expenses' : 'Net Profit / Loss';
        html += `<tr><td colspan="5" style="background:#e2e8f0;font-weight:700;font-size:12px;padding:6px 14px">${sectionLabel}</td></tr>`;
      }

      const isComputed = line.computed;
      const isIntercompany = line.sources?.some(s => s.account?.toLowerCase().includes('management fee'));
      const rowClass = isComputed ? 'computed' : (isIntercompany ? 'intercompany' : '');
      const amountClass = line.amount < 0 ? 'num negative' : 'num';

      const sources = (line.sources || []).map(s => {
        const flag = s.flag ? ` <span class="badge BLOCKING">${s.flag}</span>` : '';
        return `${s.account}: ${fmt(s.amount)}${flag}`;
      }).join('<br>');

      const noteKey = `sc_${idx}_${line.line}`;
      const noteVal = state.notes[noteKey] || '';
      const scNoteKey = `${sc.entity}__${line.line}`;
      const scNoteVal = (state._scNotes || {})[scNoteKey] || noteVal;

      html += `<tr class="${rowClass}">
        <td>${line.line}</td>
        <td>${line.description}</td>
        <td class="${amountClass}">${line.amount !== 0 ? fmt(line.amount) : ''}</td>
        <td style="font-size:11px">${sources}</td>
        <td><textarea class="note-input sc-note" data-note-key="${noteKey}" data-entity="${sc.entity}" data-line="${line.line}">${scNoteVal}</textarea></td>
      </tr>`;
    }

    // Unmapped accounts
    if (sc.unmapped_accounts?.length) {
      html += `<tr><td colspan="5" style="background:#fef2f2;font-weight:700;font-size:12px;padding:6px 14px">Unmapped / Excluded Accounts</td></tr>`;
      sc.unmapped_accounts.forEach(ua => {
        html += `<tr class="excluded"><td></td><td>${ua.account}</td><td class="num">${fmt(ua.amount)}</td><td>${ua.type}</td><td></td></tr>`;
      });
    }

    html += '</tbody></table>';
  });

  container.innerHTML = html;
  attachNoteListeners();
  attachScheduleCNoteListeners();
}

function attachScheduleCNoteListeners() {
  document.querySelectorAll('.sc-note').forEach(ta => {
    let debounce = null;
    ta.addEventListener('input', () => {
      clearTimeout(debounce);
      debounce = setTimeout(async () => {
        const entity = ta.dataset.entity;
        const lineNum = ta.dataset.line;
        const year = state.currentYear;
        try {
          await fetch(`/api/schedule-c-notes/${year}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entity, line_number: lineNum, note_text: ta.value }),
          });
        } catch (e) { console.error('SC note save failed', e); }
      }, 800);
    });
  });
}

// ─── Home Office Calculator ───────────────────────────────────────────────

async function loadHomeOfficeSection(year) {
  const section = document.getElementById('home-office-section');
  if (!section) return;

  try {
    const resp = await fetch(`/api/home-office/${year}`);
    if (!resp.ok) throw new Error('Failed to load');
    const data = await resp.json();

    let html = '';

    // Show detected leases
    if (data.detected_leases && data.detected_leases.length) {
      html += `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px">
        Detected lease files: ${data.detected_leases.map(l => `<strong>${l.file}</strong>${l.address_hint ? ` (${l.address_hint})` : ''}`).join(', ')}
      </div>`;
    }

    // Show existing periods
    if (data.periods && data.periods.length) {
      if (data.move_detected) {
        html += `<div style="background:#fefce8;border:1px solid #fde68a;border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:12px">
          <strong>Move detected:</strong> ${data.period_count} addresses on file for ${year}. Deduction is pro-rated by period.
        </div>`;
      }

      html += `<table class="data-table" style="margin-bottom:12px"><thead><tr>
        <th>Address</th><th>Dates</th><th>Method</th><th class="num">Office / Total Sq Ft</th>
        <th class="num">Monthly Rent</th><th class="num">Deduction</th><th style="width:60px"></th>
      </tr></thead><tbody>`;

      data.periods.forEach(p => {
        html += `<tr>
          <td>${p.address}</td>
          <td style="font-size:11px">${p.months_in_year} months (in ${year})</td>
          <td>${p.method === 'simplified' ? 'Simplified' : 'Regular'}</td>
          <td class="num">${p.office_sqft || '—'} / ${p.total_sqft || '—'} (${p.business_pct}%)</td>
          <td class="num">${p.breakdown.rent ? fmt(p.breakdown.rent / (p.months_in_year || 1)) + '/mo' : '—'}</td>
          <td class="num"><strong>${fmt(p.chosen_deduction)}</strong></td>
          <td><button class="btn btn-sm ho-delete" data-id="${p.period_id}" style="color:var(--negative)">✕</button></td>
        </tr>`;
      });

      html += `</tbody></table>`;
      html += `<div style="font-size:14px;font-weight:600;margin-bottom:16px">
        Total Home Office Deduction: <span style="color:var(--positive)">${fmt(data.total_deduction)}</span>
        <span style="font-size:11px;font-weight:400;color:var(--text-secondary);margin-left:8px">→ Schedule C Line 30</span>
      </div>`;
    }

    // Add period form
    html += `
      <div style="background:var(--bg-primary);border-radius:8px;padding:16px;margin-top:8px">
        <h4 style="margin:0 0 12px;font-size:13px">${data.periods && data.periods.length ? 'Add another address (mid-year move)' : 'Add home office details'}</h4>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <div class="form-group"><label style="font-size:11px">Address</label>
            <input type="text" id="ho-address" placeholder="e.g., 123 Example St, CO Springs" value="${(data.detected_leases || [])[data.periods?.length || 0]?.address_hint || ''}"></div>
          <div class="form-group"><label style="font-size:11px">Lease file (in Home Office folder)</label>
            <input type="text" id="ho-lease-file" placeholder="e.g., 123 Example St- Signed lease.pdf" value="${(data.detected_leases || [])[data.periods?.length || 0]?.file || ''}"></div>
          <div class="form-group"><label style="font-size:11px">Start date at this address</label>
            <input type="date" id="ho-start" value="${year}-01-01"></div>
          <div class="form-group"><label style="font-size:11px">End date at this address</label>
            <input type="date" id="ho-end" value="${year}-12-31"></div>
          <div class="form-group"><label style="font-size:11px">Total home sq ft</label>
            <input type="number" id="ho-total-sqft" placeholder="e.g., 1800"></div>
          <div class="form-group"><label style="font-size:11px">Office sq ft</label>
            <input type="number" id="ho-office-sqft" placeholder="e.g., 150"></div>
          <div class="form-group"><label style="font-size:11px">Monthly rent</label>
            <input type="number" id="ho-rent" step="0.01" placeholder="e.g., 2200"></div>
          <div class="form-group"><label style="font-size:11px">Monthly utilities</label>
            <input type="number" id="ho-utilities" step="0.01" placeholder="e.g., 250"></div>
          <div class="form-group"><label style="font-size:11px">Monthly internet</label>
            <input type="number" id="ho-internet" step="0.01" placeholder="e.g., 80"></div>
          <div class="form-group"><label style="font-size:11px">Monthly insurance</label>
            <input type="number" id="ho-insurance" step="0.01" placeholder="e.g., 30"></div>
          <div class="form-group"><label style="font-size:11px">Method</label>
            <select id="ho-method">
              <option value="simplified">Simplified ($5/sqft, max $1,500)</option>
              <option value="regular">Regular (actual expenses × business %)</option>
            </select></div>
        </div>
        <button id="ho-save" class="btn btn-primary" style="margin-top:12px">Save period</button>
      </div>
    `;

    section.innerHTML = html;

    // Wire save button
    document.getElementById('ho-save').addEventListener('click', async () => {
      const body = {
        address: document.getElementById('ho-address').value,
        lease_file: document.getElementById('ho-lease-file').value,
        start_date: document.getElementById('ho-start').value,
        end_date: document.getElementById('ho-end').value,
        total_sqft: parseFloat(document.getElementById('ho-total-sqft').value) || null,
        office_sqft: parseFloat(document.getElementById('ho-office-sqft').value) || null,
        monthly_rent: parseFloat(document.getElementById('ho-rent').value) || null,
        monthly_utilities: parseFloat(document.getElementById('ho-utilities').value) || null,
        monthly_internet: parseFloat(document.getElementById('ho-internet').value) || null,
        monthly_insurance: parseFloat(document.getElementById('ho-insurance').value) || null,
        method: document.getElementById('ho-method').value,
      };
      try {
        await fetch(`/api/home-office/${year}`, {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
        });
        state.data = null;
        loadHomeOfficeSection(year);
      } catch (e) { console.error('Home office save failed', e); }
    });

    // Wire delete buttons
    section.querySelectorAll('.ho-delete').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm('Remove this home office period?')) return;
        await fetch(`/api/home-office/${year}/${btn.dataset.id}`, { method: 'DELETE' });
        state.data = null;
        loadHomeOfficeSection(year);
      });
    });

  } catch (e) {
    section.innerHTML = '<div style="color:var(--text-secondary);font-size:12px">Home office data unavailable.</div>';
  }
}

// Load SC notes from DB when data loads
async function loadScheduleCNotes(year) {
  try {
    const resp = await fetch(`/api/schedule-c-notes/${year}`);
    if (!resp.ok) return;
    const notes = await resp.json();
    state._scNotes = {};
    for (const n of notes) {
      state._scNotes[`${n.entity}__${n.line_number}`] = n.note_text;
    }
  } catch (e) { /* ignore */ }
}

// ─── Findings Tab ──────────────────────────────────────────────────────────

function renderFindings(data) {
  if (window.__enhancementsOwnFindings) return;
  const findings = data.findings || [];
  const container = $('#findings-content');

  // Update tab badge
  const blocking = findings.filter(f => f.priority === 'BLOCKING' || f.priority === 'CRITICAL').length;
  const badge = $('#findings-badge');
  if (blocking) {
    badge.textContent = blocking;
    badge.className = 'badge CRITICAL';
    badge.style.display = 'inline';
  } else {
    badge.style.display = 'none';
  }

  if (!findings.length) {
    container.innerHTML = '<div class="loading">No findings. All data appears clean.</div>';
    return;
  }

  let html = `<table class="data-table"><thead><tr>
    <th style="width:40px">#</th><th>Topic</th><th style="width:80px">Priority</th><th>Finding</th><th style="width:200px">Notes</th>
  </tr></thead><tbody>`;

  findings.forEach((f, i) => {
    const noteKey = `finding_${i}`;
    const noteVal = state.notes[noteKey] || '';
    html += `<tr>
      <td>${i + 1}</td>
      <td>${f.topic || ''}</td>
      <td><span class="badge ${f.priority}">${f.priority}</span></td>
      <td style="font-size:12px">${f.finding || ''}</td>
      <td><textarea class="note-input" data-note-key="${noteKey}">${noteVal}</textarea></td>
    </tr>`;
  });

  html += '</tbody></table>';
  container.innerHTML = html;
  attachNoteListeners();
}

// ─── Bank Reconciliation Tab ───────────────────────────────────────────────

function renderBankRecon(data) {
  const bankData = data.bank_recon || {};
  const accounts = bankData.accounts || [];
  const container = $('#bank-recon-content');

  if (!accounts.length) {
    container.innerHTML = '<div class="loading">No bank data available. Upload bank statements (PDFs/CSVs) to entity folders.</div>';
    return;
  }

  // Account selector
  let html = `<div class="account-selector"><select id="bank-account-select">`;
  accounts.forEach((acct, i) => {
    html += `<option value="${i}">${acct.name} — <span class="status ${acct.status}">${acct.status}</span></option>`;
  });
  html += `</select></div><div id="bank-account-detail"></div>`;
  container.innerHTML = html;

  const select = $('#bank-account-select');
  const renderAccount = () => {
    const acct = accounts[parseInt(select.value)];
    renderBankAccount(acct);
  };
  select.addEventListener('change', renderAccount);
  renderAccount();
}

function renderBankAccount(acct) {
  const detail = $('#bank-account-detail');
  if (!acct) { detail.innerHTML = ''; return; }

  const months = acct.months || [];
  const isCC = acct.type === 'credit_card';

  let html = `<p style="margin-bottom:12px"><span class="status ${acct.status}">${acct.status}</span> — ${acct.statement_count || 0} statements found`;
  if (acct.gaps?.length) {
    html += ` | <span style="color:var(--negative)">Missing months: ${acct.gaps.join(', ')}</span>`;
  }
  html += '</p>';

  if (months.length) {
    if (isCC) {
      html += `<table class="data-table"><thead><tr>
        <th>Month</th><th class="num">Previous Balance</th><th class="num">Payments</th><th class="num">Charges</th><th class="num">New Balance</th><th>Status</th>
      </tr></thead><tbody>`;
      months.forEach(m => {
        html += `<tr>
          <td>${m.month}</td>
          <td class="num">${m.previous_balance != null ? fmt(m.previous_balance) : '—'}</td>
          <td class="num">${m.total_payments != null ? fmt(m.total_payments) : '—'}</td>
          <td class="num">${m.total_charges != null ? fmt(m.total_charges) : '—'}</td>
          <td class="num">${m.new_balance != null ? fmt(m.new_balance) : '—'}</td>
          <td><span class="status ${m.status}">${m.status}</span></td>
        </tr>`;
      });
    } else {
      html += `<table class="data-table"><thead><tr>
        <th>Month</th><th class="num">Beginning</th><th class="num">Deposits</th><th class="num">Withdrawals</th><th class="num">Ending</th><th>Status</th>
      </tr></thead><tbody>`;
      months.forEach(m => {
        html += `<tr>
          <td>${m.month}</td>
          <td class="num">${m.beginning_balance != null ? fmt(m.beginning_balance) : '—'}</td>
          <td class="num">${m.total_deposits != null ? fmt(m.total_deposits) : '—'}</td>
          <td class="num">${m.total_withdrawals != null ? fmt(m.total_withdrawals) : '—'}</td>
          <td class="num">${m.ending_balance != null ? fmt(m.ending_balance) : '—'}</td>
          <td><span class="status ${m.status}">${m.status}</span></td>
        </tr>`;
      });
    }
    html += '</tbody></table>';
  } else {
    html += '<div class="loading">No monthly data extracted from statements.</div>';
  }

  detail.innerHTML = html;
}

// ─── Checklist Tab ─────────────────────────────────────────────────────────

function renderChecklist(data) {
  const cl = data.checklist || {};
  const categories = cl.categories || [];
  const container = $('#checklist-content');

  const pct = Math.min(cl.completion_pct || 0, 100);
  let html = `
    <div class="progress-bar"><div class="fill" style="width:${Math.min(pct, 100)}%">${pct.toFixed(0)}% Complete (${cl.total_found || 0} of ${cl.total_expected || 0})</div></div>
  `;

  categories.forEach(cat => {
    html += `<h3 class="section-header">${cat.name} <span style="font-weight:400;font-size:12px;color:var(--text-secondary)">(${cat.folder})</span></h3>`;
    html += `<table class="data-table" style="margin-bottom:12px"><thead><tr>
      <th>Document</th><th style="width:80px">Status</th><th>Files Found</th><th style="width:80px">Count</th><th style="width:130px">Override</th>
    </tr></thead><tbody>`;

    (cat.items || []).forEach(item => {
      const statusClass = item.status === 'FOUND' ? 'FOUND' :
                          item.status === 'PARTIAL' ? 'PARTIAL' :
                          item.status === 'MISSING' ? 'MISSING' :
                          item.status === 'DISABLED' ? 'DISABLED' :
                          item.status === 'STORED_ELSEWHERE' ? 'FOUND' : 'NA';
      const overrideVal = item.override || 'active';
      html += `<tr${item.status === 'DISABLED' ? ' style="opacity:0.5"' : ''}>
        <td>${item.doc}</td>
        <td><span class="status ${statusClass}">${item.status}</span></td>
        <td style="font-size:11px">${(item.files || []).join(', ') || '—'}</td>
        <td class="num">${item.count_found}${item.count_expected !== 'any' ? ' / ' + item.count_expected : ''}${item.count_found > item.count_expected && item.count_expected !== 'any' ? ' <span title="Includes multiple account types (checking + credit card). See Bank Rec tab for breakdown." style="color:var(--accent);cursor:help;font-size:14px">ⓘ</span>' : ''}</td>
        <td><select class="checklist-override" data-cat="${cat.name}" data-doc="${item.doc}" style="font-size:11px;padding:2px 4px">
          <option value="active"${overrideVal === 'active' ? ' selected' : ''}>Active</option>
          <option value="disabled"${overrideVal === 'disabled' ? ' selected' : ''}>Disabled</option>
          <option value="stored_elsewhere"${overrideVal === 'stored_elsewhere' ? ' selected' : ''}>Stored Elsewhere</option>
          <option value="referenced_in"${overrideVal === 'referenced_in' ? ' selected' : ''}>Referenced In...</option>
        </select></td>
      </tr>`;
    });

    html += '</tbody></table>';
  });

  // ── Home Office Calculator section ──
  html += `
    <h3 class="section-header" style="margin-top:24px">Home Office Deduction Calculator</h3>
    <div id="home-office-section" style="margin-bottom:20px">
      <div class="loading">Loading home office data...</div>
    </div>
  `;

  container.innerHTML = html;

  // Load Home Office data asynchronously
  loadHomeOfficeSection(state.currentYear);

  // Wire override dropdowns
  container.querySelectorAll('.checklist-override').forEach(sel => {
    sel.addEventListener('change', async () => {
      const year = state.currentYear;
      const body = { category: sel.dataset.cat, doc_name: sel.dataset.doc, status: sel.value };

      // "Referenced In" needs file + amount from user
      if (sel.value === 'referenced_in') {
        const refFile = prompt('Which file contains this info?\n(e.g., 033125 WellsFargo.pdf)');
        if (!refFile) { sel.value = 'active'; return; }
        const refAmt = prompt('Amount? (e.g., 623.88 — leave blank if N/A)');
        body.reference_file = refFile;
        body.reference_amount = refAmt ? parseFloat(refAmt) : null;
      }

      try {
        window.__suppressRefresh = Date.now() + 8000;
        await fetch(`/api/checklist-override/${year}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        await new Promise(r => setTimeout(r, 500));
        state.data = null;
        loadTab('checklist');
      } catch (e) { console.error('Override failed', e); }
    });
  });
}

// ─── Strategy Tab ──────────────────────────────────────────────────────────

function renderStrategy(data) {
  const strat = data.strategy || {};
  const recs = strat.recommendations || [];
  const container = $('#strategy-content');

  let html = '';

  // Summary banner
  html += `<div class="strategy-summary">
    <div><div class="kpi-label">Total Estimated Tax Savings</div>
    <div class="big-number">${fmt(strat.total_estimated_savings || 0)}</div></div>
    <div style="font-size:13px;color:var(--text-secondary)">${recs.length} strategies analyzed</div>
  </div>`;

  if (!recs.length) {
    html += '<div class="loading">No strategy recommendations available. Ensure P&L data is loaded.</div>';
  }

  recs.forEach(rec => {
    html += `<div class="strategy-card">
      <div class="strategy-card-header" onclick="this.nextElementSibling.classList.toggle('open')">
        <span class="badge ${rec.priority}">${rec.priority}</span>
        <span class="title">${rec.title}</span>
        <span class="savings">${rec.estimated_savings ? fmt(rec.estimated_savings) : 'Info'}</span>
      </div>
      <div class="strategy-card-body">
        <p><strong>Summary:</strong> ${rec.summary}</p>
        <p>${(rec.detail || '').replace(/\n/g, '<br>')}</p>
        ${rec.action_items?.length ? `
          <h4 style="margin-top:12px;font-size:13px">Action Items:</h4>
          <ol class="action-items">${rec.action_items.map(a => `<li>${a}</li>`).join('')}</ol>
        ` : ''}
        ${rec.irs_reference ? `<p style="font-size:12px;color:var(--text-secondary);margin-top:8px">IRS Reference: ${rec.irs_reference}</p>` : ''}
        ${rec.data_used ? `<div class="data-used"><strong>Data Used:</strong><br>${Object.entries(rec.data_used).map(([k,v]) => `${k}: ${typeof v === 'number' ? fmt(v) : v}`).join('<br>')}</div>` : ''}
      </div>
    </div>`;
  });

  html += `<div class="strategy-disclaimer">
    These strategies are generated from your parsed financial data for informational purposes only.
    They do not constitute tax, legal, or financial advice.
    Consult a qualified CPA or tax attorney before implementing any strategy.
  </div>`;

  container.innerHTML = html;
}

// ─── Settings Tab ──────────────────────────────────────────────────────────

async function renderSettings() {
  const container = $('#settings-content');
  const cfg = state.config || await api('config');
  const constants = await api(`tax-constants/${state.currentYear}`);

  let html = '';

  // Entities section
  html += `<div class="settings-section"><h3>Entities</h3><div id="entities-list">`;
  (cfg.entities || []).forEach((ent, i) => {
    html += renderEntityCard(ent, i);
  });
  html += `</div>`;
  if ((cfg.entities || []).length < 3) {
    html += `<button class="btn btn-secondary" style="margin-top:8px" onclick="addEntity()">+ Add Entity</button>`;
  }
  html += `</div>`;

  // Tax Constants
  html += `<div class="settings-section"><h3>Tax Constants (${state.currentYear})</h3><div class="form-row">`;
  const constFields = [
    ['standard_mileage_rate', 'Standard Mileage Rate ($/mi)'],
    ['standard_deduction_mfj', 'Standard Deduction (MFJ)'],
    ['se_tax_rate', 'SE Tax Rate (% of net)'],
    ['se_tax_fica_rate', 'SE FICA Rate'],
    ['meals_deductibility_pct', 'Meals Deductibility %'],
    ['sep_ira_limit', 'SEP-IRA Limit'],
    ['solo_401k_employee_deferral_limit', 'Solo 401(k) Employee Limit'],
    ['qbi_deduction_rate', 'QBI Deduction Rate'],
    ['qbi_phase_out_mfj', 'QBI Phase-Out (MFJ)'],
    ['estimated_marginal_rate', 'Estimated Marginal Rate'],
  ];
  constFields.forEach(([key, label]) => {
    html += `<div class="form-group">
      <label>${label}</label>
      <input type="number" step="any" id="const-${key}" value="${constants?.[key] ?? ''}">
    </div>`;
  });
  html += `</div><button class="btn btn-primary" style="margin-top:8px" onclick="saveConstants()">Save Constants</button></div>`;

  // Taxpayer Information (for IRS form population)
  html += `<div class="settings-section"><h3>Taxpayer Information</h3>
    <p class="muted" style="margin-bottom:12px;font-size:12px">Used to populate IRS forms (1040, 4868, Schedule C, state returns). Stored locally in your encrypted database — never sent to any API.</p>
    <div class="form-row">
      <div class="form-group"><label>Your Full Name</label><input type="text" id="tp-owner-name" value="${cfg.owner_name || ''}" placeholder="First Last"></div>
      <div class="form-group"><label>Spouse Full Name</label><input type="text" id="tp-spouse-name" value="${cfg.spouse_name || ''}" placeholder="First Last (if MFJ)"></div>
      <div class="form-group"><label>SSN (last 4 only)</label><input type="text" id="tp-ssn-last4" value="${cfg.ssn_last4 || ''}" maxlength="4" placeholder="1234" style="width:80px"></div>
    </div>
    <div class="form-row">
      <div class="form-group"><label>Street Address</label><input type="text" id="tp-address" value="${cfg.address || ''}" placeholder="123 Main St"></div>
      <div class="form-group"><label>City</label><input type="text" id="tp-city" value="${cfg.city || ''}" placeholder="Denver"></div>
      <div class="form-group"><label>State</label><input type="text" id="tp-state" value="${cfg.state || ''}" placeholder="CO" style="width:50px"></div>
      <div class="form-group"><label>ZIP</label><input type="text" id="tp-zip" value="${cfg.zip || ''}" placeholder="80202" style="width:80px"></div>
    </div>
    <div class="form-row">
      <div class="form-group"><label>City, State ZIP (for IRS forms)</label><input type="text" id="tp-city-state-zip" value="${cfg.city_state_zip || ''}" placeholder="Denver, CO 80202"></div>
      <div class="form-group"><label>Phone</label><input type="text" id="tp-phone" value="${cfg.phone || ''}" placeholder="(303) 555-1234"></div>
    </div>
    <button class="btn btn-primary" onclick="saveTaxpayerInfo()">Save Taxpayer Info</button>
  </div>`;

  // App Preferences
  html += `<div class="settings-section"><h3>App Preferences</h3>
    <div class="form-row">
      <div class="form-group">
        <label>Root Folder Path</label>
        <input type="text" id="pref-root" value="${cfg.root_path || ''}">
      </div>
      <div class="form-group">
        <label>Filing Status</label>
        <select id="pref-filing">
          ${['MFJ', 'Single', 'MFS', 'HOH'].map(s => `<option ${cfg.filing_status === s ? 'selected' : ''}>${s}</option>`).join('')}
        </select>
      </div>
      <div class="form-group">
        <label>Estimated Marginal Rate</label>
        <input type="number" step="0.01" id="pref-marginal" value="${cfg.preferences?.estimated_marginal_rate || 0.22}">
      </div>
      <div class="form-group">
        <label>Home Office Total Deduction ($)</label>
        <input type="number" step="0.01" id="pref-home-office" value="${cfg.preferences?.home_office_total || 0}">
      </div>
      <div class="form-group">
        <label>2025 Estimated Tax Payments ($)</label>
        <input type="number" step="0.01" id="pref-est-payments" value="${cfg.preferences?.estimated_tax_payments_made || 0}" placeholder="Only payments designated for 2025">
      </div>
    </div>
    <button class="btn btn-primary" onclick="savePreferences()">Save Preferences</button>
  </div>`;

  container.innerHTML = html;
}

function renderEntityCard(ent, idx) {
  return `<div class="entity-card">
    <h4>${ent.name || 'New Entity'} ${ent.is_parent ? '<span class="badge INFO">PARENT</span>' : ''}</h4>
    <div class="form-row">
      <div class="form-group"><label>Name</label><input type="text" value="${ent.name || ''}" data-entity="${idx}" data-field="name"></div>
      <div class="form-group"><label>Type</label><select data-entity="${idx}" data-field="entity_type">
        ${['LLC (disregarded)', 'LLC (S-Corp elected)', 'S-Corp', 'C-Corp'].map(t => `<option ${ent.entity_type === t ? 'selected' : ''}>${t}</option>`).join('')}
      </select></div>
      <div class="form-group"><label>State</label><input type="text" value="${ent.state || ''}" data-entity="${idx}" data-field="state"></div>
      <div class="form-group"><label>EIN</label><input type="text" value="${ent.ein || ''}" data-entity="${idx}" data-field="ein"></div>
      <div class="form-group"><label>Accounting Software</label><select data-entity="${idx}" data-field="accounting_system">
        ${['Wave', 'QuickBooks Online', 'FreshBooks', 'Xero', 'Other'].map(s => `<option ${ent.accounting_system === s ? 'selected' : ''}>${s}</option>`).join('')}
      </select></div>
      <div class="form-group"><label>Folder Name</label><input type="text" value="${ent.folder_name || ''}" data-entity="${idx}" data-field="folder_name" placeholder="Auto-discovered"></div>
    </div>
    <div class="bank-list"><strong style="font-size:12px">Bank Accounts:</strong>
      ${(ent.bank_accounts || []).map((b, bi) =>
        `<div class="bank-item">${b.name} (${b.institution}) <button class="btn btn-danger btn-sm" onclick="removeBank(${idx},${bi})">x</button></div>`
      ).join('')}
      <button class="btn btn-secondary btn-sm" style="margin-top:4px" onclick="addBank(${idx})">+ Add Bank</button>
    </div>
    <div class="bank-list"><strong style="font-size:12px">Credit Cards:</strong>
      ${(ent.credit_cards || []).map((c, ci) =>
        `<div class="bank-item">${c.name} (${c.institution}) <button class="btn btn-danger btn-sm" onclick="removeCC(${idx},${ci})">x</button></div>`
      ).join('')}
      <button class="btn btn-secondary btn-sm" style="margin-top:4px" onclick="addCC(${idx})">+ Add Credit Card</button>
    </div>
    <div style="margin-top:8px">
      <label style="font-size:12px"><input type="checkbox" ${ent.is_parent ? 'checked' : ''} data-entity="${idx}" data-field="is_parent"> Parent / Holding Entity</label>
    </div>
    <button class="btn btn-primary btn-sm" style="margin-top:12px" onclick="saveEntities()">Save Entity Changes</button>
    ${idx > 0 ? `<button class="btn btn-danger btn-sm" style="margin-top:12px;margin-left:8px" onclick="removeEntity(${idx})">Remove Entity</button>` : ''}
  </div>`;
}

async function saveEntities() {
  const cfg = state.config;
  $$('[data-entity]').forEach(el => {
    const idx = parseInt(el.dataset.entity);
    const field = el.dataset.field;
    if (idx >= 0 && field && cfg.entities[idx]) {
      if (el.type === 'checkbox') cfg.entities[idx][field] = el.checked;
      else cfg.entities[idx][field] = el.value;
    }
  });
  await api('config', { method: 'POST', body: JSON.stringify(cfg) });
  state.data = null;
  showToast('Entities saved');
}

function addEntity() {
  state.config.entities.push({
    name: '', entity_type: 'LLC (disregarded)', state: '', ein: '',
    accounting_system: 'Wave', folder_name: '', bank_accounts: [], credit_cards: [], is_parent: false,
  });
  renderSettings();
}

function removeEntity(idx) {
  state.config.entities.splice(idx, 1);
  api('config', { method: 'POST', body: JSON.stringify(state.config) });
  state.data = null;
  renderSettings();
}

function addBank(entityIdx) {
  const name = prompt('Bank account name (e.g., "Operating Checking"):');
  const inst = prompt('Institution (e.g., "Wells Fargo"):');
  if (name && inst) {
    state.config.entities[entityIdx].bank_accounts.push({ name, institution: inst });
    renderSettings();
  }
}

function removeBank(entityIdx, bankIdx) {
  state.config.entities[entityIdx].bank_accounts.splice(bankIdx, 1);
  renderSettings();
}

function addCC(entityIdx) {
  const name = prompt('Credit card name (e.g., "Rewards Card"):');
  const inst = prompt('Institution (e.g., "Citi"):');
  if (name && inst) {
    state.config.entities[entityIdx].credit_cards.push({ name, institution: inst });
    renderSettings();
  }
}

function removeCC(entityIdx, ccIdx) {
  state.config.entities[entityIdx].credit_cards.splice(ccIdx, 1);
  renderSettings();
}

async function saveConstants() {
  const constants = {};
  $$('[id^="const-"]').forEach(el => {
    const key = el.id.replace('const-', '');
    constants[key] = parseFloat(el.value) || 0;
  });
  constants.year = state.currentYear;
  await api(`tax-constants/${state.currentYear}`, { method: 'POST', body: JSON.stringify(constants) });
  state.data = null;
  showToast('Tax constants saved');
}

async function savePreferences() {
  const cfg = state.config;
  cfg.root_path = $('#pref-root').value;
  cfg.filing_status = $('#pref-filing').value;
  cfg.preferences = cfg.preferences || {};
  cfg.preferences.estimated_marginal_rate = parseFloat($('#pref-marginal').value) || 0.22;
  cfg.preferences.home_office_total = parseFloat($('#pref-home-office').value) || 0;
  cfg.preferences.estimated_tax_payments_made = parseFloat($('#pref-est-payments').value) || 0;
  await api('config', { method: 'POST', body: JSON.stringify(cfg) });
  state.data = null;
  showToast('Preferences saved');
}

async function saveTaxpayerInfo() {
  const cfg = state.config;
  cfg.owner_name = $('#tp-owner-name').value.trim();
  cfg.spouse_name = $('#tp-spouse-name').value.trim();
  cfg.ssn_last4 = $('#tp-ssn-last4').value.trim();
  cfg.address = $('#tp-address').value.trim();
  cfg.city = $('#tp-city').value.trim();
  cfg.state = $('#tp-state').value.trim();
  cfg.zip = $('#tp-zip').value.trim();
  cfg.city_state_zip = $('#tp-city-state-zip').value.trim();
  cfg.phone = $('#tp-phone').value.trim();
  // Auto-compose city_state_zip if not manually set
  if (!cfg.city_state_zip && cfg.city && cfg.state && cfg.zip) {
    cfg.city_state_zip = `${cfg.city}, ${cfg.state} ${cfg.zip}`;
    $('#tp-city-state-zip').value = cfg.city_state_zip;
  }
  await api('config', { method: 'POST', body: JSON.stringify(cfg) });
  state.data = null;
  showToast('Taxpayer info saved — regenerate forms to see updated fields');
}

// ─── Notes Auto-Save ───────────────────────────────────────────────────────

function attachNoteListeners() {
  $$('.note-input').forEach(el => {
    el.addEventListener('input', () => {
      const key = el.dataset.noteKey;
      state.notes[key] = el.value;
      debounceNoteSave();
    });
  });
}

function debounceNoteSave() {
  if (state.noteTimer) clearTimeout(state.noteTimer);
  state.noteTimer = setTimeout(saveNotes, 500);
}

async function saveNotes() {
  if (!state.currentYear) return;
  const result = await api(`notes/${state.currentYear}`, {
    method: 'POST',
    body: JSON.stringify(state.notes),
  });
  if (result?.status === 'saved') {
    // Subtle save indicator
  } else {
    showToast('Notes could not be saved — check file permissions', 'error');
  }
}

// ─── New Year ──────────────────────────────────────────────────────────────

async function createNewYear() {
  const year = prompt('Enter the new tax year (e.g., 2026):');
  if (!year || isNaN(year)) return;

  const result = await api('new-year', {
    method: 'POST',
    body: JSON.stringify({ year: parseInt(year) }),
  });

  if (result?.status === 'created') {
    showToast(`${year} Tax Year created`);
    location.reload();
  } else {
    showToast(result?.error || 'Failed to create year', 'error');
  }
}

// ─── SSE ───────────────────────────────────────────────────────────────────

function connectSSE() {
  const evtSource = new EventSource('/api/stream');
  evtSource.onopen = () => {
    state.sseConnected = true;
    const dot = $('#sse-dot');
    if (dot) dot.classList.remove('disconnected');
  };
  evtSource.onerror = () => {
    state.sseConnected = false;
    const dot = $('#sse-dot');
    if (dot) dot.classList.add('disconnected');
  };
  evtSource.addEventListener('file_changed', (e) => {
    const data = JSON.parse(e.data);
    showToast(data.toast || `File updated: ${data.file}`, 'success');
    state.data = null; // invalidate cache
    loadTab(state.currentTab);
  });
  evtSource.addEventListener('parse_complete', () => {
    state.data = null;
    loadTab(state.currentTab);
  });
}

// ─── Boot ──────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', init);
