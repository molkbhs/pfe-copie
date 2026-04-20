(() => {
  const currentUser = window.UICore?.bootstrapAuthenticatedPage?.({
    requireAuth: true,
    refreshProfile: true
  }) || null;

  if (!currentUser) {
    window.location.replace('index.html');
    return;
  }

  const state = {
    allTx: [],
    allKpis: [],
    filteredTx: [],
    filteredKpis: [],
    computed: null,
    analyticsReady: true,
    analyticsMessage: '',
    activeFocus: 'all',
    tableMode: 'kpi',
    tableSort: { key: 'valeur', dir: 'desc' },
    tablePage: 1,
    pageSize: 12,
    charts: {},
    lastLoadedAt: null
  };

  const tableConfig = {
    kpi: {
      defaultSort: { key: 'valeur', dir: 'desc' },
      columns: [
        { key: 'kpiNom', label: 'KPI', numeric: false },
        { key: 'periode', label: 'Période', numeric: false },
        { key: 'valeur', label: 'Valeur', numeric: true, format: 'currency' },
        { key: 'evolution', label: 'Variation %', numeric: true, format: 'percent' },
        { key: 'departementId', label: 'Département', numeric: false },
        { key: 'source', label: 'Source', numeric: false },
        { key: 'stat_type', label: 'Type', numeric: false }
      ]
    },
    tx: {
      defaultSort: { key: 'periode_mois', dir: 'desc' },
      columns: [
        { key: 'periode_mois', label: 'Période', numeric: false },
        { key: 'annee', label: 'Année', numeric: true },
        { key: 'trimestre', label: 'Trimestre', numeric: true },
        { key: 'departement', label: 'Département', numeric: false },
        { key: 'type_transaction', label: 'Type transaction', numeric: false },
        { key: 'type_depense', label: 'Type dépense', numeric: false },
        { key: 'Montant', label: 'Montant', numeric: true, format: 'currency' },
        { key: 'Montant_Signe', label: 'Montant signé', numeric: true, format: 'currency' }
      ]
    }
  };

  const defaultEmptyMessage = 'Importez un fichier via la page Data Import pour alimenter ce tableau de bord BI.';
  const sessionExpiredMessage = 'Session expirée. Veuillez vous reconnecter.';
  let fetchSequence = 0;
  let logoutInProgress = false;

  function apiUrl(path) {
    return `${window.API_BASE || ''}${path}`;
  }

  function liveUser() {
    return window.UICore?.getUser?.() || null;
  }

  function hasLiveSession() {
    const user = liveUser();
    if (!user) return false;
    if (window.UICore?.isAuthenticated) return window.UICore.isAuthenticated(user);
    return Boolean(user.id || user.token);
  }

  function authHeaders(json = false) {
    if (!hasLiveSession()) {
      return json ? { 'Content-Type': 'application/json' } : {};
    }

    const user = liveUser();
    if (window.UICore?.authHeaders) {
      return window.UICore.authHeaders({ user, json });
    }

    const token = user?.token || user?.id || '';
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    if (json) headers['Content-Type'] = 'application/json';
    return headers;
  }

  function normalizeText(value) {
    return String(value || '')
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .trim();
  }

  function toNum(value) {
    if (value === null || value === undefined || value === '') return 0;
    if (typeof value === 'number' && Number.isFinite(value)) return value;

    let text = String(value).trim();
    if (!text) return 0;
    text = text.replace(/\u00a0/g, '').replace(/\s+/g, '');
    text = text.replace(/[^0-9,.-]/g, '');

    if (text.includes(',') && text.includes('.')) {
      if (text.lastIndexOf(',') > text.lastIndexOf('.')) {
        text = text.replace(/\./g, '').replace(',', '.');
      } else {
        text = text.replace(/,/g, '');
      }
    } else if (text.includes(',') && !text.includes('.')) {
      text = text.replace(',', '.');
    }

    const parsed = Number.parseFloat(text);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function toInt(value, fallback = 0) {
    const n = Number.parseInt(value, 10);
    return Number.isFinite(n) ? n : fallback;
  }

  function formatCurrency(value, digits = 2) {
    if (!Number.isFinite(value)) return '--';
    return `${new Intl.NumberFormat('fr-TN', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    }).format(value)} DT`;
  }

  function formatNumber(value, digits = 0) {
    if (!Number.isFinite(value)) return '--';
    return new Intl.NumberFormat('fr-TN', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    }).format(value);
  }

  function formatPercent(value, digits = 1) {
    if (!Number.isFinite(value)) return '--';
    return `${value.toFixed(digits)}%`;
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function trendClass(value) {
    if (!Number.isFinite(value)) return 'flat';
    if (value > 0.1) return 'up';
    if (value < -0.1) return 'down';
    return 'flat';
  }

  function trendIcon(value) {
    if (!Number.isFinite(value)) return 'bi-dash';
    if (value > 0.1) return 'bi-arrow-up';
    if (value < -0.1) return 'bi-arrow-down';
    return 'bi-dash';
  }

  function pct(current, previous) {
    if (!Number.isFinite(current) || !Number.isFinite(previous)) return 0;
    if (Math.abs(previous) < 1e-9) {
      return current === 0 ? 0 : 100;
    }
    return ((current - previous) / Math.abs(previous)) * 100;
  }

  function stdDev(values) {
    if (!values.length) return 0;
    const mean = values.reduce((acc, v) => acc + v, 0) / values.length;
    const variance = values.reduce((acc, v) => {
      const d = v - mean;
      return acc + d * d;
    }, 0) / values.length;
    return Math.sqrt(variance);
  }

  function toast(message, type = 'info') {
    const stack = document.getElementById('toastStack');
    if (!stack) return;

    const node = document.createElement('div');
    node.className = `toast-item ${type}`;
    node.textContent = message;
    stack.appendChild(node);
    window.setTimeout(() => node.remove(), 3200);
  }

  function readFilters() {
    return {
      quarter: document.getElementById('fQuarter')?.value || '',
      dept: document.getElementById('fDept')?.value || '',
      period: document.getElementById('fPeriod')?.value || '',
      typeDepense: document.getElementById('fTypeDepense')?.value || '',
      typeTrans: document.getElementById('fTypeTrans')?.value || '',
      search: (document.getElementById('fSearch')?.value || '').trim()
    };
  }

  function ensureAuthorized(response, payload) {
    if (window.UICore?.isUnauthorizedResponse?.(response, payload)) {
      const err = new Error(payload?.message || payload?.error || sessionExpiredMessage);
      err.code = 'AUTH_REQUIRED';
      throw err;
    }
  }

  function forceLogout(reason = sessionExpiredMessage) {
    if (logoutInProgress) return;
    logoutInProgress = true;
    fetchSequence += 1;
    clearDashboard(reason);

    if (window.UICore?.logout) {
      window.UICore.logout('index.html', { reason: 'unauthorized' });
    } else {
      localStorage.clear();
      sessionStorage.clear();
      window.location.replace('index.html');
    }
  }

  function ensureSession() {
    if (!hasLiveSession()) {
      forceLogout(sessionExpiredMessage);
      return false;
    }
    return true;
  }

  async function fetchJson(path, options = {}, requireAuth = true) {
    if (requireAuth && !ensureSession()) {
      const err = new Error(sessionExpiredMessage);
      err.code = 'AUTH_REQUIRED';
      throw err;
    }

    const finalHeaders = {
      ...(options.headers || {}),
      ...(requireAuth ? authHeaders(options.json === true) : {})
    };

    const response = await fetch(apiUrl(path), {
      ...options,
      headers: finalHeaders
    });

    let payload = {};
    try {
      payload = await response.json();
    } catch (_) {
      payload = {};
    }

    if (requireAuth) ensureAuthorized(response, payload);

    if (!response.ok || payload?.success === false) {
      const message = payload?.message || payload?.error || `Erreur HTTP ${response.status}`;
      throw new Error(message);
    }

    return payload;
  }

  function inferSignedAmount(row, montant) {
    if (row.Montant_Signe !== null && row.Montant_Signe !== undefined && row.Montant_Signe !== '') {
      return toNum(row.Montant_Signe);
    }

    const typeTx = normalizeText(row.type_transaction || row.TypeTransaction);
    if (typeTx.includes('revenu') || typeTx.includes('income') || typeTx.includes('encaisse')) {
      return Math.abs(montant);
    }
    if (typeTx.includes('depense') || typeTx.includes('expense') || typeTx.includes('charge') || typeTx.includes('debit')) {
      return -Math.abs(montant);
    }

    return montant;
  }

  function normalizeTxRow(row) {
    const montant = Math.abs(toNum(row?.Montant));
    const signed = inferSignedAmount(row || {}, montant);
    const period = String(row?.periode_mois || row?.YearMonth || row?.period || '').trim();
    const trimestre = toInt(row?.trimestre ?? row?.Trimestre, 0);

    return {
      ...row,
      Montant: montant,
      Montant_Signe: signed,
      annee: toInt(row?.annee ?? row?.Année, 0),
      trimestre: trimestre || null,
      periode_mois: period,
      departement: row?.departement || row?.Département || 'Non renseigné',
      type_transaction: row?.type_transaction || row?.TypeTransaction || 'Non renseigné',
      type_depense: row?.type_depense || row?.TypeDépense || 'Non renseigné',
      responsable: row?.responsable || row?.Responsable || 'Non renseigné',
      client_fournisseur: row?.client_fournisseur || row?.Client_Fournisseur || 'Non renseigné',
      projet: row?.projet || row?.Projet || 'Sans projet'
    };
  }

  function parseGlobalKpis(rows) {
    const byName = new Map();
    (rows || []).forEach((row) => {
      if (String(row?.periode || '').toLowerCase() !== 'global') return;
      byName.set(normalizeText(row?.kpiNom), toNum(row?.valeur));
    });

    const find = (patterns) => {
      for (const [key, value] of byName.entries()) {
        if (patterns.some((pattern) => key.includes(pattern))) return value;
      }
      return 0;
    };

    return {
      total: find(['ca_total', 'ca total', 'chiffre']) || find(['revenu']),
      revenue: find(['revenu']),
      expense: find(['depense']),
      net: find(['solde']),
      count: find(['nb_transaction', 'nombre de transaction', 'nombre_transaction']),
      avg: find(['valeur_moyenne', 'valeur moyenne', 'moyenne'])
    };
  }

  function mapGroup(rows, keyFn) {
    const map = new Map();
    rows.forEach((row) => {
      const key = keyFn(row);
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(row);
    });
    return map;
  }

  function computeMetrics(txRows, kpiRows) {
    const tx = txRows || [];
    const hasTx = tx.length > 0;

    const globalKpi = parseGlobalKpis(kpiRows || []);

    const totalAmount = hasTx ? tx.reduce((acc, row) => acc + toNum(row.Montant), 0) : toNum(globalKpi.total);
    const totalRevenue = hasTx
      ? tx.reduce((acc, row) => acc + Math.max(0, toNum(row.Montant_Signe)), 0)
      : toNum(globalKpi.revenue);
    const totalExpense = hasTx
      ? Math.abs(tx.reduce((acc, row) => acc + Math.min(0, toNum(row.Montant_Signe)), 0))
      : Math.abs(toNum(globalKpi.expense));
    const totalNet = hasTx ? totalRevenue - totalExpense : toNum(globalKpi.net);
    const txCount = hasTx ? tx.length : toInt(globalKpi.count, 0);
    const avgValue = txCount > 0 ? totalAmount / txCount : toNum(globalKpi.avg);

    const monthlyMap = new Map();
    tx.forEach((row) => {
      const month = row.periode_mois || 'N/A';
      if (!monthlyMap.has(month)) {
        monthlyMap.set(month, {
          month,
          revenue: 0,
          expense: 0,
          net: 0,
          amount: 0,
          count: 0
        });
      }

      const bucket = monthlyMap.get(month);
      const signed = toNum(row.Montant_Signe);
      const amount = toNum(row.Montant);
      bucket.amount += amount;
      bucket.count += 1;
      if (signed >= 0) bucket.revenue += signed;
      else bucket.expense += Math.abs(signed);
      bucket.net = bucket.revenue - bucket.expense;
    });

    const monthLabels = Array.from(monthlyMap.keys())
      .filter((m) => m && m !== 'undefined')
      .sort();

    const monthlySeries = monthLabels.map((label) => {
      const row = monthlyMap.get(label);
      return {
        ...row,
        average: row.count > 0 ? row.amount / row.count : 0
      };
    });

    const lastMonth = monthlySeries[monthlySeries.length - 1] || null;
    const prevMonth = monthlySeries[monthlySeries.length - 2] || null;

    const revenueTrend = lastMonth && prevMonth ? pct(lastMonth.revenue, prevMonth.revenue) : 0;
    const expenseTrend = lastMonth && prevMonth ? pct(lastMonth.expense, prevMonth.expense) : 0;
    const netTrend = lastMonth && prevMonth ? pct(lastMonth.net, prevMonth.net) : 0;
    const txTrend = lastMonth && prevMonth ? pct(lastMonth.count, prevMonth.count) : 0;
    const avgTrend = lastMonth && prevMonth ? pct(lastMonth.average, prevMonth.average) : 0;

    const ratioDepRev = totalRevenue > 0 ? (totalExpense / totalRevenue) * 100 : 0;
    const volatility = stdDev(tx.map((row) => toNum(row.Montant)));

    const depMap = mapGroup(tx, (row) => row.departement || 'Non renseigné');
    const depTotals = Array.from(depMap.entries()).map(([name, rows]) => {
      const signed = rows.reduce((acc, row) => acc + toNum(row.Montant_Signe), 0);
      const amount = rows.reduce((acc, row) => acc + toNum(row.Montant), 0);
      return {
        name,
        net: signed,
        amount,
        txCount: rows.length,
        share: totalAmount > 0 ? (amount / totalAmount) * 100 : 0
      };
    }).sort((a, b) => b.amount - a.amount);

    const byExpenseType = mapGroup(
      tx.filter((row) => toNum(row.Montant_Signe) < 0),
      (row) => row.type_depense || 'Non renseigné'
    );
    const expenseGroups = Array.from(byExpenseType.entries())
      .map(([name, rows]) => ({
        name,
        value: Math.abs(rows.reduce((acc, row) => acc + Math.min(0, toNum(row.Montant_Signe)), 0))
      }))
      .sort((a, b) => b.value - a.value);

    const byTransactionType = mapGroup(tx, (row) => row.type_transaction || 'Non renseigné');
    const txGroups = Array.from(byTransactionType.entries())
      .map(([name, rows]) => ({
        name,
        count: rows.length,
        amount: rows.reduce((acc, row) => acc + toNum(row.Montant), 0)
      }))
      .sort((a, b) => b.count - a.count);

    const monthlyGrowth = monthlySeries.map((row, idx) => {
      if (idx === 0) return 0;
      return pct(row.net, monthlySeries[idx - 1].net);
    });

    const departmentCount = depTotals.length;
    const expenseMean = txCount > 0 ? totalExpense / txCount : 0;
    const negativeMonths = monthlySeries.filter((m) => m.net < 0).length;
    const alertCount = negativeMonths + (ratioDepRev > 70 ? 1 : 0) + (netTrend < -10 ? 1 : 0);

    const topContributors = [...depTotals].sort((a, b) => b.net - a.net).slice(0, 5);
    const bottomContributors = [...depTotals].sort((a, b) => a.net - b.net).slice(0, 5);

    return {
      hasTx,
      txCount,
      totalAmount,
      totalRevenue,
      totalExpense,
      totalNet,
      avgValue,
      ratioDepRev,
      volatility,
      departmentCount,
      expenseMean,
      alertCount,
      negativeMonths,
      revenueTrend,
      expenseTrend,
      netTrend,
      txTrend,
      avgTrend,
      monthLabels,
      monthlySeries,
      monthlyGrowth,
      depTotals,
      expenseGroups,
      txGroups,
      topContributors,
      bottomContributors
    };
  }

  function setFilterOptions(selectId, values, formatter) {
    const select = document.getElementById(selectId);
    if (!select) return;

    const current = select.value;
    while (select.options.length > 1) {
      select.remove(1);
    }

    const uniqueValues = [...new Set(values.filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), 'fr'));
    uniqueValues.forEach((value) => {
      const label = formatter ? formatter(value) : value;
      select.add(new Option(label, value));
    });

    if (current && uniqueValues.includes(current)) {
      select.value = current;
    }
  }

  function populateFilters() {
    setFilterOptions(
      'fQuarter',
      state.allTx.map((row) => (row.trimestre ? String(row.trimestre) : '')),
      (q) => `Trimestre: Q${q}`
    );
    setFilterOptions('fDept', state.allTx.map((row) => row.departement));

    const periods = [
      ...state.allTx.map((row) => row.periode_mois),
      ...state.allKpis.map((row) => row.periode)
    ].filter((value) => value && String(value).toLowerCase() !== 'global');
    setFilterOptions('fPeriod', periods);

    setFilterOptions('fTypeDepense', state.allTx.map((row) => row.type_depense));
    setFilterOptions('fTypeTrans', state.allTx.map((row) => row.type_transaction));
  }

  function globalSearchHit(row, term) {
    if (!term) return true;

    const haystack = [
      row.kpiNom,
      row.periode,
      row.departement,
      row.departementId,
      row.type_depense,
      row.type_transaction,
      row.periode_mois,
      row.source,
      row.stat_type,
      row.projet,
      row.client_fournisseur
    ].map(normalizeText).join(' ');

    return haystack.includes(term);
  }

  function focusForKpi(kpiName) {
    const normalized = normalizeText(kpiName);
    if (normalized.includes('ratio') || normalized.includes('solde') || normalized.includes('volatil')) return 'risk';
    if (normalized.includes('transaction') || normalized.includes('moyenne')) return 'operations';
    return 'finance';
  }

  function applyFiltersAndCompute() {
    const filters = readFilters();
    const term = normalizeText(filters.search);

    state.filteredTx = state.allTx.filter((row) => {
      if (filters.quarter && String(row.trimestre || '') !== filters.quarter) return false;
      if (filters.dept && String(row.departement || '') !== filters.dept) return false;
      if (filters.period && String(row.periode_mois || '') !== filters.period) return false;
      if (filters.typeDepense && String(row.type_depense || '') !== filters.typeDepense) return false;
      if (filters.typeTrans && String(row.type_transaction || '') !== filters.typeTrans) return false;
      return globalSearchHit(row, term);
    });

    state.filteredKpis = state.allKpis.filter((row) => {
      if (filters.period && String(row.periode || '') !== filters.period) return false;
      return globalSearchHit(row, term);
    });

    if (state.activeFocus !== 'all') {
      state.filteredKpis = state.filteredKpis.filter((row) => focusForKpi(row.kpiNom) === state.activeFocus);
    }

    const activeCount = [
      filters.quarter,
      filters.dept,
      filters.period,
      filters.typeDepense,
      filters.typeTrans,
      filters.search
    ].filter(Boolean).length;

    const countEl = document.getElementById('activeCount');
    if (countEl) {
      countEl.textContent = `${activeCount} filtre${activeCount > 1 ? 's' : ''}`;
    }

    state.computed = computeMetrics(state.filteredTx, state.filteredKpis);
  }

  function clearDashboard(message = defaultEmptyMessage) {
    state.allTx = [];
    state.allKpis = [];
    state.filteredTx = [];
    state.filteredKpis = [];
    state.computed = null;
    state.analyticsMessage = message || defaultEmptyMessage;

    destroyCharts();

    const subtitle = document.getElementById('dataSubtitle');
    if (subtitle) subtitle.textContent = message || defaultEmptyMessage;

    const lastUpdated = document.getElementById('lastUpdated');
    if (lastUpdated) lastUpdated.textContent = 'Dernière mise à jour: --';

    const ids = ['kpiGrid', 'insightCards', 'summaryMetrics', 'topContributors', 'bottomContributors', 'tableHead', 'pager'];
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = '';
    });

    const tableBody = document.getElementById('tableTbody');
    if (tableBody) {
      tableBody.innerHTML = `<tr><td class="text-center text-muted py-4">${escapeHtml(message || defaultEmptyMessage)}</td></tr>`;
    }

    const kpiCountBadge = document.getElementById('kpiCountBadge');
    if (kpiCountBadge) kpiCountBadge.textContent = '0';

    const tblCountBadge = document.getElementById('tblCountBadge');
    if (tblCountBadge) tblCountBadge.textContent = '0 lignes';

    const emptyState = document.getElementById('emptyState');
    if (emptyState) emptyState.style.display = 'block';

    const emptyMessage = document.getElementById('emptyStateMessage');
    if (emptyMessage) emptyMessage.textContent = message || defaultEmptyMessage;

    const aiResponse = document.getElementById('aiResponse');
    if (aiResponse) {
      aiResponse.innerHTML = '';
      aiResponse.classList.remove('show');
    }
  }

  function destroyCharts() {
    Object.keys(state.charts).forEach((key) => {
      try {
        state.charts[key]?.destroy?.();
      } catch (_) {
        // noop
      }
    });
    state.charts = {};
  }

  function renderHeaderSummary() {
    const subtitle = document.getElementById('dataSubtitle');
    const updated = document.getElementById('lastUpdated');

    if (!state.computed || !subtitle || !updated) return;

    const m = state.computed;
    const periodInfo = m.monthLabels.length
      ? `${m.monthLabels[0]} → ${m.monthLabels[m.monthLabels.length - 1]}`
      : 'Période non disponible';

    subtitle.textContent = `${formatNumber(m.txCount)} transactions traitées | ${periodInfo}`;

    const stamp = state.lastLoadedAt || new Date();
    updated.innerHTML = `Dernière mise à jour: <b>${stamp.toLocaleString('fr-FR')}</b>`;
  }

  function renderKpiCards() {
    const grid = document.getElementById('kpiGrid');
    const badge = document.getElementById('kpiCountBadge');
    if (!grid || !state.computed) return;

    const m = state.computed;
    const cards = [
      {
        key: 'ca_total',
        label: 'CA Total',
        group: 'finance',
        value: m.totalAmount,
        format: 'currency',
        trend: m.revenueTrend,
        context: 'Volume global consolidé',
        icon: 'bi-currency-exchange',
        line: 'linear-gradient(90deg,#1fb5b5,#14b8a6)'
      },
      {
        key: 'revenus',
        label: 'Revenus Totaux',
        group: 'finance',
        value: m.totalRevenue,
        format: 'currency',
        trend: m.revenueTrend,
        context: 'Transactions positives',
        icon: 'bi-graph-up-arrow',
        line: 'linear-gradient(90deg,#10b981,#059669)'
      },
      {
        key: 'depenses',
        label: 'Dépenses Totales',
        group: 'finance',
        value: m.totalExpense,
        format: 'currency',
        trend: m.expenseTrend,
        context: 'Transactions négatives absolues',
        icon: 'bi-wallet2',
        line: 'linear-gradient(90deg,#f97316,#ef4444)'
      },
      {
        key: 'solde_net',
        label: 'Solde Net',
        group: 'finance',
        value: m.totalNet,
        format: 'currency',
        trend: m.netTrend,
        context: 'Revenus - dépenses',
        icon: m.totalNet >= 0 ? 'bi-check-circle-fill' : 'bi-x-circle-fill',
        line: m.totalNet >= 0
          ? 'linear-gradient(90deg,#10b981,#1fb5b5)'
          : 'linear-gradient(90deg,#ef4444,#f97316)'
      },
      {
        key: 'tx_count',
        label: 'Nb Transactions',
        group: 'operations',
        value: m.txCount,
        format: 'number',
        trend: m.txTrend,
        context: 'Volume d\'opérations',
        icon: 'bi-hash',
        line: 'linear-gradient(90deg,#6366f1,#8b5cf6)'
      },
      {
        key: 'avg_value',
        label: 'Valeur Moyenne',
        group: 'operations',
        value: m.avgValue,
        format: 'currency',
        trend: m.avgTrend,
        context: 'Montant moyen par transaction',
        icon: 'bi-bar-chart-line-fill',
        line: 'linear-gradient(90deg,#0ea5e9,#6366f1)'
      },
      {
        key: 'ratio_dep_rev',
        label: 'Ratio Dépenses / Revenus',
        group: 'risk',
        value: m.ratioDepRev,
        format: 'percent',
        trend: -m.netTrend,
        context: 'Pression des coûts',
        icon: 'bi-percent',
        line: 'linear-gradient(90deg,#f59e0b,#f97316)'
      },
      {
        key: 'volatility',
        label: 'Volatilité Montants',
        group: 'risk',
        value: m.volatility,
        format: 'currency',
        trend: 0,
        context: 'Écart type des montants',
        icon: 'bi-activity',
        line: 'linear-gradient(90deg,#64748b,#94a3b8)'
      }
    ];

    const visibleCards = cards.filter((card) => state.activeFocus === 'all' || card.group === state.activeFocus);

    const formatByType = (card) => {
      if (card.format === 'currency') return formatCurrency(card.value, 2);
      if (card.format === 'percent') return formatPercent(card.value, 1);
      return formatNumber(card.value, 0);
    };

    grid.innerHTML = visibleCards.map((card) => {
      const cls = trendClass(card.trend);
      const icon = trendIcon(card.trend);
      return `
        <article class="kpi-card" style="--kpi-line:${card.line};">
          <div class="kpi-head">
            <div>
              <div class="kpi-label">${escapeHtml(card.label)}</div>
            </div>
            <div class="kpi-icon"><i class="bi ${escapeHtml(card.icon)}"></i></div>
          </div>
          <div class="kpi-value">${formatByType(card)}</div>
          <div class="kpi-foot">
            <div class="kpi-context">${escapeHtml(card.context)}</div>
            <div class="kpi-trend ${cls}"><i class="bi ${icon}"></i>${formatPercent(Math.abs(card.trend), 1)}</div>
          </div>
        </article>
      `;
    }).join('');

    if (badge) {
      badge.textContent = String(visibleCards.length);
    }
  }

  function renderInsights() {
    const target = document.getElementById('insightCards');
    if (!target || !state.computed) return;

    const m = state.computed;
    const topExpense = m.expenseGroups[0] || { name: 'N/A', value: 0 };
    const topDept = m.depTotals[0] || { name: 'N/A', amount: 0, net: 0 };

    const cards = [
      {
        title: 'Pression des coûts',
        value: formatPercent(m.ratioDepRev, 1),
        note: m.ratioDepRev > 70
          ? 'Niveau élevé : surveiller les dépenses prioritaires.'
          : 'Niveau maîtrisé sur la période active.'
      },
      {
        title: 'Poste le plus lourd',
        value: topExpense.name,
        note: `${formatCurrency(topExpense.value)} de dépenses sur cette catégorie.`
      },
      {
        title: 'Département dominant',
        value: topDept.name,
        note: `${formatCurrency(topDept.amount)} de volume, net ${formatCurrency(topDept.net)}.`
      },
      {
        title: 'Mois déficitaires',
        value: String(m.negativeMonths),
        note: m.negativeMonths > 0
          ? 'Des périodes négatives existent : investiguer les causes.'
          : 'Aucun mois en solde négatif détecté.'
      }
    ];

    target.innerHTML = cards.map((card) => `
      <article class="alert-card">
        <h6 class="alert-title">${escapeHtml(card.title)}</h6>
        <div class="alert-value">${escapeHtml(card.value)}</div>
        <div class="alert-note">${escapeHtml(card.note)}</div>
      </article>
    `).join('');
  }

  function renderSummary() {
    const list = document.getElementById('summaryMetrics');
    if (!list || !state.computed) return;

    const m = state.computed;
    const items = [
      { label: 'Solde net', value: formatCurrency(m.totalNet) },
      { label: 'Revenus totaux', value: formatCurrency(m.totalRevenue) },
      { label: 'Dépenses totales', value: formatCurrency(m.totalExpense) },
      { label: 'Ratio dépenses / revenus', value: formatPercent(m.ratioDepRev, 1) },
      { label: 'Départements actifs', value: formatNumber(m.departmentCount, 0) },
      { label: 'Alertes détectées', value: formatNumber(m.alertCount, 0) }
    ];

    list.innerHTML = items.map((item) => `
      <li class="metric-item">
        <div class="label">${escapeHtml(item.label)}</div>
        <div class="value">${escapeHtml(item.value)}</div>
      </li>
    `).join('');
  }

  function renderContributors() {
    if (!state.computed) return;

    const up = document.getElementById('topContributors');
    const down = document.getElementById('bottomContributors');
    if (!up || !down) return;

    const top = state.computed.topContributors;
    const bottom = state.computed.bottomContributors;

    const renderList = (rows) => {
      if (!rows.length) {
        return '<li class="contrib-item"><span>Aucune donnée</span><span>--</span></li>';
      }

      return rows.map((row) => `
        <li class="contrib-item">
          <span>${escapeHtml(row.name)}</span>
          <span>${formatCurrency(row.net)}</span>
        </li>
      `).join('');
    };

    up.innerHTML = renderList(top);
    down.innerHTML = renderList(bottom);
  }

  function getCssVar(name, fallback) {
    const val = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return val || fallback;
  }

  function chartPalette() {
    return {
      primary: getCssVar('--primary', '#1fb5b5'),
      primaryDark: getCssVar('--primary-dark', '#0f9e9e'),
      red: '#ef4444',
      orange: '#f97316',
      amber: '#f59e0b',
      blue: '#3b82f6',
      indigo: '#6366f1',
      slate: '#64748b',
      teal: '#14b8a6'
    };
  }

  function buildCharts() {
    destroyCharts();
    if (!state.computed || !state.computed.monthLabels.length) return;

    const c = chartPalette();
    const m = state.computed;

    const labels = m.monthLabels;
    const revenueSeries = m.monthlySeries.map((row) => row.revenue);
    const expenseSeries = m.monthlySeries.map((row) => row.expense);
    const netSeries = m.monthlySeries.map((row) => row.net);

    const trendCtx = document.getElementById('chartExecutiveTrend');
    if (trendCtx) {
      state.charts.executiveTrend = new Chart(trendCtx, {
        type: 'line',
        data: {
          labels,
          datasets: [
            {
              label: 'Revenus',
              data: revenueSeries,
              borderColor: c.teal,
              backgroundColor: 'rgba(20,184,166,0.12)',
              tension: 0.3,
              fill: false
            },
            {
              label: 'Dépenses',
              data: expenseSeries,
              borderColor: c.orange,
              backgroundColor: 'rgba(249,115,22,0.12)',
              tension: 0.3,
              fill: false
            },
            {
              label: 'Solde Net',
              data: netSeries,
              borderColor: c.primary,
              backgroundColor: 'rgba(31,181,181,0.12)',
              tension: 0.3,
              fill: true
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'bottom' },
            tooltip: {
              callbacks: {
                label: (ctx) => `${ctx.dataset.label}: ${formatCurrency(toNum(ctx.raw))}`
              }
            }
          },
          scales: {
            y: {
              ticks: {
                callback: (value) => formatNumber(toNum(value), 0)
              }
            }
          }
        }
      });
    }

    const expenseCtx = document.getElementById('chartExpenseBreakdown');
    if (expenseCtx) {
      const topExpense = m.expenseGroups.slice(0, 8);
      state.charts.expenseBreakdown = new Chart(expenseCtx, {
        type: 'doughnut',
        data: {
          labels: topExpense.map((row) => row.name),
          datasets: [{
            data: topExpense.map((row) => row.value),
            backgroundColor: ['#f97316', '#ef4444', '#f59e0b', '#fb7185', '#34d399', '#38bdf8', '#818cf8', '#a78bfa']
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'bottom' },
            tooltip: {
              callbacks: {
                label: (ctx) => `${ctx.label}: ${formatCurrency(toNum(ctx.raw))}`
              }
            }
          }
        }
      });
    }

    const txMixCtx = document.getElementById('chartTransactionMix');
    if (txMixCtx) {
      const txGroups = m.txGroups.slice(0, 8);
      state.charts.transactionMix = new Chart(txMixCtx, {
        type: 'pie',
        data: {
          labels: txGroups.map((row) => row.name),
          datasets: [{
            data: txGroups.map((row) => row.count),
            backgroundColor: ['#1fb5b5', '#3b82f6', '#8b5cf6', '#14b8a6', '#10b981', '#f59e0b', '#f97316', '#ef4444']
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'bottom' },
            tooltip: {
              callbacks: {
                label: (ctx) => `${ctx.label}: ${formatNumber(toNum(ctx.raw), 0)} transactions`
              }
            }
          }
        }
      });
    }

    const deptShareCtx = document.getElementById('chartDepartmentShare');
    if (deptShareCtx) {
      const dep = m.depTotals.slice(0, 8);
      state.charts.departmentShare = new Chart(deptShareCtx, {
        type: 'bar',
        data: {
          labels: dep.map((row) => row.name),
          datasets: [{
            label: 'Part de CA (%)',
            data: dep.map((row) => row.share),
            backgroundColor: '#3b82f6'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: (ctx) => `${formatPercent(toNum(ctx.raw), 1)}`
              }
            }
          },
          scales: {
            y: {
              ticks: {
                callback: (value) => `${value}%`
              }
            }
          }
        }
      });
    }

    const deptPerfCtx = document.getElementById('chartDeptPerformance');
    if (deptPerfCtx) {
      const dep = m.depTotals.slice(0, 10);
      state.charts.deptPerformance = new Chart(deptPerfCtx, {
        type: 'bar',
        data: {
          labels: dep.map((row) => row.name),
          datasets: [{
            label: 'Solde net',
            data: dep.map((row) => row.net),
            backgroundColor: dep.map((row) => (row.net >= 0 ? '#10b981' : '#ef4444'))
          }]
        },
        options: {
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: (ctx) => formatCurrency(toNum(ctx.raw))
              }
            }
          },
          scales: {
            x: {
              ticks: {
                callback: (value) => formatNumber(toNum(value), 0)
              }
            }
          }
        }
      });
    }

    const riskCtx = document.getElementById('chartRiskSignals');
    if (riskCtx) {
      state.charts.riskSignals = new Chart(riskCtx, {
        data: {
          labels,
          datasets: [
            {
              type: 'bar',
              label: 'Variation mensuelle (%)',
              data: m.monthlyGrowth,
              backgroundColor: m.monthlyGrowth.map((v) => (v >= 0 ? 'rgba(16,185,129,0.45)' : 'rgba(239,68,68,0.45)')),
              yAxisID: 'y1'
            },
            {
              type: 'line',
              label: 'Solde net',
              data: netSeries,
              borderColor: '#1fb5b5',
              backgroundColor: 'rgba(31,181,181,0.12)',
              tension: 0.3,
              yAxisID: 'y'
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'bottom' },
            tooltip: {
              callbacks: {
                label: (ctx) => {
                  if (ctx.dataset.yAxisID === 'y1') {
                    return `${ctx.dataset.label}: ${formatPercent(toNum(ctx.raw), 1)}`;
                  }
                  return `${ctx.dataset.label}: ${formatCurrency(toNum(ctx.raw))}`;
                }
              }
            }
          },
          scales: {
            y: {
              position: 'left',
              ticks: {
                callback: (value) => formatNumber(toNum(value), 0)
              }
            },
            y1: {
              position: 'right',
              grid: { drawOnChartArea: false },
              ticks: {
                callback: (value) => `${value}%`
              }
            }
          }
        }
      });
    }
  }

  function formatCell(value, format) {
    if (format === 'currency') return formatCurrency(toNum(value));
    if (format === 'percent') return formatPercent(toNum(value), 1);
    if (value === null || value === undefined || value === '') return '--';
    return String(value);
  }

  function sortRows(rows, sort) {
    const arr = [...rows];
    const dir = sort.dir === 'asc' ? 1 : -1;

    arr.sort((a, b) => {
      const av = a?.[sort.key];
      const bv = b?.[sort.key];

      const an = toNum(av);
      const bn = toNum(bv);

      const bothNumeric = Number.isFinite(an) && Number.isFinite(bn)
        && (typeof av === 'number' || typeof bv === 'number' || /^-?[\d\s,.]+$/.test(String(av || '')) || /^-?[\d\s,.]+$/.test(String(bv || '')));

      if (bothNumeric) {
        if (an === bn) return 0;
        return an > bn ? dir : -dir;
      }

      const as = String(av ?? '').toLowerCase();
      const bs = String(bv ?? '').toLowerCase();
      if (as === bs) return 0;
      return as > bs ? dir : -dir;
    });

    return arr;
  }

  function setTableMode(mode) {
    state.tableMode = mode;
    state.tableSort = { ...tableConfig[mode].defaultSort };
    state.tablePage = 1;

    document.getElementById('tableModeKpi')?.classList.toggle('active', mode === 'kpi');
    document.getElementById('tableModeTxn')?.classList.toggle('active', mode === 'tx');

    renderTable();
  }

  function renderTable() {
    const head = document.getElementById('tableHead');
    const body = document.getElementById('tableTbody');
    const pager = document.getElementById('pager');
    const badge = document.getElementById('tblCountBadge');
    if (!head || !body || !pager || !badge) return;

    const cfg = tableConfig[state.tableMode];
    const sourceRows = state.tableMode === 'kpi' ? state.filteredKpis : state.filteredTx;
    const rows = sortRows(sourceRows, state.tableSort);

    const totalRows = rows.length;
    badge.textContent = `${formatNumber(totalRows, 0)} ligne${totalRows > 1 ? 's' : ''}`;

    if (!rows.length) {
      head.innerHTML = '';
      body.innerHTML = '<tr><td class="text-center text-muted py-4">Aucune donnée pour les filtres actifs.</td></tr>';
      pager.innerHTML = '';
      return;
    }

    head.innerHTML = `
      <tr>
        ${cfg.columns.map((col) => {
          const isCurrent = state.tableSort.key === col.key;
          const icon = isCurrent ? (state.tableSort.dir === 'asc' ? 'bi-sort-up' : 'bi-sort-down') : 'bi-arrow-down-up';
          return `<th class="sorting" data-key="${col.key}">${escapeHtml(col.label)} <i class="bi ${icon} sort-icon"></i></th>`;
        }).join('')}
      </tr>
    `;

    head.querySelectorAll('th.sorting').forEach((th) => {
      th.addEventListener('click', () => {
        const key = th.getAttribute('data-key');
        if (!key) return;

        if (state.tableSort.key === key) {
          state.tableSort.dir = state.tableSort.dir === 'asc' ? 'desc' : 'asc';
        } else {
          state.tableSort.key = key;
          state.tableSort.dir = 'desc';
        }

        renderTable();
      });
    });

    const totalPages = Math.max(1, Math.ceil(totalRows / state.pageSize));
    state.tablePage = Math.max(1, Math.min(state.tablePage, totalPages));

    const start = (state.tablePage - 1) * state.pageSize;
    const pageRows = rows.slice(start, start + state.pageSize);

    body.innerHTML = pageRows.map((row) => `
      <tr>
        ${cfg.columns.map((col) => `<td>${escapeHtml(formatCell(row?.[col.key], col.format))}</td>`).join('')}
      </tr>
    `).join('');

    const navButtons = [];
    navButtons.push(`<button type="button" class="${state.tablePage === 1 ? 'disabled' : ''}" data-page="${state.tablePage - 1}">Précédent</button>`);

    for (let p = 1; p <= totalPages; p += 1) {
      if (p === 1 || p === totalPages || Math.abs(p - state.tablePage) <= 1) {
        navButtons.push(`<button type="button" class="${p === state.tablePage ? 'active' : ''}" data-page="${p}">${p}</button>`);
      } else if (Math.abs(p - state.tablePage) === 2) {
        navButtons.push('<span class="px-1 text-muted">...</span>');
      }
    }

    navButtons.push(`<button type="button" class="${state.tablePage === totalPages ? 'disabled' : ''}" data-page="${state.tablePage + 1}">Suivant</button>`);

    pager.innerHTML = navButtons.join('');
    pager.querySelectorAll('button[data-page]').forEach((button) => {
      button.addEventListener('click', () => {
        if (button.classList.contains('disabled')) return;
        state.tablePage = toInt(button.getAttribute('data-page'), 1);
        renderTable();
      });
    });
  }

  function showEmptyState(message) {
    const emptyState = document.getElementById('emptyState');
    const emptyMessage = document.getElementById('emptyStateMessage');
    if (emptyState) emptyState.style.display = 'block';
    if (emptyMessage) emptyMessage.textContent = message || defaultEmptyMessage;
  }

  function hideEmptyState() {
    const emptyState = document.getElementById('emptyState');
    if (emptyState) emptyState.style.display = 'none';
  }

  function renderAll() {
    if (!state.computed || !state.filteredTx.length) {
      showEmptyState(state.analyticsMessage || defaultEmptyMessage);
      renderTable();
      return;
    }

    hideEmptyState();
    renderHeaderSummary();
    renderKpiCards();
    renderInsights();
    renderSummary();
    renderContributors();
    buildCharts();
    renderTable();
  }

  function refreshPipeline() {
    applyFiltersAndCompute();
    renderAll();
  }

  async function fetchKpisWithFallback() {
    let kpiPayload = await fetchJson('/api/kpi?limit=1000', { method: 'GET' }, true);

    const hasGlobal = (kpiPayload.kpis || []).some((row) => String(row?.periode || '').toLowerCase() === 'global');
    if (!hasGlobal && state.allTx.length) {
      await fetchJson('/api/analytics/kpi-refresh', {
        method: 'POST',
        json: true,
        body: JSON.stringify({ source: 'etl_auto' })
      }, true);
      kpiPayload = await fetchJson('/api/kpi?limit=1000', { method: 'GET' }, true);
    }

    return kpiPayload.kpis || [];
  }

  async function loadData(forceRefreshKpi = false) {
    const runId = ++fetchSequence;

    try {
      const analyticsPayload = await fetchJson('/api/analytics/data', { method: 'GET' }, true);
      if (runId !== fetchSequence) return;

      state.analyticsReady = analyticsPayload.analytics_ready !== false;
      state.analyticsMessage = analyticsPayload.message || '';

      if (!state.analyticsReady || !Array.isArray(analyticsPayload.data) || !analyticsPayload.data.length) {
        clearDashboard(state.analyticsMessage || defaultEmptyMessage);
        return;
      }

      state.allTx = analyticsPayload.data.map(normalizeTxRow);

      if (forceRefreshKpi) {
        await fetchJson('/api/analytics/kpi-refresh', {
          method: 'POST',
          json: true,
          body: JSON.stringify({ source: 'etl_auto' })
        }, true);
      }

      state.allKpis = await fetchKpisWithFallback();
      state.lastLoadedAt = new Date();

      populateFilters();
      refreshPipeline();
    } catch (error) {
      if (error?.code === 'AUTH_REQUIRED') {
        forceLogout(error.message || sessionExpiredMessage);
        return;
      }
      clearDashboard(error?.message || 'Erreur lors du chargement des données analytiques.');
      toast(error?.message || 'Chargement impossible.', 'error');
    }
  }

  function resetFilters() {
    ['fQuarter', 'fDept', 'fPeriod', 'fTypeDepense', 'fTypeTrans'].forEach((id) => {
      const select = document.getElementById(id);
      if (select) select.value = '';
    });

    const search = document.getElementById('fSearch');
    if (search) search.value = '';

    state.activeFocus = 'all';
    document.querySelectorAll('#focusChips .focus-chip').forEach((chip) => {
      chip.classList.toggle('active', chip.getAttribute('data-focus') === 'all');
    });

    state.tablePage = 1;
    refreshPipeline();
  }

  function renderAiResponse(html) {
    const target = document.getElementById('aiResponse');
    if (!target) return;
    target.innerHTML = html;
    target.classList.add('show');
  }

  function aiAnswer(question) {
    if (!state.computed) {
      return 'Aucune donnée ETL exploitable pour répondre. Importez un fichier puis relancez l’analyse.';
    }

    const q = normalizeText(question);
    const m = state.computed;

    if (q.includes('solde')) {
      return `Le solde net actuel est ${formatCurrency(m.totalNet)}.`;
    }
    if (q.includes('revenu') || q.includes('ca')) {
      return `Les revenus totaux sont ${formatCurrency(m.totalRevenue)}.`;
    }
    if (q.includes('depense') || q.includes('cout') || q.includes('charge')) {
      return `Les dépenses totales atteignent ${formatCurrency(m.totalExpense)}.`;
    }
    if (q.includes('transaction')) {
      return `Le périmètre actif contient ${formatNumber(m.txCount, 0)} transactions.`;
    }
    if (q.includes('departement') && m.depTotals.length) {
      const best = [...m.depTotals].sort((a, b) => b.net - a.net)[0];
      const worst = [...m.depTotals].sort((a, b) => a.net - b.net)[0];
      return `Meilleure contribution: ${best.name} (${formatCurrency(best.net)}). Plus faible: ${worst.name} (${formatCurrency(worst.net)}).`;
    }
    if (q.includes('risque') || q.includes('alerte')) {
      return `Alertes détectées: ${formatNumber(m.alertCount, 0)}. Ratio dépenses/revenus: ${formatPercent(m.ratioDepRev, 1)}.`;
    }

    return [
      `Résumé rapide: solde ${formatCurrency(m.totalNet)}, ratio coûts ${formatPercent(m.ratioDepRev, 1)},`,
      `${formatNumber(m.txCount, 0)} transactions et ${formatNumber(m.departmentCount, 0)} départements actifs.`
    ].join(' ');
  }

  function bindAi() {
    const sendBtn = document.getElementById('aiSendBtn');
    const input = document.getElementById('aiQuestion');
    const suggestions = document.querySelectorAll('#aiSuggestions .ai-chip');

    if (sendBtn && input) {
      sendBtn.addEventListener('click', () => {
        const question = input.value.trim();
        if (!question) return;

        renderAiResponse(`
          <div class="d-flex align-items-center gap-2 mb-2 text-muted">
            <span class="ai-dot-anim"><span class="ai-dot"></span><span class="ai-dot"></span><span class="ai-dot"></span></span>
            Analyse en cours...
          </div>
        `);

        window.setTimeout(() => {
          const answer = aiAnswer(question);
          renderAiResponse(`<strong>Question:</strong> ${escapeHtml(question)}<br><strong>Réponse:</strong> ${escapeHtml(answer)}`);
        }, 300);
      });

      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          sendBtn.click();
        }
      });
    }

    suggestions.forEach((chip) => {
      chip.addEventListener('click', () => {
        if (!input) return;
        input.value = chip.textContent.trim();
        sendBtn?.click();
      });
    });
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function exportKpiCsv() {
    if (!state.computed) {
      toast('Aucune donnée KPI à exporter.', 'error');
      return;
    }

    const rows = [
      ['kpi', 'valeur'],
      ['CA Total', state.computed.totalAmount],
      ['Revenus Totaux', state.computed.totalRevenue],
      ['Dépenses Totales', state.computed.totalExpense],
      ['Solde Net', state.computed.totalNet],
      ['Nb Transactions', state.computed.txCount],
      ['Valeur Moyenne', state.computed.avgValue],
      ['Ratio Dépenses/Revenus (%)', state.computed.ratioDepRev],
      ['Volatilité Montants', state.computed.volatility]
    ];

    const csv = rows.map((row) => row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(',')).join('\n');
    downloadBlob(new Blob([csv], { type: 'text/csv;charset=utf-8' }), `kpi_export_${Date.now()}.csv`);
    toast('Export CSV généré.', 'success');
  }

  async function exportKpiImage() {
    const node = document.getElementById('kpiGrid');
    if (!node || typeof window.html2canvas !== 'function') {
      toast('Export image indisponible.', 'error');
      return;
    }

    const canvas = await window.html2canvas(node, { backgroundColor: null, scale: 2 });
    canvas.toBlob((blob) => {
      if (!blob) return;
      downloadBlob(blob, `kpi_export_${Date.now()}.png`);
      toast('Export image généré.', 'success');
    });
  }

  async function exportKpiPdf() {
    const node = document.getElementById('dashboardCaptureArea');
    const jsPdf = window.jspdf?.jsPDF;
    if (!node || typeof window.html2canvas !== 'function' || !jsPdf) {
      toast('Export PDF indisponible.', 'error');
      return;
    }

    const canvas = await window.html2canvas(node, { backgroundColor: '#ffffff', scale: 2 });
    const imgData = canvas.toDataURL('image/png');

    const pdf = new jsPdf('l', 'mm', 'a4');
    const pageWidth = pdf.internal.pageSize.getWidth();
    const pageHeight = pdf.internal.pageSize.getHeight();
    pdf.addImage(imgData, 'PNG', 8, 8, pageWidth - 16, pageHeight - 16, '', 'FAST');
    pdf.save(`dashboard_${Date.now()}.pdf`);
    toast('Export PDF généré.', 'success');
  }

  function exportKpiDoc() {
    if (!state.computed) {
      toast('Aucune donnée KPI à exporter.', 'error');
      return;
    }

    const m = state.computed;
    const html = `
      <html><head><meta charset="UTF-8"><title>KPI Export</title></head><body>
        <h2>Export KPI BusinessApp</h2>
        <table border="1" cellspacing="0" cellpadding="6">
          <tr><th>KPI</th><th>Valeur</th></tr>
          <tr><td>CA Total</td><td>${formatCurrency(m.totalAmount)}</td></tr>
          <tr><td>Revenus Totaux</td><td>${formatCurrency(m.totalRevenue)}</td></tr>
          <tr><td>Dépenses Totales</td><td>${formatCurrency(m.totalExpense)}</td></tr>
          <tr><td>Solde Net</td><td>${formatCurrency(m.totalNet)}</td></tr>
          <tr><td>Nb Transactions</td><td>${formatNumber(m.txCount, 0)}</td></tr>
          <tr><td>Valeur Moyenne</td><td>${formatCurrency(m.avgValue)}</td></tr>
          <tr><td>Ratio Dépenses/Revenus</td><td>${formatPercent(m.ratioDepRev, 1)}</td></tr>
        </table>
      </body></html>
    `;

    downloadBlob(new Blob([html], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }), `kpi_export_${Date.now()}.docx`);
    toast('Export document généré.', 'success');
  }

  function bindExportButtons() {
    document.getElementById('exportKpiCsv')?.addEventListener('click', exportKpiCsv);
    document.getElementById('exportKpiImage')?.addEventListener('click', () => {
      exportKpiImage().catch(() => toast('Export image impossible.', 'error'));
    });
    document.getElementById('exportKpiPdf')?.addEventListener('click', () => {
      exportKpiPdf().catch(() => toast('Export PDF impossible.', 'error'));
    });
    document.getElementById('exportKpiDocx')?.addEventListener('click', exportKpiDoc);
  }

  function bindFilters() {
    ['fQuarter', 'fDept', 'fPeriod', 'fTypeDepense', 'fTypeTrans'].forEach((id) => {
      document.getElementById(id)?.addEventListener('change', () => {
        state.tablePage = 1;
        refreshPipeline();
      });
    });

    document.getElementById('fSearch')?.addEventListener('input', () => {
      state.tablePage = 1;
      refreshPipeline();
    });

    document.getElementById('btnReset')?.addEventListener('click', resetFilters);

    document.getElementById('btnRefreshKPI')?.addEventListener('click', async () => {
      try {
        toast('Recalcul des KPI en cours...', 'info');
        await fetchJson('/api/analytics/kpi-refresh', {
          method: 'POST',
          json: true,
          body: JSON.stringify({ source: 'etl_auto' })
        }, true);
        await loadData(false);
        toast('KPI recalculés avec succès.', 'success');
      } catch (error) {
        if (error?.code === 'AUTH_REQUIRED') {
          forceLogout(error.message || sessionExpiredMessage);
          return;
        }
        toast(error?.message || 'Recalcul KPI impossible.', 'error');
      }
    });

    document.querySelectorAll('#focusChips .focus-chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        const focus = chip.getAttribute('data-focus') || 'all';
        state.activeFocus = focus;
        document.querySelectorAll('#focusChips .focus-chip').forEach((btn) => {
          btn.classList.toggle('active', btn === chip);
        });
        state.tablePage = 1;
        refreshPipeline();
      });
    });

    document.getElementById('tableModeKpi')?.addEventListener('click', () => setTableMode('kpi'));
    document.getElementById('tableModeTxn')?.addEventListener('click', () => setTableMode('tx'));
  }

  function bindThemeRepaint() {
    const observer = new MutationObserver(() => {
      if (!state.filteredTx.length) return;
      buildCharts();
    });

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme']
    });
  }

  async function boot() {
    bindFilters();
    bindAi();
    bindExportButtons();
    bindThemeRepaint();

    await loadData(false);
  }

  boot().catch((error) => {
    clearDashboard(error?.message || 'Erreur inattendue.');
  });
})();
