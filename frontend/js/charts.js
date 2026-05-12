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
    scopedTx: [],
    scopedKpis: [],
    dimensionRows: [],
    filteredTx: [],
    filteredKpis: [],
    computed: null,
    analyticsReady: true,
    analyticsMessage: '',
    activeFocus: 'global',
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
    },
    dimension: {
      defaultSort: { key: 'solde_net', dir: 'desc' },
      columns: [
        { key: 'dimension_label', label: 'Entité', numeric: false },
        { key: 'type_entite', label: 'Type', numeric: false },
        { key: 'revenus', label: 'Revenus', numeric: true, format: 'currency' },
        { key: 'depenses', label: 'Dépenses', numeric: true, format: 'currency' },
        { key: 'solde_net', label: 'Solde net', numeric: true, format: 'currency' },
        { key: 'nombre_transactions', label: 'Transactions', numeric: true, format: 'number' },
        { key: 'moyenne_transaction', label: 'Moyenne transaction', numeric: true, format: 'currency' }
      ]
    }
  };

  const defaultEmptyMessage = 'Aucun import disponible. Veuillez importer un fichier depuis la page Data Import.';
  const sessionExpiredMessage = 'Session expirée. Veuillez vous reconnecter.';
  const ANALYSIS_VIEWS = {
    global: {
      title: 'Vue globale',
      description: 'Synthèse financière consolidée: revenus, dépenses, solde, marge et tendance globale.',
      className: 'view-global',
      dimensionLabel: 'Entité',
      visibleCards: ['executive-trend', 'expense-breakdown', 'transaction-mix', 'department-share', 'dept-performance', 'risk-signals', 'top-contributors', 'bottom-contributors']
    },
    departements: {
      title: 'Analyse par départements',
      description: 'Lecture départementale des revenus, dépenses et soldes pour identifier les pôles performants.',
      className: 'view-departements',
      dimension: 'departement',
      dimensionLabel: 'Département',
      visibleCards: ['executive-trend', 'expense-breakdown', 'department-share', 'dept-performance', 'top-contributors', 'bottom-contributors']
    },
    responsables: {
      title: 'Analyse par responsables',
      description: 'Performance des responsables: volumes gérés, revenus générés, charges traitées et solde net.',
      className: 'view-responsables',
      dimension: 'responsable',
      dimensionLabel: 'Responsable',
      visibleCards: ['executive-trend', 'expense-breakdown', 'transaction-mix', 'dept-performance', 'risk-signals', 'top-contributors', 'bottom-contributors']
    },
    projets: {
      title: 'Analyse par projets',
      description: 'Rentabilité projet: revenus, dépenses, profitabilité et suivi des projets déficitaires.',
      className: 'view-projets',
      dimension: 'projet',
      dimensionLabel: 'Projet',
      visibleCards: ['executive-trend', 'expense-breakdown', 'transaction-mix', 'dept-performance', 'risk-signals', 'top-contributors', 'bottom-contributors']
    },
    clientsFournisseurs: {
      title: 'Analyse Clients/Fournisseurs',
      description: 'Impact commercial et achat: top clients, top fournisseurs et contribution nette par tiers.',
      className: 'view-clients-fournisseurs',
      dimension: 'client_fournisseur',
      dimensionLabel: 'Client/Fournisseur',
      visibleCards: ['executive-trend', 'expense-breakdown', 'transaction-mix', 'department-share', 'risk-signals', 'top-contributors', 'bottom-contributors']
    }
  };
  let fetchSequence = 0;
  let logoutInProgress = false;

  function resetFilterControls() {
    const defaults = {
      fQuarter: 'Trimestre: Tous',
      fDept: 'Département: Tous',
      fPeriod: 'Période: Toutes',
      fTypeDepense: 'Type dépense: Tous',
      fTypeTrans: 'Type transaction: Tous'
    };

    Object.entries(defaults).forEach(([id, label]) => {
      const select = document.getElementById(id);
      if (!select) return;
      select.innerHTML = `<option value="">${label}</option>`;
      select.value = '';
    });

    const search = document.getElementById('fSearch');
    if (search) search.value = '';

    const activeCount = document.getElementById('activeCount');
    if (activeCount) activeCount.textContent = '0 filtre';

    document.querySelectorAll('#focusChips .focus-chip').forEach((chip) => {
      chip.classList.toggle('active', chip.getAttribute('data-view') === 'global');
    });

    document.getElementById('tableModeKpi')?.classList.add('active');
    document.getElementById('tableModeTxn')?.classList.remove('active');
  }

  function resetRuntimeState(message) {
    state.allTx = [];
    state.allKpis = [];
    state.scopedTx = [];
    state.scopedKpis = [];
    state.dimensionRows = [];
    state.filteredTx = [];
    state.filteredKpis = [];
    state.computed = null;
    state.analyticsReady = false;
    state.analyticsMessage = message || defaultEmptyMessage;
    state.activeFocus = 'global';
    state.tableMode = 'kpi';
    state.tableSort = { ...tableConfig.kpi.defaultSort };
    state.tablePage = 1;
    state.lastLoadedAt = null;
  }

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
    return Boolean(user.token);
  }

  function authHeaders(json = false) {
    if (!hasLiveSession()) {
      return json ? { 'Content-Type': 'application/json' } : {};
    }

    const user = liveUser();
    if (window.UICore?.authHeaders) {
      return window.UICore.authHeaders({ user, json });
    }

    const token = user?.token || '';
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
      try {
        sessionStorage.removeItem('user');
        localStorage.removeItem('user');
      } catch (_) {
        // noop
      }
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
      cache: options.cache || 'no-store',
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
      const err = new Error(message);
      err.status = response.status;
      err.payload = payload;
      throw err;
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

  function normalizePeriod(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';

    const match = raw.match(/^(\d{4})[-/](\d{1,2})/);
    if (!match) return raw;

    const year = match[1];
    const month = match[2].padStart(2, '0');
    return `${year}-${month}`;
  }

  function periodFromRow(row) {
    const direct = normalizePeriod(
      row?.periode_mois
      || row?.year_month
      || row?.YearMonth
      || row?.period
    );
    if (direct) return direct;

    const dt = row?.date_val ? new Date(row.date_val) : null;
    if (dt && Number.isFinite(dt.getTime())) {
      return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}`;
    }

    const year = toInt(row?.annee ?? row?.Année, 0);
    const month = toInt(row?.mois ?? row?.Mois, 0);
    if (year > 0 && month >= 1 && month <= 12) {
      return `${year}-${String(month).padStart(2, '0')}`;
    }

    return '';
  }

  function normalizeTxRow(row) {
    const montant = Math.abs(toNum(row?.Montant));
    const signed = inferSignedAmount(row || {}, montant);
    const period = periodFromRow(row);
    const trimestre = toInt(row?.trimestre ?? row?.Trimestre, 0);

    return {
      ...row,
      Montant: montant,
      Montant_Signe: signed,
      year_month: period || String(row?.year_month || '').trim(),
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
      row.responsable,
      row.client_fournisseur,
      row.cf_type
    ].map(normalizeText).join(' ');

    return haystack.includes(term);
  }

  function currentViewDef() {
    return ANALYSIS_VIEWS[state.activeFocus] || ANALYSIS_VIEWS.global;
  }

  function groupByDimension(data, dimensionName) {
    const map = new Map();
    (data || []).forEach((row) => {
      const key = String(row?.[dimensionName] || '').trim() || 'Non renseigné';
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(row);
    });
    return map;
  }

  function inferEntityType(rows) {
    const typeScores = { Client: 0, Fournisseur: 0 };
    (rows || []).forEach((row) => {
      const t = normalizeText(row?.cf_type);
      if (t.includes('client')) typeScores.Client += 1;
      if (t.includes('fourn')) typeScores.Fournisseur += 1;
    });
    if (typeScores.Client === 0 && typeScores.Fournisseur === 0) return '';
    if (typeScores.Client > 0 && typeScores.Fournisseur > 0) return 'Mixte';
    return typeScores.Client > 0 ? 'Client' : 'Fournisseur';
  }

  function aggregateGroupedRows(grouped, includeEntityType = false) {
    return Array.from(grouped.entries()).map(([dimensionLabel, rows]) => {
      const revenus = rows.reduce((acc, row) => acc + Math.max(0, toNum(row?.Montant_Signe)), 0);
      const depenses = Math.abs(rows.reduce((acc, row) => acc + Math.min(0, toNum(row?.Montant_Signe)), 0));
      const soldeNet = revenus - depenses;
      const nombreTransactions = rows.length;
      const montantTotal = rows.reduce((acc, row) => acc + toNum(row?.Montant), 0);
      const moyenneTransaction = nombreTransactions > 0 ? montantTotal / nombreTransactions : 0;

      return {
        dimension_label: dimensionLabel || 'Non renseigné',
        type_entite: includeEntityType ? inferEntityType(rows) : '',
        revenus,
        depenses,
        solde_net: soldeNet,
        nombre_transactions: nombreTransactions,
        moyenne_transaction: moyenneTransaction
      };
    });
  }

  function aggregateByDepartement(data) {
    const grouped = groupByDimension(data, 'departement');
    return aggregateGroupedRows(grouped, false).sort((a, b) => b.solde_net - a.solde_net);
  }

  function aggregateByResponsable(data) {
    const grouped = groupByDimension(data, 'responsable');
    return aggregateGroupedRows(grouped, false).sort((a, b) => b.solde_net - a.solde_net);
  }

  function aggregateByProjet(data) {
    const grouped = groupByDimension(data, 'projet');
    return aggregateGroupedRows(grouped, false).sort((a, b) => b.solde_net - a.solde_net);
  }

  function aggregateByClientFournisseur(data) {
    const grouped = groupByDimension(data, 'client_fournisseur');
    return aggregateGroupedRows(grouped, true).sort((a, b) => b.solde_net - a.solde_net);
  }

  function computeViewDimensionRows(viewKey, txRows) {
    if (viewKey === 'departements') return aggregateByDepartement(txRows);
    if (viewKey === 'responsables') return aggregateByResponsable(txRows);
    if (viewKey === 'projets') return aggregateByProjet(txRows);
    if (viewKey === 'clientsFournisseurs') return aggregateByClientFournisseur(txRows);
    return [];
  }

  function applyFiltersAndCompute() {
    const filters = readFilters();
    const term = normalizeText(filters.search);

    const baseTx = state.allTx.filter((row) => {
      if (filters.quarter && String(row.trimestre || '') !== filters.quarter) return false;
      if (filters.dept && String(row.departement || '') !== filters.dept) return false;
      if (filters.period && String(row.periode_mois || '') !== filters.period) return false;
      if (filters.typeDepense && String(row.type_depense || '') !== filters.typeDepense) return false;
      if (filters.typeTrans && String(row.type_transaction || '') !== filters.typeTrans) return false;
      return globalSearchHit(row, term);
    });

    const baseKpis = state.allKpis.filter((row) => {
      if (filters.period && String(row.periode || '') !== filters.period) return false;
      return globalSearchHit(row, term);
    });

    state.scopedTx = baseTx;
    state.scopedKpis = baseKpis;
    state.filteredTx = baseTx;
    state.filteredKpis = baseKpis;
    state.dimensionRows = computeViewDimensionRows(state.activeFocus, baseTx);

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

    state.computed = computeMetrics(baseTx, baseKpis);
  }

  function clearDashboard(message = defaultEmptyMessage) {
    resetRuntimeState(message);
    resetFilterControls();

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

    applyViewVisibility();
    updateViewContext();
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

  function activeViewClass() {
    return currentViewDef().className || 'view-global';
  }

  function applyViewVisibility() {
    const shell = document.querySelector('.analytics-wrap');
    const classes = Object.values(ANALYSIS_VIEWS).map((v) => v.className);
    const activeClass = activeViewClass();
    const visibleCards = new Set(currentViewDef().visibleCards || []);

    if (shell) {
      shell.classList.remove(...classes);
      shell.classList.add(activeClass);
    }

    document.querySelectorAll('.view-switchable').forEach((node) => {
      let visible = node.classList.contains(activeClass);
      if (visible && node.classList.contains('chart-card')) {
        const cardId = node.getAttribute('data-card');
        if (cardId && !visibleCards.has(cardId)) visible = false;
      }
      node.classList.toggle('view-hidden', !visible);
    });

    document.querySelector('.table-mode')?.classList.toggle('view-hidden', state.activeFocus !== 'global');
  }

  function updateViewContext() {
    const titleEl = document.getElementById('viewContextTitle');
    const descEl = document.getElementById('viewContextDesc');
    const def = currentViewDef();
    if (!titleEl || !descEl) return;

    titleEl.textContent = def.title;
    const txScope = formatNumber(state.scopedTx.length || 0, 0);
    const kpiScope = formatNumber(state.scopedKpis.length || 0, 0);
    descEl.textContent = `${def.description} Périmètre actif: ${txScope} transactions, ${kpiScope} KPI.`;
  }

  function setActiveView(nextView, triggerRefresh = true) {
    const normalized = Object.prototype.hasOwnProperty.call(ANALYSIS_VIEWS, nextView) ? nextView : 'global';
    state.activeFocus = normalized;

    document.querySelectorAll('#focusChips .focus-chip').forEach((chip) => {
      chip.classList.toggle('active', chip.getAttribute('data-view') === normalized);
    });

    const isGlobal = normalized === 'global';
    const preferredMode = isGlobal ? 'kpi' : 'dimension';
    if (state.tableMode !== preferredMode) {
      state.tableMode = preferredMode;
      state.tableSort = { ...tableConfig[preferredMode].defaultSort };
    }
    document.getElementById('tableModeKpi')?.classList.toggle('active', state.tableMode === 'kpi');
    document.getElementById('tableModeTxn')?.classList.toggle('active', state.tableMode === 'tx');
    document.querySelector('.table-mode')?.classList.toggle('view-hidden', !isGlobal);

    state.tablePage = 1;
    applyViewVisibility();

    if (triggerRefresh) refreshPipeline();
    else updateViewContext();
  }

  function renderHeaderSummary() {
    const subtitle = document.getElementById('dataSubtitle');
    const updated = document.getElementById('lastUpdated');

    if (!state.computed || !subtitle || !updated) return;

    const m = state.computed;
    const periodInfo = m.monthLabels.length
      ? `${m.monthLabels[0]} → ${m.monthLabels[m.monthLabels.length - 1]}`
      : 'Période non disponible';

    if (state.activeFocus === 'global') {
      subtitle.textContent = `${formatNumber(m.txCount)} transactions traitées | ${periodInfo}`;
    } else {
      subtitle.textContent = `${formatNumber(state.dimensionRows.length, 0)} ${currentViewDef().dimensionLabel.toLowerCase()}s analysés | ${formatNumber(m.txCount)} transactions | ${periodInfo}`;
    }

    const stamp = state.lastLoadedAt || new Date();
    updated.innerHTML = `Dernière mise à jour: <b>${stamp.toLocaleString('fr-FR')}</b>`;
    updateViewContext();
  }

  function renderKpiCards() {
    const grid = document.getElementById('kpiGrid');
    const badge = document.getElementById('kpiCountBadge');
    if (!grid || !state.computed) return;

    const m = state.computed;
    const dimensionRows = state.dimensionRows || [];
    const byRevenue = [...dimensionRows].sort((a, b) => b.revenus - a.revenus);
    const byExpense = [...dimensionRows].sort((a, b) => b.depenses - a.depenses);
    const byNetDesc = [...dimensionRows].sort((a, b) => b.solde_net - a.solde_net);
    const byNetAsc = [...dimensionRows].sort((a, b) => a.solde_net - b.solde_net);
    const bestRevenue = byRevenue[0] || null;
    const bestExpense = byExpense[0] || null;
    const bestNet = byNetDesc[0] || null;
    const worstNet = byNetAsc[0] || null;
    const cards = state.activeFocus === 'global'
      ? [
          {
            key: 'revenus',
            label: 'Revenus totaux',
            value: m.totalRevenue,
            format: 'currency',
            trend: m.revenueTrend,
            context: 'Somme des flux positifs',
            icon: 'bi-graph-up-arrow',
            line: 'linear-gradient(90deg,#10b981,#059669)'
          },
          {
            key: 'depenses',
            label: 'Dépenses totales',
            value: m.totalExpense,
            format: 'currency',
            trend: m.expenseTrend,
            context: 'Somme des flux négatifs',
            icon: 'bi-wallet2',
            line: 'linear-gradient(90deg,#f97316,#ef4444)'
          },
          {
            key: 'solde',
            label: 'Solde net',
            value: m.totalNet,
            format: 'currency',
            trend: m.netTrend,
            context: 'Revenus - Dépenses',
            icon: m.totalNet >= 0 ? 'bi-check-circle-fill' : 'bi-x-circle-fill',
            line: m.totalNet >= 0 ? 'linear-gradient(90deg,#10b981,#1fb5b5)' : 'linear-gradient(90deg,#ef4444,#f97316)'
          },
          {
            key: 'marge',
            label: 'Marge',
            value: m.totalRevenue > 0 ? (m.totalNet / m.totalRevenue) * 100 : 0,
            format: 'percent',
            trend: m.netTrend,
            context: 'Rentabilité globale',
            icon: 'bi-pie-chart-fill',
            line: 'linear-gradient(90deg,#0ea5e9,#3b82f6)'
          },
          {
            key: 'tx_count',
            label: 'Nombre de transactions',
            value: m.txCount,
            format: 'number',
            trend: m.txTrend,
            context: 'Volume traité',
            icon: 'bi-hash',
            line: 'linear-gradient(90deg,#6366f1,#8b5cf6)'
          }
        ]
      : (() => {
          if (state.activeFocus === 'clientsFournisseurs') {
            const clientRows = byRevenue.filter((row) => row.type_entite === 'Client');
            const supplierRows = byExpense.filter((row) => row.type_entite === 'Fournisseur');
            const topClient = clientRows[0] || bestRevenue;
            const topSupplier = supplierRows[0] || bestExpense;
            return [
              {
                key: 'top_client',
                label: 'Top client (revenus)',
                value: topClient?.revenus || 0,
                format: 'currency',
                trend: 0,
                context: topClient?.dimension_label || 'Non renseigné',
                icon: 'bi-person-check-fill',
                line: 'linear-gradient(90deg,#10b981,#059669)'
              },
              {
                key: 'top_fournisseur',
                label: 'Top fournisseur (dépenses)',
                value: topSupplier?.depenses || 0,
                format: 'currency',
                trend: 0,
                context: topSupplier?.dimension_label || 'Non renseigné',
                icon: 'bi-truck',
                line: 'linear-gradient(90deg,#f97316,#ef4444)'
              },
              {
                key: 'meilleur_solde',
                label: 'Meilleur impact net',
                value: bestNet?.solde_net || 0,
                format: 'currency',
                trend: 0,
                context: bestNet?.dimension_label || 'Non renseigné',
                icon: 'bi-graph-up',
                line: 'linear-gradient(90deg,#1fb5b5,#14b8a6)'
              },
              {
                key: 'tiers_negatifs',
                label: 'Tiers à solde négatif',
                value: dimensionRows.filter((row) => row.solde_net < 0).length,
                format: 'number',
                trend: 0,
                context: 'Clients/Fournisseurs à surveiller',
                icon: 'bi-exclamation-triangle-fill',
                line: 'linear-gradient(90deg,#ef4444,#f97316)'
              },
              {
                key: 'dimension_count',
                label: 'Nombre de tiers',
                value: dimensionRows.length,
                format: 'number',
                trend: 0,
                context: `${formatNumber(m.txCount, 0)} transactions agrégées`,
                icon: 'bi-people-fill',
                line: 'linear-gradient(90deg,#6366f1,#8b5cf6)'
              }
            ];
          }

          return [
            {
              key: 'top_revenus',
              label: `Top ${currentViewDef().dimensionLabel} (revenus)`,
              value: bestRevenue?.revenus || 0,
              format: 'currency',
              trend: 0,
              context: bestRevenue?.dimension_label || 'Non renseigné',
              icon: 'bi-trophy-fill',
              line: 'linear-gradient(90deg,#10b981,#059669)'
            },
            {
              key: 'top_depenses',
              label: `Top ${currentViewDef().dimensionLabel} (dépenses)`,
              value: bestExpense?.depenses || 0,
              format: 'currency',
              trend: 0,
              context: bestExpense?.dimension_label || 'Non renseigné',
              icon: 'bi-exclamation-circle-fill',
              line: 'linear-gradient(90deg,#f97316,#ef4444)'
            },
            {
              key: 'meilleur_solde',
              label: `Meilleur solde ${currentViewDef().dimensionLabel.toLowerCase()}`,
              value: bestNet?.solde_net || 0,
              format: 'currency',
              trend: 0,
              context: bestNet?.dimension_label || 'Non renseigné',
              icon: 'bi-graph-up',
              line: 'linear-gradient(90deg,#1fb5b5,#14b8a6)'
            },
            {
              key: 'plus_faible_solde',
              label: 'Solde le plus faible',
              value: worstNet?.solde_net || 0,
              format: 'currency',
              trend: 0,
              context: worstNet?.dimension_label || 'Non renseigné',
              icon: 'bi-graph-down-arrow',
              line: 'linear-gradient(90deg,#ef4444,#f97316)'
            },
            {
              key: 'dimension_count',
              label: `Nombre de ${currentViewDef().dimensionLabel.toLowerCase()}s`,
              value: dimensionRows.length,
              format: 'number',
              trend: 0,
              context: `${formatNumber(m.txCount, 0)} transactions agrégées`,
              icon: 'bi-diagram-3-fill',
              line: 'linear-gradient(90deg,#6366f1,#8b5cf6)'
            }
          ];
        })();

    const formatByType = (card) => {
      if (card.format === 'currency') return formatCurrency(card.value, 2);
      if (card.format === 'percent') return formatPercent(card.value, 1);
      return formatNumber(card.value, 0);
    };

    grid.innerHTML = cards.map((card) => {
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
      badge.textContent = String(cards.length);
    }
  }

  function renderInsights() {
    const target = document.getElementById('insightCards');
    if (!target || !state.computed) return;

    const m = state.computed;
    const rows = state.dimensionRows || [];
    const byRevenue = [...rows].sort((a, b) => b.revenus - a.revenus);
    const byExpense = [...rows].sort((a, b) => b.depenses - a.depenses);
    const byNetAsc = [...rows].sort((a, b) => a.solde_net - b.solde_net);
    const topExpense = m.expenseGroups[0] || { name: 'N/A', value: 0 };
    const topRevenueEntity = byRevenue[0] || null;
    const topExpenseEntity = byExpense[0] || null;
    const negativeEntities = rows.filter((row) => row.solde_net < 0).length;
    const cards = state.activeFocus === 'global'
      ? [
          {
            title: 'Pression des coûts',
            value: formatPercent(m.ratioDepRev, 1),
            note: m.ratioDepRev > 70 ? 'Niveau élevé, surveillance renforcée.' : 'Niveau maîtrisé sur la période active.'
          },
          {
            title: 'Poste de dépense principal',
            value: topExpense.name,
            note: `${formatCurrency(topExpense.value)} sur la catégorie dominante.`
          },
          {
            title: 'Mois déficitaires',
            value: String(m.negativeMonths),
            note: m.negativeMonths > 0 ? 'Des périodes à solde négatif existent.' : 'Aucun mois déficitaire détecté.'
          },
          {
            title: 'Volatilité',
            value: formatCurrency(m.volatility),
            note: 'Écart-type des montants transactionnels.'
          }
        ]
      : state.activeFocus === 'clientsFournisseurs'
        ? (() => {
            const topClient = byRevenue.find((row) => row.type_entite === 'Client') || topRevenueEntity;
            const topSupplier = byExpense.find((row) => row.type_entite === 'Fournisseur') || topExpenseEntity;
            return [
              {
                title: 'Top client',
                value: topClient?.dimension_label || 'N/A',
                note: `${formatCurrency(topClient?.revenus || 0)} de revenus générés.`
              },
              {
                title: 'Top fournisseur',
                value: topSupplier?.dimension_label || 'N/A',
                note: `${formatCurrency(topSupplier?.depenses || 0)} de dépenses engagées.`
              },
              {
                title: 'Impact net tiers',
                value: formatCurrency(rows.reduce((acc, row) => acc + row.solde_net, 0)),
                note: `Clients: ${formatNumber(rows.filter((row) => row.type_entite === 'Client').length, 0)} | Fournisseurs: ${formatNumber(rows.filter((row) => row.type_entite === 'Fournisseur').length, 0)}.`
              },
              {
                title: 'Recommandation',
                value: negativeEntities > 0 ? 'Cibler les tiers à risque' : 'Portefeuille stable',
                note: negativeEntities > 0
                  ? 'Analyser les fournisseurs coûteux et clients peu rentables.'
                  : 'Maintenir les meilleures relations commerciales.'
              }
            ];
          })()
        : [
            {
              title: 'Top revenus',
              value: topRevenueEntity?.dimension_label || 'N/A',
              note: `${formatCurrency(topRevenueEntity?.revenus || 0)} de revenus cumulés.`
            },
            {
              title: 'Top dépenses',
              value: topExpenseEntity?.dimension_label || 'N/A',
              note: `${formatCurrency(topExpenseEntity?.depenses || 0)} de dépenses cumulées.`
            },
            {
              title: 'Entités en solde négatif',
              value: formatNumber(negativeEntities, 0),
              note: `${formatNumber(rows.length, 0)} entités analysées dans cette vue.`
            },
            {
              title: 'Recommandation',
              value: negativeEntities > 0 ? 'Action ciblée' : 'Situation stable',
              note: negativeEntities > 0
                ? 'Prioriser les entités à solde net négatif pour un plan correctif.'
                : 'Maintenir la trajectoire actuelle et suivre la tendance.'
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
    const rows = state.dimensionRows || [];
    const averageNet = rows.length ? rows.reduce((acc, row) => acc + row.solde_net, 0) / rows.length : 0;
    const topClientName = [...rows]
      .filter((row) => row.type_entite === 'Client')
      .sort((a, b) => b.revenus - a.revenus)[0]?.dimension_label || 'N/A';
    const topSupplierName = [...rows]
      .filter((row) => row.type_entite === 'Fournisseur')
      .sort((a, b) => b.depenses - a.depenses)[0]?.dimension_label || 'N/A';
    const byView = {
      global: [
        { label: 'Revenus', value: formatCurrency(m.totalRevenue) },
        { label: 'Dépenses', value: formatCurrency(m.totalExpense) },
        { label: 'Solde net', value: formatCurrency(m.totalNet) },
        { label: 'Marge', value: formatPercent(m.totalRevenue > 0 ? (m.totalNet / m.totalRevenue) * 100 : 0, 1) },
        { label: 'Transactions', value: formatNumber(m.txCount, 0) },
        { label: 'Périodes mensuelles', value: formatNumber(m.monthLabels.length, 0) }
      ],
      departements: [
        { label: 'Départements', value: formatNumber(rows.length, 0) },
        { label: 'Solde moyen', value: formatCurrency(averageNet) },
        { label: 'Top revenu', value: rows[0]?.dimension_label || 'N/A' },
        { label: 'Tx totales', value: formatNumber(rows.reduce((acc, row) => acc + row.nombre_transactions, 0), 0) },
        { label: 'Revenus cumulés', value: formatCurrency(rows.reduce((acc, row) => acc + row.revenus, 0)) },
        { label: 'Dépenses cumulées', value: formatCurrency(rows.reduce((acc, row) => acc + row.depenses, 0)) }
      ],
      responsables: [
        { label: 'Responsables', value: formatNumber(rows.length, 0) },
        { label: 'Montant moyen géré', value: formatCurrency(rows.length ? rows.reduce((acc, row) => acc + row.revenus + row.depenses, 0) / rows.length : 0) },
        { label: 'Top responsable', value: rows[0]?.dimension_label || 'N/A' },
        { label: 'Revenus cumulés', value: formatCurrency(rows.reduce((acc, row) => acc + row.revenus, 0)) },
        { label: 'Dépenses cumulées', value: formatCurrency(rows.reduce((acc, row) => acc + row.depenses, 0)) },
        { label: 'Soldes négatifs', value: formatNumber(rows.filter((row) => row.solde_net < 0).length, 0) }
      ],
      projets: [
        { label: 'Projets', value: formatNumber(rows.length, 0) },
        { label: 'Top projet', value: rows[0]?.dimension_label || 'N/A' },
        { label: 'Projets déficitaires', value: formatNumber(rows.filter((row) => row.solde_net < 0).length, 0) },
        { label: 'Profitabilité moyenne', value: formatCurrency(averageNet) },
        { label: 'Revenus cumulés', value: formatCurrency(rows.reduce((acc, row) => acc + row.revenus, 0)) },
        { label: 'Dépenses cumulées', value: formatCurrency(rows.reduce((acc, row) => acc + row.depenses, 0)) }
      ],
      clientsFournisseurs: [
        { label: 'Tiers analysés', value: formatNumber(rows.length, 0) },
        { label: 'Clients', value: formatNumber(rows.filter((row) => row.type_entite === 'Client').length, 0) },
        { label: 'Fournisseurs', value: formatNumber(rows.filter((row) => row.type_entite === 'Fournisseur').length, 0) },
        { label: 'Impact net total', value: formatCurrency(rows.reduce((acc, row) => acc + row.solde_net, 0)) },
        { label: 'Top client', value: topClientName },
        { label: 'Top fournisseur', value: topSupplierName }
      ]
    };
    const items = byView[state.activeFocus] || byView.global;

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

    const top = state.activeFocus === 'global'
      ? [...state.computed.depTotals].sort((a, b) => b.net - a.net).slice(0, 5).map((row) => ({ name: row.name, net: row.net }))
      : [...(state.dimensionRows || [])].sort((a, b) => b.solde_net - a.solde_net).slice(0, 5).map((row) => ({ name: row.dimension_label, net: row.solde_net }));
    const bottom = state.activeFocus === 'global'
      ? [...state.computed.depTotals].sort((a, b) => a.net - b.net).slice(0, 5).map((row) => ({ name: row.name, net: row.net }))
      : [...(state.dimensionRows || [])].sort((a, b) => a.solde_net - b.solde_net).slice(0, 5).map((row) => ({ name: row.dimension_label, net: row.solde_net }));

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

  function setChartCardText(canvasId, title, description) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const card = canvas.closest('.chart-card');
    if (!card) return;
    const titleEl = card.querySelector('h6');
    const descEl = card.querySelector('p');
    if (titleEl) titleEl.textContent = title;
    if (descEl) descEl.textContent = description;
  }

  function buildCharts() {
    destroyCharts();
    if (!state.computed) return;

    const c = chartPalette();
    const m = state.computed;
    const rows = state.dimensionRows || [];

    if (state.activeFocus === 'global') {
      const labels = m.monthLabels;
      const revenueSeries = m.monthlySeries.map((row) => row.revenue);
      const expenseSeries = m.monthlySeries.map((row) => row.expense);
      const netSeries = m.monthlySeries.map((row) => row.net);

      setChartCardText('chartExecutiveTrend', 'Évolution revenus / dépenses / solde net', 'Lecture mensuelle de la performance financière globale.');
      const trendCtx = document.getElementById('chartExecutiveTrend');
      if (trendCtx && labels.length) {
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
            }
          }
        });
      }

      setChartCardText('chartExpenseBreakdown', 'Classement KPI globaux', 'Comparaison directe des KPI financiers principaux.');
      const expenseCtx = document.getElementById('chartExpenseBreakdown');
      if (expenseCtx) {
        const kpiRank = [
          { label: 'Revenus', value: m.totalRevenue },
          { label: 'Dépenses', value: m.totalExpense },
          { label: 'Solde Net', value: m.totalNet },
          { label: 'Marge (%)', value: m.totalRevenue > 0 ? (m.totalNet / m.totalRevenue) * 100 : 0 }
        ];
        state.charts.expenseBreakdown = new Chart(expenseCtx, {
          type: 'bar',
          data: {
            labels: kpiRank.map((row) => row.label),
            datasets: [{
              label: 'Valeur KPI',
              data: kpiRank.map((row) => row.value),
              backgroundColor: ['#10b981', '#f97316', '#1fb5b5', '#6366f1']
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: {
                  label: (ctx) => ctx.label.includes('(%)')
                    ? formatPercent(toNum(ctx.raw), 1)
                    : formatCurrency(toNum(ctx.raw))
                }
              }
            }
          }
        });
      }

      setChartCardText('chartTransactionMix', 'Répartition des transactions', 'Distribution des transactions par type.');
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

      setChartCardText('chartDepartmentShare', 'Part de CA par département', 'Contribution relative des départements au volume global.');
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
            }
          },
          scales: { y: { ticks: { callback: (value) => `${value}%` } } }
        });
      }

      setChartCardText('chartDeptPerformance', 'Performance départementale', 'Comparatif du solde net des départements.');
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
              tooltip: { callbacks: { label: (ctx) => formatCurrency(toNum(ctx.raw)) } }
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

      setChartCardText('chartRiskSignals', 'Variance et dégradation', 'Variation mensuelle du solde net et rythme d’évolution.');
      const riskCtx = document.getElementById('chartRiskSignals');
      if (riskCtx && labels.length) {
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
                ticks: { callback: (value) => formatNumber(toNum(value), 0) }
              },
              y1: {
                position: 'right',
                grid: { drawOnChartArea: false },
                ticks: { callback: (value) => `${value}%` }
              }
            }
          }
        });
      }
      return;
    }

    const dimRows = [...rows].sort((a, b) => b.revenus - a.revenus).slice(0, 10);
    const dimLabels = dimRows.map((row) => row.dimension_label);
    const dimRevenues = dimRows.map((row) => row.revenus);
    const dimExpenses = dimRows.map((row) => row.depenses);
    const dimNets = dimRows.map((row) => row.solde_net);
    const dimTx = dimRows.map((row) => row.nombre_transactions);
    const dimAvg = dimRows.map((row) => row.moyenne_transaction);
    const dimKeyLabel = currentViewDef().dimensionLabel;

    setChartCardText('chartExecutiveTrend', `Revenus vs dépenses par ${dimKeyLabel.toLowerCase()}`, `Comparaison des flux financiers sur les 10 ${dimKeyLabel.toLowerCase()}s les plus actifs.`);
    const trendCtx = document.getElementById('chartExecutiveTrend');
    if (trendCtx && dimRows.length) {
      state.charts.executiveTrend = new Chart(trendCtx, {
        data: {
          labels: dimLabels,
          datasets: [
            { type: 'bar', label: 'Revenus', data: dimRevenues, backgroundColor: 'rgba(16,185,129,0.5)' },
            { type: 'bar', label: 'Dépenses', data: dimExpenses, backgroundColor: 'rgba(249,115,22,0.5)' },
            { type: 'line', label: 'Solde net', data: dimNets, borderColor: '#1fb5b5', backgroundColor: 'rgba(31,181,181,0.12)', tension: 0.28 }
          ]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
      });
    }

    setChartCardText('chartExpenseBreakdown', `Top ${dimKeyLabel.toLowerCase()}s par dépenses`, 'Répartition des charges sur les principales entités.');
    const expenseCtx = document.getElementById('chartExpenseBreakdown');
    if (expenseCtx && dimRows.length) {
      state.charts.expenseBreakdown = new Chart(expenseCtx, {
        type: 'doughnut',
        data: {
          labels: dimLabels,
          datasets: [{ data: dimExpenses, backgroundColor: ['#f97316', '#ef4444', '#f59e0b', '#fb7185', '#34d399', '#38bdf8', '#818cf8', '#a78bfa', '#60a5fa', '#22c55e'] }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
      });
    }

    setChartCardText('chartTransactionMix', `Volumes transactionnels par ${dimKeyLabel.toLowerCase()}`, 'Part relative des volumes de transactions.');
    const txMixCtx = document.getElementById('chartTransactionMix');
    if (txMixCtx && dimRows.length) {
      state.charts.transactionMix = new Chart(txMixCtx, {
        type: 'pie',
        data: {
          labels: dimLabels,
          datasets: [{ data: dimTx, backgroundColor: ['#1fb5b5', '#3b82f6', '#8b5cf6', '#14b8a6', '#10b981', '#f59e0b', '#f97316', '#ef4444', '#94a3b8', '#6366f1'] }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
      });
    }

    setChartCardText('chartDepartmentShare', `Revenus par ${dimKeyLabel.toLowerCase()}`, 'Comparatif des revenus cumulés.');
    const deptShareCtx = document.getElementById('chartDepartmentShare');
    if (deptShareCtx && dimRows.length) {
      state.charts.departmentShare = new Chart(deptShareCtx, {
        type: 'bar',
        data: { labels: dimLabels, datasets: [{ label: 'Revenus', data: dimRevenues, backgroundColor: '#3b82f6' }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
      });
    }

    setChartCardText('chartDeptPerformance', `Solde net par ${dimKeyLabel.toLowerCase()}`, 'Identification des meilleures et des plus faibles performances.');
    const deptPerfCtx = document.getElementById('chartDeptPerformance');
    if (deptPerfCtx && dimRows.length) {
      state.charts.deptPerformance = new Chart(deptPerfCtx, {
        type: 'bar',
        data: {
          labels: dimLabels,
          datasets: [{ label: 'Solde net', data: dimNets, backgroundColor: dimNets.map((v) => (v >= 0 ? '#10b981' : '#ef4444')) }]
        },
        options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
      });
    }

    setChartCardText('chartRiskSignals', `Moyenne transaction et rentabilité`, 'Lecture simultanée du montant moyen et du solde net par entité.');
    const riskCtx = document.getElementById('chartRiskSignals');
    if (riskCtx && dimRows.length) {
      state.charts.riskSignals = new Chart(riskCtx, {
        data: {
          labels: dimLabels,
          datasets: [
            { type: 'bar', label: 'Moyenne transaction', data: dimAvg, backgroundColor: 'rgba(99,102,241,0.4)', yAxisID: 'y1' },
            { type: 'line', label: 'Solde net', data: dimNets, borderColor: '#1fb5b5', backgroundColor: 'rgba(31,181,181,0.12)', yAxisID: 'y', tension: 0.25 }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom' } },
          scales: {
            y: { position: 'left', ticks: { callback: (value) => formatNumber(toNum(value), 0) } },
            y1: { position: 'right', grid: { drawOnChartArea: false }, ticks: { callback: (value) => formatNumber(toNum(value), 0) } }
          }
        }
      });
    }
  }

  function formatCell(value, format) {
    if (format === 'currency') return formatCurrency(toNum(value));
    if (format === 'percent') return formatPercent(toNum(value), 1);
    if (format === 'number') return formatNumber(toNum(value), 0);
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
    if (state.activeFocus !== 'global') {
      state.tableMode = 'dimension';
      state.tableSort = { ...tableConfig.dimension.defaultSort };
      state.tablePage = 1;
      renderTable();
      return;
    }

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

    let cfg = tableConfig[state.tableMode];
    let sourceRows = state.tableMode === 'kpi' ? state.filteredKpis : state.filteredTx;

    if (state.activeFocus !== 'global') {
      cfg = {
        ...tableConfig.dimension,
        columns: tableConfig.dimension.columns.map((col) => (
          col.key === 'dimension_label'
            ? { ...col, label: currentViewDef().dimensionLabel }
            : col
        ))
      };
      sourceRows = state.dimensionRows || [];
    }

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
    applyViewVisibility();
    updateViewContext();

    const hasRowsForView = state.activeFocus === 'global'
      ? (state.filteredTx.length > 0 || state.filteredKpis.length > 0)
      : (state.dimensionRows.length > 0);
    if (!state.computed || !hasRowsForView) {
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
    const summaryStarted = performance.now();
    const summaryPromise = fetchJson('/api/dashboard/summary', { method: 'GET' }, true)
      .then((payload) => ({ payload, elapsed: performance.now() - summaryStarted }))
      .catch((error) => ({ error, elapsed: performance.now() - summaryStarted }));

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

      const summaryProbe = await summaryPromise;
      if (summaryProbe?.error?.code === 'AUTH_REQUIRED') {
        forceLogout(summaryProbe.error.message || sessionExpiredMessage);
        return;
      }
      const summaryPayload = summaryProbe?.payload || null;
      const elapsedMs = Number(summaryProbe?.elapsed || 0);
      const backendMs = Number(summaryPayload?.response_ms || 0);
      if ((backendMs > 3000 || elapsedMs > 3000) && summaryPayload) {
        toast('Le chargement dépasse 3 secondes. Pensez à filtrer la période ou optimiser les données.', 'info');
      }

      populateFilters();
      refreshPipeline();
    } catch (error) {
      if (error?.code === 'AUTH_REQUIRED') {
        forceLogout(error.message || sessionExpiredMessage);
        return;
      }
      const msg = String(error?.message || '');
      const status = Number(error?.status || 0);
      if (status === 404 && msg.toLowerCase().includes('aucun import disponible')) {
        clearDashboard(defaultEmptyMessage);
        return;
      }
      if (msg.toLowerCase().includes('aucun import disponible')) {
        clearDashboard(defaultEmptyMessage);
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

    setActiveView('global', false);
    refreshPipeline();
  }

  function renderAiResponse(html) {
    const target = document.getElementById('aiResponse');
    if (!target) return;
    target.innerHTML = html;
    target.classList.add('show');
  }

  function buildAiFiltersContext() {
    const getValue = (id) => {
      const el = document.getElementById(id);
      return el ? String(el.value || '') : '';
    };

    return {
      filters: {
        trimestre: getValue('fQuarter'),
        departement: getValue('fDept'),
        periode: getValue('fPeriod'),
        type_depense: getValue('fTypeDepense'),
        type_transaction: getValue('fTypeTrans'),
      }
    };
  }

  async function sendChatbotMessage(message) {
    const cleanMessage = String(message || '').trim();
    if (!cleanMessage) return 'Posez une question plus précise.';

    if (window.ChatbotAI?.sendChatbotMessage) {
      return window.ChatbotAI.sendChatbotMessage(cleanMessage, 'charts', buildAiFiltersContext());
    }

    const response = await fetch(apiUrl('/api/chatbot/ask'), {
      method: 'POST',
      cache: 'no-store',
      headers: authHeaders(true),
      body: JSON.stringify({
        question: cleanMessage,
        message: cleanMessage,
        page: 'charts',
        ...buildAiFiltersContext()
      })
    });

    let payload = {};
    try {
      payload = await response.json();
    } catch (_) {
      payload = {};
    }

    ensureAuthorized(response, payload);
    return String(payload?.answer || payload?.response || payload?.reply || "Je n'ai pas pu répondre pour le moment.");
  }

  function bindAi() {
    const sendBtn = document.getElementById('aiSendBtn');
    const input = document.getElementById('aiQuestion');
    const suggestions = document.querySelectorAll('#aiSuggestions .ai-chip');

    if (sendBtn && input) {
      sendBtn.addEventListener('click', async () => {
        const question = input.value.trim();
        if (!question) return;

        sendBtn.disabled = true;
        renderAiResponse(`
          <div class="d-flex align-items-center gap-2 mb-2 text-muted">
            <span class="ai-dot-anim"><span class="ai-dot"></span><span class="ai-dot"></span><span class="ai-dot"></span></span>
            Analyse en cours...
          </div>
        `);

        try {
          const answer = await sendChatbotMessage(question);
          renderAiResponse(`<strong>Question:</strong> ${escapeHtml(question)}<br><strong>Réponse:</strong> ${escapeHtml(answer)}`);
        } catch (error) {
          if (error?.code === 'AUTH_REQUIRED') {
            forceLogout(error.message || sessionExpiredMessage);
            return;
          }
          renderAiResponse(`<strong>Question:</strong> ${escapeHtml(question)}<br><strong>Réponse:</strong> Je n'ai pas pu répondre pour le moment.`);
        } finally {
          sendBtn.disabled = false;
        }
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

  function summarizeKpiForReport() {
    if (!state.computed) return {};
    return {
      transactions: Number(state.computed.txCount || 0),
      revenus_totaux: Number(state.computed.totalRevenue || 0),
      depenses_totales: Number(state.computed.totalExpense || 0),
      solde_net: Number(state.computed.totalNet || 0),
      marge_pct: Number(state.computed.marginRate || 0),
      ratio_dep_rev: Number(state.computed.ratioDepRev || 0),
      volatilite: Number(state.computed.volatility || 0)
    };
  }

  function blobToDataUrl(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(String(reader.result || ''));
      reader.onerror = () => reject(new Error('Lecture du fichier export impossible'));
      reader.readAsDataURL(blob);
    });
  }

  async function persistReportExport({ format, filename, blob, type = 'dashboard_analytics' }) {
    if (!blob || !format) return;
    try {
      const dataUrl = await blobToDataUrl(blob);
      await fetchJson('/api/reports/export', {
        method: 'POST',
        json: true,
        body: JSON.stringify({
          nom_rapport: `Rapport KPI ${new Date().toLocaleString('fr-FR')}`,
          type_rapport: type,
          format,
          filename,
          file_base64: dataUrl,
          summary: summarizeKpiForReport()
        })
      }, true);
    } catch (error) {
      // Export local already successful; backend archive failure should stay non-blocking.
      console.warn('[reports/export] Persist failed', error);
      toast("Export généré localement, mais l'archivage serveur a échoué.", 'error');
    }
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
    const filename = `kpi_export_${Date.now()}.csv`;
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    downloadBlob(blob, filename);
    persistReportExport({ format: 'csv', filename, blob, type: 'kpi_csv' }).catch(() => {});
    toast('Export CSV généré.', 'success');
  }

  async function exportKpiImage() {
    const node = document.getElementById('kpiGrid');
    if (!node || typeof window.html2canvas !== 'function') {
      toast('Export image indisponible.', 'error');
      return;
    }

    const canvas = await window.html2canvas(node, { backgroundColor: null, scale: 2 });
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
    if (!blob) {
      toast("Impossible de générer l'image PNG.", 'error');
      return;
    }
    const filename = `kpi_export_${Date.now()}.png`;
    downloadBlob(blob, filename);
    await persistReportExport({ format: 'png', filename, blob, type: 'kpi_image_png' });
    toast('Export image PNG généré.', 'success');
  }

  async function exportKpiJpg() {
    const node = document.getElementById('kpiGrid');
    if (!node || typeof window.html2canvas !== 'function') {
      toast('Export JPG indisponible.', 'error');
      return;
    }
    const canvas = await window.html2canvas(node, { backgroundColor: '#ffffff', scale: 2 });
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92));
    if (!blob) {
      toast("Impossible de générer l'image JPG.", 'error');
      return;
    }
    const filename = `kpi_export_${Date.now()}.jpg`;
    downloadBlob(blob, filename);
    await persistReportExport({ format: 'jpg', filename, blob, type: 'kpi_image_jpg' });
    toast('Export image JPG généré.', 'success');
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
    const user = liveUser() || {};
    const author = [user.firstname, user.lastname].filter(Boolean).join(' ').trim() || user.email || 'Utilisateur';
    const generatedAt = new Date().toLocaleString('fr-FR');
    const pageWidth = pdf.internal.pageSize.getWidth();
    const pageHeight = pdf.internal.pageSize.getHeight();
    pdf.setFontSize(12);
    pdf.text('Rapport financier Finova', 8, 8);
    pdf.setFontSize(9);
    pdf.text(`Date: ${generatedAt}`, 8, 13);
    pdf.text(`Généré par: ${author}`, 8, 17);
    pdf.addImage(imgData, 'PNG', 8, 22, pageWidth - 16, pageHeight - 30, '', 'FAST');
    const filename = `dashboard_${Date.now()}.pdf`;
    const blob = pdf.output('blob');
    downloadBlob(blob, filename);
    await persistReportExport({ format: 'pdf', filename, blob, type: 'dashboard_pdf' });
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

    const filename = `kpi_export_${Date.now()}.docx`;
    const blob = new Blob([html], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
    downloadBlob(blob, filename);
    persistReportExport({ format: 'docx', filename, blob, type: 'kpi_docx' }).catch(() => {});
    toast('Export document généré.', 'success');
  }

  function bindExportButtons() {
    document.getElementById('exportKpiCsv')?.addEventListener('click', exportKpiCsv);
    document.getElementById('exportKpiImage')?.addEventListener('click', () => {
      exportKpiImage().catch(() => toast('Export image impossible.', 'error'));
    });
    document.getElementById('exportKpiJpg')?.addEventListener('click', () => {
      exportKpiJpg().catch(() => toast('Export JPG impossible.', 'error'));
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
        const nextView = chip.getAttribute('data-view') || 'global';
        setActiveView(nextView, true);
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
    document.addEventListener('app:logout', () => {
      if (logoutInProgress) return;
      logoutInProgress = true;
      fetchSequence += 1;
      clearDashboard(sessionExpiredMessage);
    });

    window.addEventListener('storage', (event) => {
      if (event.key !== 'user') return;
      if (event.newValue) return;
      forceLogout(sessionExpiredMessage);
    });

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) return;
      if (hasLiveSession()) return;
      forceLogout(sessionExpiredMessage);
    });

    window.addEventListener('offline', () => {
      fetchSequence += 1;
      clearDashboard('Connexion perdue. Les données sont masquées jusqu’à reconnexion.');
    });

    window.addEventListener('online', () => {
      if (!hasLiveSession()) return;
      loadData(false).catch(() => {
        clearDashboard('Impossible de recharger les données après reconnexion.');
      });
    });

    bindFilters();
    bindAi();
    bindExportButtons();
    bindThemeRepaint();
    setActiveView(state.activeFocus || 'global', false);

    await loadData(false);
  }

  boot().catch((error) => {
    clearDashboard(error?.message || 'Erreur inattendue.');
  });
})();


