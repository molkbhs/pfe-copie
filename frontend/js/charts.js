(() => {
  const currentUser = window.UICore?.bootstrapAuthenticatedPage?.({
    requireAuth: true,
    refreshProfile: true
  }) || null;

  if (!currentUser) {
    window.location.replace('index.html');
    return;
  }

  const liveUser = () => window.UICore?.getUser?.() || null;
  const hasLiveSession = () => {
    const user = liveUser();
    if (window.UICore?.isAuthenticated) return window.UICore.isAuthenticated(user);
    return Boolean(user && (user.id || user.token));
  };

  const state = {
    allKpis: [],
    allTx: [],
    filteredKpis: [],
    filteredTx: [],
    lastAnalyticsMessage: '',
    computed: null,
    activeFocus: 'all',
    tableMode: 'kpi',
    tableSort: { key: 'valeur', dir: 'desc' },
    tablePage: 1,
    pageSize: 12,
    charts: {}
  };
  const defaultEmptyMessage = 'Importez un fichier via la page Data Import pour alimenter ce tableau de bord BI.';
  const sessionExpiredMessage = 'Session expiree. Veuillez vous reconnecter.';
  let fetchSequence = 0;
  let logoutInProgress = false;

  const tableConfig = {
    kpi: {
      defaultSort: { key: 'valeur', dir: 'desc' },
      columns: [
        { key: 'kpiNom', label: 'KPI', numeric: false },
        { key: 'periode', label: 'Periode', numeric: false },
        { key: 'valeur', label: 'Valeur', numeric: true, format: 'currency' },
        { key: 'evolution', label: 'Variation %', numeric: true, format: 'percent' },
        { key: 'departementId', label: 'Departement', numeric: false },
        { key: 'source', label: 'Source', numeric: false },
        { key: 'stat_type', label: 'Type', numeric: false }
      ]
    },
    tx: {
      defaultSort: { key: 'year_month', dir: 'desc' },
      columns: [
        { key: 'year_month', label: 'Periode', numeric: false },
        { key: 'departement', label: 'Departement', numeric: false },
        { key: 'type_transaction', label: 'Type transaction', numeric: false },
        { key: 'type_depense', label: 'Type depense', numeric: false },
        { key: 'Montant', label: 'Montant', numeric: true, format: 'currency' },
        { key: 'Montant_Signe', label: 'Montant signe', numeric: true, format: 'currency' }
      ]
    }
  };

  const authHeaders = (json = false) => {
    const user = liveUser();
    if (!hasLiveSession()) return json ? { 'Content-Type': 'application/json' } : {};
    if (window.UICore?.authHeaders) {
      return window.UICore.authHeaders({ user, json });
    }
    const token = user?.token || user?.id || '';
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    if (json) headers['Content-Type'] = 'application/json';
    return headers;
  };

  const toNum = (v) => {
    const n = Number.parseFloat(v);
    return Number.isFinite(n) ? n : 0;
  };

  const normalize = (v) => String(v || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .trim();

  const isMissingDimensionValue = (v) => {
    const compact = normalize(v).replace(/[\s._\-\/–—]+/g, '');
    return !compact
      || compact === 'na'
      || compact === 'none'
      || compact === 'null'
      || compact === 'undefined'
      || compact === 'inconnu'
      || compact === 'nr'
      || compact === 'nd'
      || compact === 'nonrenseigne'
      || compact === 'sansobjet';
  };

  const escapeHtml = (v) => String(v ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');

  const groupBy = (rows, key) => rows.reduce((acc, row) => {
    const gk = String(row?.[key] ?? 'N/D');
    if (!acc[gk]) acc[gk] = [];
    acc[gk].push(row);
    return acc;
  }, {});

  const sum = (rows, key) => rows.reduce((acc, row) => acc + toNum(row?.[key]), 0);
  const avg = (rows, key) => (rows.length ? sum(rows, key) / rows.length : 0);
  const pct = (current, prev) => {
    if (!Number.isFinite(current) || !Number.isFinite(prev)) return 0;
    if (Math.abs(prev) < 1e-9) return current === 0 ? 0 : 100;
    return ((current - prev) / Math.abs(prev)) * 100;
  };
  const stdev = (rows, key) => {
    if (!rows.length) return 0;
    const m = avg(rows, key);
    const variance = rows.reduce((acc, row) => {
      const d = toNum(row?.[key]) - m;
      return acc + d * d;
    }, 0) / rows.length;
    return Math.sqrt(variance);
  };

  const formatDT = (n, digits = 2) => {
    if (!Number.isFinite(n)) return '--';
    return `${new Intl.NumberFormat('fr-TN', { maximumFractionDigits: digits }).format(n)} DT`;
  };
  const formatNumber = (n, digits = 0) => {
    if (!Number.isFinite(n)) return '--';
    return new Intl.NumberFormat('fr-TN', { maximumFractionDigits: digits }).format(n);
  };
  const formatPct = (n, digits = 1) => {
    if (!Number.isFinite(n)) return '--';
    return `${n.toFixed(digits)}%`;
  };

  const readJsonSafely = async (response) => {
    try {
      return await response.json();
    } catch (_) {
      return {};
    }
  };

  const ensureAuthorized = (response, payload) => {
    if (window.UICore?.isUnauthorizedResponse?.(response, payload)) {
      const err = new Error(payload?.message || payload?.error || sessionExpiredMessage);
      err.code = 'AUTH_REQUIRED';
      throw err;
    }
  };

  const destroyCharts = () => {
    Object.keys(state.charts).forEach((key) => {
      try {
        state.charts[key]?.destroy?.();
      } catch (_) {}
    });
    state.charts = {};
  };

  const clearDashboard = (message = defaultEmptyMessage) => {
    state.allKpis = [];
    state.allTx = [];
    state.filteredKpis = [];
    state.filteredTx = [];
    state.computed = null;
    state.lastAnalyticsMessage = message || defaultEmptyMessage;
    state.tablePage = 1;
    destroyCharts();

    const dataSubtitle = document.getElementById('dataSubtitle');
    if (dataSubtitle) dataSubtitle.textContent = message || defaultEmptyMessage;

    const lastUpdated = document.getElementById('lastUpdated');
    if (lastUpdated) lastUpdated.textContent = 'Derniere mise a jour: --';

    const summaryMetrics = document.getElementById('summaryMetrics');
    if (summaryMetrics) summaryMetrics.innerHTML = '';

    const insightCards = document.getElementById('insightCards');
    if (insightCards) insightCards.innerHTML = '';

    const kpiGrid = document.getElementById('kpiGrid');
    if (kpiGrid) kpiGrid.innerHTML = '';

    const topContributors = document.getElementById('topContributors');
    if (topContributors) topContributors.innerHTML = '<li class="contrib-item"><span>Aucune donnee</span><span>--</span></li>';

    const bottomContributors = document.getElementById('bottomContributors');
    if (bottomContributors) bottomContributors.innerHTML = '<li class="contrib-item"><span>Aucune donnee</span><span>--</span></li>';

    const tableHead = document.getElementById('tableHead');
    if (tableHead) tableHead.innerHTML = '';

    const tableTbody = document.getElementById('tableTbody');
    if (tableTbody) {
      tableTbody.innerHTML = `<tr><td class="text-center text-muted py-4">${escapeHtml(message || defaultEmptyMessage)}</td></tr>`;
    }

    const pager = document.getElementById('pager');
    if (pager) pager.innerHTML = '';

    const aiResponse = document.getElementById('aiResponse');
    if (aiResponse) {
      aiResponse.classList.remove('show');
      aiResponse.innerHTML = '';
    }

    const badges = {
      kpiCountBadge: '0',
      tblCountBadge: '0 lignes',
      activeCount: '0 filtre'
    };
    Object.entries(badges).forEach(([id, value]) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    });

    const emptyState = document.getElementById('emptyState');
    if (emptyState) emptyState.style.display = 'block';
    const emptyStateMessage = document.getElementById('emptyStateMessage');
    if (emptyStateMessage) emptyStateMessage.textContent = message || defaultEmptyMessage;
  };

  const forceLogout = (reason = sessionExpiredMessage) => {
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
  };

  const ensureSession = () => {
    if (!hasLiveSession()) {
      forceLogout(sessionExpiredMessage);
      return false;
    }
    return true;
  };

  const toast = (message, type = 'info') => {
    const stack = document.getElementById('toastStack');
    if (!stack) return;
    const node = document.createElement('div');
    node.className = `toast-item ${type}`;
    node.textContent = message;
    stack.appendChild(node);
    window.setTimeout(() => node.remove(), 3000);
  };

  const setFilterOptions = (selectId, values, formatter) => {
    const select = document.getElementById(selectId);
    const current = select.value;
    while (select.options.length > 1) select.remove(1);
    [...new Set(values.filter(Boolean))]
      .sort((a, b) => String(a).localeCompare(String(b)))
      .forEach((value) => {
        const label = formatter ? formatter(value) : value;
        select.add(new Option(label, value));
      });
    if (current) select.value = current;
  };

  const populateFilters = () => {
    setFilterOptions('fQuarter', state.allTx.map((row) => String(row.trimestre || '')).filter(Boolean), (q) => `Trimestre Q${q}`);
    setFilterOptions('fDept', state.allTx.map((row) => row.departement || row.departementId));

    const periods = [
      ...state.allTx.map((row) => row.year_month),
      ...state.allKpis.map((row) => row.periode)
    ].filter((v) => v && v !== 'global');
    setFilterOptions('fPeriod', periods);

    setFilterOptions('fTypeDepense', state.allTx.map((row) => row.type_depense));
    setFilterOptions('fTypeTrans', state.allTx.map((row) => row.type_transaction));
  };

  const readFilters = () => ({
    quarter: document.getElementById('fQuarter').value,
    dept: document.getElementById('fDept').value,
    period: document.getElementById('fPeriod').value,
    typeDepense: document.getElementById('fTypeDepense').value,
    typeTrans: document.getElementById('fTypeTrans').value,
    search: document.getElementById('fSearch').value.trim()
  });

  const globalSearchHit = (row, term) => {
    if (!term) return true;
    const text = [
      row.kpiNom,
      row.periode,
      row.departement,
      row.departementId,
      row.type_depense,
      row.type_transaction,
      row.year_month,
      row.source,
      row.stat_type,
      row.projet,
      row.client_fournisseur
    ].map(normalize).join(' ');
    return text.includes(term);
  };

  const parseGlobalKpis = (rows) => {
    const map = new Map();
    rows.forEach((row) => {
      if (String(row.periode || '').toLowerCase() === 'global') {
        map.set(normalize(row.kpiNom), toNum(row.valeur));
      }
    });

    const find = (patterns) => {
      for (const [key, value] of map.entries()) {
        if (patterns.some((pattern) => key.includes(pattern))) return value;
      }
      return 0;
    };

    return {
      total: find(['ca_total', 'ca total', 'ca']),
      revenue: find(['revenu']),
      expense: find(['depense']),
      net: find(['solde']),
      count: find(['nb_transaction', 'nombre de transaction', 'nombre_transaction', 'nb transaction']),
      avg: find(['valeur_moyenne', 'valeur moyenne', 'moyenne'])
    };
  };

  const computeMetrics = (txRows, kpiRows) => {
    const tx = txRows || [];
    const hasTx = tx.length > 0;

    const monthlyMap = groupBy(tx, 'year_month');
    const monthLabels = Object.keys(monthlyMap).filter((k) => k && k !== 'undefined').sort();
    const monthlySeries = monthLabels.map((month) => {
      const rows = monthlyMap[month] || [];
      const signed = rows.map((r) => toNum(r.Montant_Signe));
      const revenue = signed.filter((v) => v > 0).reduce((a, b) => a + b, 0);
      const expense = Math.abs(signed.filter((v) => v < 0).reduce((a, b) => a + b, 0));
      const net = revenue - expense;
      const amount = sum(rows, 'Montant');
      const count = rows.length;
      return { month, revenue, expense, net, amount, count, average: count ? amount / count : 0 };
    });

    const global = parseGlobalKpis(kpiRows);
    const totalAmount = hasTx ? sum(tx, 'Montant') : global.total;
    const totalRevenue = hasTx
      ? tx.reduce((acc, row) => acc + Math.max(0, toNum(row.Montant_Signe)), 0)
      : global.revenue;
    const totalExpense = hasTx
      ? Math.abs(tx.reduce((acc, row) => acc + Math.min(0, toNum(row.Montant_Signe)), 0))
      : Math.abs(global.expense);
    const totalNet = hasTx ? totalRevenue - totalExpense : global.net;
    const txCount = hasTx ? tx.length : global.count;
    const avgValue = txCount ? totalAmount / txCount : global.avg;

    const lastMonth = monthlySeries[monthlySeries.length - 1] || null;
    const prevMonth = monthlySeries[monthlySeries.length - 2] || null;
    const revenueTrend = lastMonth && prevMonth ? pct(lastMonth.revenue, prevMonth.revenue) : 0;
    const expenseTrend = lastMonth && prevMonth ? pct(lastMonth.expense, prevMonth.expense) : 0;
    const netTrend = lastMonth && prevMonth ? pct(lastMonth.net, prevMonth.net) : 0;
    const countTrend = lastMonth && prevMonth ? pct(lastMonth.count, prevMonth.count) : 0;
    const avgTrend = lastMonth && prevMonth ? pct(lastMonth.average, prevMonth.average) : 0;

    const ratioDepRev = totalRevenue > 0 ? (totalExpense / totalRevenue) * 100 : 0;
    const volatility = hasTx ? stdev(tx, 'Montant') : 0;

    const depTotals = Object.entries(groupBy(tx, 'departement'))
      .map(([name, rows]) => {
        const net = rows.reduce((acc, row) => acc + toNum(row.Montant_Signe), 0);
        const amount = sum(rows, 'Montant');
        return {
          name,
          net,
          amount,
          txCount: rows.length,
          share: totalAmount > 0 ? (amount / totalAmount) * 100 : 0
        };
      })
      .filter((d) => d.name && d.name !== 'undefined')
      .sort((a, b) => b.amount - a.amount);

    const expenseGroups = Object.entries(groupBy(tx, 'type_depense'))
      .map(([name, rows]) => ({ name, value: sum(rows, 'Montant') }))
      .filter((d) => !isMissingDimensionValue(d.name))
      .sort((a, b) => b.value - a.value);

    const txGroups = Object.entries(groupBy(tx, 'type_transaction'))
      .map(([name, rows]) => ({ name, count: rows.length, amount: sum(rows, 'Montant') }))
      .filter((d) => d.name && d.name !== 'undefined')
      .sort((a, b) => b.count - a.count);

    const monthlyGrowth = monthlySeries.map((m, i) => (i === 0 ? 0 : pct(m.net, monthlySeries[i - 1].net)));

    const alertCount = kpiRows.filter((row) => toNum(row.evolution) < -10).length
      + monthlySeries.filter((m) => m.net < 0).length;

    const bestDept = [...depTotals].sort((a, b) => b.net - a.net)[0] || null;
    const worstDept = [...depTotals].sort((a, b) => a.net - b.net)[0] || null;

    const kpiCards = [
      {
        key: 'ca_total',
        label: 'CA Total',
        group: 'finance',
        value: totalAmount,
        context: 'Volume global consolide',
        icon: 'bi-currency-exchange',
        format: 'currency',
        trend: revenueTrend,
        line: 'linear-gradient(90deg,#1fb5b5,#14b8a6)'
      },
      {
        key: 'revenus',
        label: 'Revenus Totaux',
        group: 'finance',
        value: totalRevenue,
        context: 'Transactions positives',
        icon: 'bi-graph-up-arrow',
        format: 'currency',
        trend: revenueTrend,
        line: 'linear-gradient(90deg,#10b981,#059669)'
      },
      {
        key: 'depenses',
        label: 'Depenses Totales',
        group: 'finance',
        value: totalExpense,
        context: 'Transactions negatives absolues',
        icon: 'bi-wallet2',
        format: 'currency',
        trend: expenseTrend,
        line: 'linear-gradient(90deg,#f97316,#ef4444)'
      },
      {
        key: 'solde_net',
        label: 'Solde Net',
        group: 'finance',
        value: totalNet,
        context: 'Revenus - depenses',
        icon: totalNet >= 0 ? 'bi-check-circle-fill' : 'bi-x-circle-fill',
        format: 'currency',
        trend: netTrend,
        line: totalNet >= 0
          ? 'linear-gradient(90deg,#10b981,#1fb5b5)'
          : 'linear-gradient(90deg,#ef4444,#f97316)'
      },
      {
        key: 'tx_count',
        label: 'Nb Transactions',
        group: 'operations',
        value: txCount,
        context: 'Volume d operations traitees',
        icon: 'bi-hash',
        format: 'number',
        trend: countTrend,
        line: 'linear-gradient(90deg,#6366f1,#8b5cf6)'
      },
      {
        key: 'avg_value',
        label: 'Valeur Moyenne',
        group: 'operations',
        value: avgValue,
        context: 'Montant moyen par transaction',
        icon: 'bi-bar-chart-line-fill',
        format: 'currency',
        trend: avgTrend,
        line: 'linear-gradient(90deg,#0ea5e9,#6366f1)'
      },
      {
        key: 'ratio_dep_rev',
        label: 'Ratio Depenses / Revenus',
        group: 'risk',
        value: ratioDepRev,
        context: 'Pression des couts',
        icon: 'bi-percent',
        format: 'percent',
        trend: -netTrend,
        line: 'linear-gradient(90deg,#f59e0b,#f97316)'
      },
      {
        key: 'volatility',
        label: 'Volatilite Montants',
        group: 'risk',
        value: volatility,
        context: 'Ecart type des montants',
        icon: 'bi-activity',
        format: 'currency',
        trend: 0,
        line: 'linear-gradient(90deg,#64748b,#94a3b8)'
      }
    ];

    return {
      totalAmount,
      totalRevenue,
      totalExpense,
      totalNet,
      txCount,
      avgValue,
      ratioDepRev,
      volatility,
      monthLabels,
      monthlySeries,
      monthlyGrowth,
      depTotals,
      expenseGroups,
      txGroups,
      kpiCards,
      alertCount,
      bestDept,
      worstDept,
      kpiRowsCount: kpiRows.length,
      txRowsCount: tx.length,
      lastMonth
    };
  };

  const trendClass = (v) => (v > 1.5 ? 'up' : v < -1.5 ? 'down' : 'flat');
  const trendIcon = (v) => {
    if (v > 1.5) return '<i class="bi bi-arrow-up-right"></i>';
    if (v < -1.5) return '<i class="bi bi-arrow-down-right"></i>';
    return '<i class="bi bi-arrow-left-right"></i>';
  };

  const cardValue = (card) => {
    if (card.format === 'currency') return formatDT(card.value);
    if (card.format === 'percent') return formatPct(card.value);
    return formatNumber(card.value, 0);
  };

  const renderSummary = (computed) => {
    document.getElementById('dataSubtitle').textContent =
      `${formatNumber(computed.txRowsCount)} transactions actives | ${formatNumber(computed.kpiRowsCount)} KPI | CA ${formatDT(computed.totalAmount)}`;

    const now = new Date();
    document.getElementById('lastUpdated').innerHTML =
      `Derniere mise a jour: <b>${now.toLocaleDateString('fr-TN')} ${now.toLocaleTimeString('fr-TN')}</b><br><span>Contexte: tableau de bord BI base sur l'import ETL</span>`;

    const summaryItems = [
      { label: 'Solde net global', value: formatDT(computed.totalNet) },
      { label: 'Ratio depenses/revenus', value: formatPct(computed.ratioDepRev) },
      { label: 'Departement le plus performant', value: computed.bestDept ? `${computed.bestDept.name} (${formatDT(computed.bestDept.net)})` : '--' },
      { label: 'Departement sous pression', value: computed.worstDept ? `${computed.worstDept.name} (${formatDT(computed.worstDept.net)})` : '--' },
      { label: 'Alertes detectees', value: formatNumber(computed.alertCount, 0) }
    ];

    document.getElementById('summaryMetrics').innerHTML = summaryItems.map((item) => `
      <li class="metric-item">
        <div class="label">${escapeHtml(item.label)}</div>
        <div class="value">${escapeHtml(item.value)}</div>
      </li>
    `).join('');
  };
  const renderKpis = (computed) => {
    const focusMap = {
      all: () => true,
      finance: (card) => card.group === 'finance',
      operations: (card) => card.group === 'operations',
      risk: (card) => card.group === 'risk'
    };
    const pick = focusMap[state.activeFocus] || focusMap.all;
    const cards = computed.kpiCards.filter(pick);

    document.getElementById('kpiCountBadge').textContent = formatNumber(cards.length, 0);
    document.getElementById('kpiGrid').innerHTML = cards.map((card) => {
      const tClass = trendClass(card.trend);
      const tLabel = `${card.trend > 0 ? '+' : ''}${card.trend.toFixed(1)}%`;
      return `
        <article class="kpi-card" style="--kpi-line:${card.line}">
          <div class="kpi-head">
            <div class="kpi-label">${escapeHtml(card.label)}</div>
            <div class="kpi-icon"><i class="bi ${escapeHtml(card.icon)}"></i></div>
          </div>
          <div class="kpi-value">${escapeHtml(cardValue(card))}</div>
          <div class="kpi-foot">
            <span class="kpi-context">${escapeHtml(card.context)}</span>
            <span class="kpi-trend ${tClass}">${trendIcon(card.trend)} ${escapeHtml(tLabel)}</span>
          </div>
        </article>
      `;
    }).join('');
  };

  const renderInsights = (computed) => {
    const riskSignal = computed.ratioDepRev > 100
      ? 'Pression couts elevee'
      : computed.totalNet < 0
        ? 'Solde net negatif'
        : computed.alertCount > 0
          ? 'Signaux a surveiller'
          : 'Situation stable';

    const cards = [
      {
        title: 'Meilleure performance',
        value: computed.bestDept ? computed.bestDept.name : 'N/D',
        note: computed.bestDept
          ? `${formatDT(computed.bestDept.net)} net | ${formatNumber(computed.bestDept.txCount, 0)} transactions`
          : 'Aucune donnee departement disponible'
      },
      {
        title: 'Zone de vigilance',
        value: computed.worstDept ? computed.worstDept.name : 'N/D',
        note: computed.worstDept
          ? `${formatDT(computed.worstDept.net)} net | action corrective recommandee`
          : 'Aucune donnee departement disponible'
      },
      {
        title: 'Signal executif',
        value: riskSignal,
        note: `${formatNumber(computed.alertCount, 0)} alertes KPI + mois negatifs detectes`
      }
    ];

    document.getElementById('insightCards').innerHTML = cards.map((card) => `
      <article class="alert-card">
        <p class="alert-title">${escapeHtml(card.title)}</p>
        <div class="alert-value">${escapeHtml(card.value)}</div>
        <div class="alert-note">${escapeHtml(card.note)}</div>
      </article>
    `).join('');
  };

  const renderContributors = (computed) => {
    const top = computed.depTotals.slice(0, 6);
    const bottom = [...computed.depTotals].sort((a, b) => a.net - b.net).slice(0, 6);

    const renderList = (rows) => (rows.length ? rows.map((item) => `
      <li class="contrib-item">
        <span>${escapeHtml(item.name)}</span>
        <span>${escapeHtml(formatDT(item.net))}</span>
      </li>
    `).join('') : '<li class="contrib-item"><span>Aucune donnee</span><span>--</span></li>');

    document.getElementById('topContributors').innerHTML = renderList(top);
    document.getElementById('bottomContributors').innerHTML = renderList(bottom);
  };

  const chartTheme = () => {
    const dark = document.documentElement.getAttribute('data-theme') === 'dark';
    return {
      text: dark ? '#cbd5e1' : '#64748b',
      grid: dark ? 'rgba(148,163,184,0.18)' : 'rgba(100,116,139,0.16)',
      tooltipBg: dark ? 'rgba(15,23,42,0.94)' : 'rgba(15,23,42,0.88)',
      colors: ['#1fb5b5', '#10b981', '#ef4444', '#6366f1', '#f59e0b', '#0ea5e9', '#f97316', '#8b5cf6']
    };
  };

  const commonChartOptions = ({ currencyAxis = true, showLegend = true } = {}) => {
    const theme = chartTheme();
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          display: showLegend,
          labels: { color: theme.text, font: { size: 11, weight: '600' }, boxWidth: 10 }
        },
        tooltip: {
          backgroundColor: theme.tooltipBg,
          borderWidth: 0,
          titleFont: { size: 12, weight: '700' },
          bodyFont: { size: 11 },
          padding: 10
        }
      },
      scales: {
        x: {
          ticks: { color: theme.text, font: { size: 10 } },
          grid: { color: theme.grid }
        },
        y: {
          ticks: {
            color: theme.text,
            font: { size: 10 },
            callback: (v) => (currencyAxis ? formatDT(Number(v), 0) : formatNumber(Number(v), 0))
          },
          grid: { color: theme.grid }
        }
      }
    };
  };

  const setChart = (id, config) => {
    if (state.charts[id]) state.charts[id].destroy();
    const canvas = document.getElementById(id);
    if (!canvas) return;
    state.charts[id] = new Chart(canvas.getContext('2d'), config);
  };

  const renderCharts = (computed) => {
    const theme = chartTheme();
    const labels = computed.monthlySeries.map((item) => item.month).slice(-12);
    const monthly = computed.monthlySeries.slice(-12);

    setChart('chartExecutiveTrend', {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Revenus',
            data: monthly.map((item) => item.revenue),
            borderColor: '#10b981',
            backgroundColor: 'rgba(16,185,129,0.14)',
            tension: 0.35,
            fill: true,
            pointRadius: 2
          },
          {
            label: 'Depenses',
            data: monthly.map((item) => item.expense),
            borderColor: '#ef4444',
            backgroundColor: 'rgba(239,68,68,0.12)',
            tension: 0.35,
            fill: true,
            pointRadius: 2
          },
          {
            label: 'Solde net',
            data: monthly.map((item) => item.net),
            borderColor: '#1fb5b5',
            backgroundColor: 'rgba(31,181,181,0.09)',
            borderWidth: 2.5,
            tension: 0.35,
            fill: false,
            pointRadius: 3
          }
        ]
      },
      options: commonChartOptions({ currencyAxis: true, showLegend: true })
    });

    const expense = computed.expenseGroups.slice(0, 8);
    setChart('chartExpenseBreakdown', {
      type: 'doughnut',
      data: {
        labels: expense.map((item) => item.name),
        datasets: [{
          data: expense.map((item) => item.value),
          backgroundColor: expense.map((_, i) => theme.colors[i % theme.colors.length]),
          borderWidth: 2,
          borderColor: document.documentElement.getAttribute('data-theme') === 'dark' ? 'rgba(15,23,42,0.5)' : '#fff'
        }]
      },
      options: {
        ...commonChartOptions({ currencyAxis: true, showLegend: true }),
        cutout: '62%',
        scales: {}
      }
    });

    const mix = computed.txGroups.slice(0, 8);
    setChart('chartTransactionMix', {
      type: 'bar',
      data: {
        labels: mix.map((item) => item.name),
        datasets: [{
          label: 'Nb transactions',
          data: mix.map((item) => item.count),
          backgroundColor: 'rgba(99,102,241,0.68)',
          borderRadius: 6
        }]
      },
      options: commonChartOptions({ currencyAxis: false, showLegend: false })
    });

    const depShare = computed.depTotals.slice(0, 8);
    setChart('chartDepartmentShare', {
      type: 'pie',
      data: {
        labels: depShare.map((item) => item.name),
        datasets: [{
          data: depShare.map((item) => item.share),
          backgroundColor: depShare.map((_, i) => theme.colors[i % theme.colors.length]),
          borderWidth: 1
        }]
      },
      options: {
        ...commonChartOptions({ currencyAxis: false, showLegend: true }),
        plugins: {
          ...commonChartOptions({ currencyAxis: false, showLegend: true }).plugins,
          tooltip: {
            ...commonChartOptions({ currencyAxis: false, showLegend: true }).plugins.tooltip,
            callbacks: { label: (ctx) => `${ctx.label}: ${formatPct(Number(ctx.raw), 1)}` }
          }
        },
        scales: {}
      }
    });

    const perf = [...computed.depTotals].sort((a, b) => b.net - a.net).slice(0, 10);
    setChart('chartDeptPerformance', {
      type: 'bar',
      data: {
        labels: perf.map((item) => item.name),
        datasets: [{
          label: 'Solde net',
          data: perf.map((item) => item.net),
          backgroundColor: perf.map((item) => (item.net >= 0 ? 'rgba(16,185,129,0.72)' : 'rgba(239,68,68,0.72)')),
          borderRadius: 6
        }]
      },
      options: {
        ...commonChartOptions({ currencyAxis: true, showLegend: false }),
        indexAxis: 'y'
      }
    });

    setChart('chartRiskSignals', {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            type: 'bar',
            label: 'Solde net',
            data: monthly.map((item) => item.net),
            backgroundColor: 'rgba(31,181,181,0.62)',
            borderRadius: 6,
            yAxisID: 'y'
          },
          {
            type: 'line',
            label: 'Variation mensuelle %',
            data: computed.monthlyGrowth.slice(-12),
            borderColor: '#f97316',
            backgroundColor: 'rgba(249,115,22,0.15)',
            borderWidth: 2,
            tension: 0.3,
            pointRadius: 2,
            yAxisID: 'y1'
          }
        ]
      },
      options: {
        ...commonChartOptions({ currencyAxis: true, showLegend: true }),
        scales: {
          x: {
            ticks: { color: theme.text, font: { size: 10 } },
            grid: { color: theme.grid }
          },
          y: {
            ticks: { color: theme.text, callback: (v) => formatDT(Number(v), 0) },
            grid: { color: theme.grid }
          },
          y1: {
            position: 'right',
            ticks: { color: theme.text, callback: (v) => `${Number(v).toFixed(0)}%` },
            grid: { drawOnChartArea: false }
          }
        }
      }
    });
  };
  const tableRows = () => (state.tableMode === 'tx'
    ? state.filteredTx.map((row) => ({ ...row }))
    : state.filteredKpis.map((row) => ({ ...row })));

  const columns = () => tableConfig[state.tableMode].columns;

  const sortRows = (rows, cols) => {
    const { key, dir } = state.tableSort;
    const col = cols.find((c) => c.key === key);
    const factor = dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      if (col?.numeric) return (toNum(a[key]) - toNum(b[key])) * factor;
      return String(a[key] ?? '').localeCompare(String(b[key] ?? '')) * factor;
    });
  };

  const formatCell = (col, value) => {
    if (value === null || value === undefined || value === '') return '--';
    if (col.format === 'currency') return formatDT(toNum(value));
    if (col.format === 'percent') return formatPct(toNum(value));
    if (typeof value === 'number') return formatNumber(value, 2);
    return String(value);
  };

  const renderTable = () => {
    const cols = columns();
    const rows = sortRows(tableRows(), cols);

    const thead = document.getElementById('tableHead');
    const tbody = document.getElementById('tableTbody');
    const pager = document.getElementById('pager');

    thead.innerHTML = `
      <tr>
        ${cols.map((col) => {
      const active = state.tableSort.key === col.key;
      const icon = active
        ? (state.tableSort.dir === 'asc' ? 'bi-caret-up-fill' : 'bi-caret-down-fill')
        : 'bi-arrow-down-up';
      return `<th class="sorting" data-col="${escapeHtml(col.key)}">${escapeHtml(col.label)} <i class="bi ${icon} sort-icon"></i></th>`;
    }).join('')}
      </tr>
    `;

    const totalRows = rows.length;
    const totalPages = Math.max(1, Math.ceil(totalRows / state.pageSize));
    if (state.tablePage > totalPages) state.tablePage = totalPages;
    const start = (state.tablePage - 1) * state.pageSize;
    const pageRows = rows.slice(start, start + state.pageSize);

    document.getElementById('tblCountBadge').textContent = `${formatNumber(totalRows, 0)} lignes`;

    if (!pageRows.length) {
      tbody.innerHTML = `<tr><td colspan="${cols.length}" class="text-center text-muted py-4">Aucune ligne a afficher</td></tr>`;
    } else {
      tbody.innerHTML = pageRows.map((row) => `
        <tr>
          ${cols.map((col) => `<td>${escapeHtml(formatCell(col, row[col.key]))}</td>`).join('')}
        </tr>
      `).join('');
    }

    pager.innerHTML = '';
    if (totalPages > 1) {
      const makeBtn = (page, icon) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        if (icon) btn.innerHTML = `<i class="bi ${icon}"></i>`;
        else btn.textContent = String(page);
        btn.classList.toggle('active', page === state.tablePage);
        btn.addEventListener('click', () => {
          state.tablePage = page;
          renderTable();
        });
        pager.appendChild(btn);
      };

      makeBtn(1, 'bi-chevron-double-left');
      if (state.tablePage > 1) makeBtn(state.tablePage - 1, 'bi-chevron-left');

      [state.tablePage - 1, state.tablePage, state.tablePage + 1]
        .filter((p) => p >= 1 && p <= totalPages)
        .forEach((p) => makeBtn(p, null));

      if (state.tablePage < totalPages) makeBtn(state.tablePage + 1, 'bi-chevron-right');
      makeBtn(totalPages, 'bi-chevron-double-right');
    }

    thead.querySelectorAll('th[data-col]').forEach((th) => {
      th.addEventListener('click', () => {
        const key = th.dataset.col;
        if (state.tableSort.key === key) {
          state.tableSort.dir = state.tableSort.dir === 'asc' ? 'desc' : 'asc';
        } else {
          const defaults = tableConfig[state.tableMode].defaultSort;
          state.tableSort = { key, dir: defaults.key === key ? defaults.dir : 'desc' };
        }
        state.tablePage = 1;
        renderTable();
      });
    });
  };

  const setTableMode = (mode) => {
    if (!tableConfig[mode]) return;
    state.tableMode = mode;
    state.tableSort = { ...tableConfig[mode].defaultSort };
    state.tablePage = 1;
    document.getElementById('tableModeKpi').classList.toggle('active', mode === 'kpi');
    document.getElementById('tableModeTxn').classList.toggle('active', mode === 'tx');
    renderTable();
  };

  let exportBusy = false;

  const downloadBlob = (blob, filename) => {
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 0);
  };

  const runExportAction = async (label, handler) => {
    if (exportBusy) {
      toast('Un export est deja en cours...', 'info');
      return;
    }
    exportBusy = true;
    try {
      await handler();
    } catch (error) {
      console.error(`[Export ${label}]`, error);
      toast(error?.message || `Export ${label} impossible`, 'danger');
    } finally {
      exportBusy = false;
    }
  };

  const kpiExportColumns = () => tableConfig.kpi.columns;

  const sortedKpiRowsForExport = () => {
    const rows = state.filteredKpis.map((row) => ({ ...row }));
    const cols = kpiExportColumns();
    const sortCfg = state.tableMode === 'kpi'
      ? state.tableSort
      : tableConfig.kpi.defaultSort;
    const col = cols.find((c) => c.key === sortCfg.key);
    const factor = sortCfg.dir === 'asc' ? 1 : -1;

    return rows.sort((a, b) => {
      if (col?.numeric) return (toNum(a[sortCfg.key]) - toNum(b[sortCfg.key])) * factor;
      return String(a[sortCfg.key] ?? '').localeCompare(String(b[sortCfg.key] ?? '')) * factor;
    });
  };

  const getKpiExportDataset = () => {
    const cols = kpiExportColumns();
    const rows = sortedKpiRowsForExport();
    if (!rows.length) {
      toast('Aucun KPI a exporter avec les filtres actuels', 'info');
      return null;
    }
    return { cols, rows };
  };

  const kpiFilename = (ext) => `kpi_export_${new Date().toISOString().slice(0, 10)}.${ext}`;

  const getDashboardCaptureTarget = () =>
    document.getElementById('dashboardCaptureArea') || document.querySelector('.analytics-wrap');

  const captureBackgroundColor = () =>
    document.documentElement.getAttribute('data-theme') === 'dark' ? '#0b1626' : '#f5fbff';

  const captureDashboardCanvas = async () => {
    if (typeof window.html2canvas !== 'function') {
      throw new Error('Bibliotheque html2canvas manquante');
    }
    const target = getDashboardCaptureTarget();
    if (!target) {
      throw new Error('Zone du tableau de bord introuvable pour la capture');
    }
    const scale = Math.min(2, Math.max(1, window.devicePixelRatio || 1.3));
    return window.html2canvas(target, {
      backgroundColor: captureBackgroundColor(),
      scale,
      useCORS: true,
      logging: false,
      width: target.scrollWidth,
      height: target.scrollHeight,
      scrollX: 0,
      scrollY: -window.scrollY,
      windowWidth: document.documentElement.clientWidth,
      windowHeight: document.documentElement.clientHeight
    });
  };

  const buildKpiCsv = (cols, rows) => {
    const header = cols.map((col) => col.label).join(',');
    const body = rows.map((row) => cols.map((col) => {
      const value = formatCell(col, row[col.key]);
      return `"${String(value).replace(/"/g, '""')}"`;
    }).join(','));
    return [header, ...body].join('\n');
  };

  const summaryMetricsForExport = () => {
    const c = state.computed;
    if (!c) return [];
    return [
      ['Revenus Totaux', formatDT(c.totalRevenue)],
      ['Depenses Totales', formatDT(c.totalExpense)],
      ['Solde Net', formatDT(c.totalNet)],
      ['Transactions', formatNumber(c.txCount, 0)],
      ['Valeur moyenne', formatDT(c.avgValue)],
      ['Ratio depenses/revenus', formatPct(c.ratioDepRev)]
    ];
  };

  const topKpisForExport = (rows, limit = 8) =>
    [...rows]
      .sort((a, b) => Math.abs(toNum(b.valeur)) - Math.abs(toNum(a.valeur)))
      .slice(0, limit);

  const exportKpiCsv = async () => {
    const dataset = getKpiExportDataset();
    if (!dataset) return;
    const csv = buildKpiCsv(dataset.cols, dataset.rows);
    downloadBlob(new Blob([csv], { type: 'text/csv;charset=utf-8;' }), kpiFilename('csv'));
    toast('Export CSV des KPI genere', 'success');
  };

  const exportKpiImage = async () => {
    const dataset = getKpiExportDataset();
    if (!dataset) return;
    toast('Generation de l image du tableau de bord...', 'info');
    const canvas = await captureDashboardCanvas();
    await new Promise((resolve, reject) => {
      canvas.toBlob((blob) => {
        if (!blob) {
          reject(new Error('Impossible de generer l image'));
          return;
        }
        downloadBlob(blob, kpiFilename('png'));
        resolve();
      }, 'image/png');
    });
    toast('Export image du tableau de bord genere', 'success');
  };

  const exportKpiPdf = async () => {
    const dataset = getKpiExportDataset();
    if (!dataset) return;
    const jsPdfCtor = window.jspdf?.jsPDF;
    if (!jsPdfCtor) {
      throw new Error('Bibliotheque jsPDF manquante');
    }

    const { rows } = dataset;
    const summaryRows = summaryMetricsForExport();
    const topRows = topKpisForExport(rows, 8);

    toast('Generation du mini-rapport PDF...', 'info');
    const pdf = new jsPdfCtor({ orientation: 'p', unit: 'mm', format: 'a4' });
    const pageWidth = pdf.internal.pageSize.getWidth();
    const pageHeight = pdf.internal.pageSize.getHeight();
    const margin = 12;
    const usableWidth = pageWidth - (margin * 2);

    let y = margin;
    pdf.setFont('helvetica', 'bold');
    pdf.setFontSize(17);
    pdf.text('Rapport KPI / Rapport analytique', margin, y);
    y += 7;

    pdf.setFont('helvetica', 'normal');
    pdf.setFontSize(10);
    pdf.setTextColor(90, 98, 110);
    pdf.text(`Date d'export: ${new Date().toLocaleDateString('fr-TN')} ${new Date().toLocaleTimeString('fr-TN')}`, margin, y);
    y += 6;
    pdf.setTextColor(33, 37, 41);

    pdf.setFont('helvetica', 'bold');
    pdf.setFontSize(12);
    pdf.text('Synthese de direction', margin, y);
    y += 5;

    pdf.setFont('helvetica', 'normal');
    pdf.setFontSize(10);
    summaryRows.forEach(([label, value]) => {
      if (y > pageHeight - margin) {
        pdf.addPage();
        y = margin;
      }
      pdf.text(`${label}: ${value}`, margin, y);
      y += 4.7;
    });

    y += 2;
    pdf.setFont('helvetica', 'bold');
    pdf.text('Principaux KPI (vue courante)', margin, y);
    y += 5;
    pdf.setFont('helvetica', 'normal');

    topRows.forEach((row, idx) => {
      if (y > pageHeight - margin) {
        pdf.addPage();
        y = margin;
      }
      const line = `${idx + 1}. ${String(row.kpiNom || '--')} | Periode: ${String(row.periode || '--')} | Valeur: ${formatDT(toNum(row.valeur))}`;
      pdf.text(line, margin, y, { maxWidth: usableWidth });
      y += 4.8;
    });

    if (y > pageHeight - 90) {
      pdf.addPage();
      y = margin;
    } else {
      y += 2;
    }
    pdf.setFont('helvetica', 'bold');
    pdf.text('Visualisation du tableau de bord', margin, y);
    y += 4;

    const canvas = await captureDashboardCanvas();
    const imgData = canvas.toDataURL('image/jpeg', 0.92);
    const imgWidth = usableWidth;
    const imgHeight = (canvas.height * imgWidth) / canvas.width;
    const maxFirstPageHeight = pageHeight - margin - y;

    pdf.addImage(imgData, 'JPEG', margin, y, imgWidth, imgHeight, undefined, 'FAST');
    let heightLeft = imgHeight - maxFirstPageHeight;

    while (heightLeft > 0) {
      pdf.addPage();
      const offsetY = margin - (imgHeight - heightLeft);
      pdf.addImage(imgData, 'JPEG', margin, offsetY, imgWidth, imgHeight, undefined, 'FAST');
      heightLeft -= (pageHeight - (margin * 2));
    }

    pdf.save(kpiFilename('pdf'));
    toast('Mini-rapport PDF genere', 'success');
  };

  const escapeXml = (v) => String(v ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');

  const exportKpiDocx = async () => {
    const dataset = getKpiExportDataset();
    if (!dataset) return;
    if (!window.JSZip) {
      throw new Error('Bibliotheque JSZip manquante');
    }
    const { rows } = dataset;
    const summaryRows = summaryMetricsForExport();
    const topRows = topKpisForExport(rows, 20);
    const nowIso = new Date().toISOString();

    const lines = [
      'Rapport KPI / Rapport analytique',
      `Date export: ${new Date().toLocaleDateString('fr-TN')} ${new Date().toLocaleTimeString('fr-TN')}`,
      '',
      'Synthese de direction'
    ];
    summaryRows.forEach(([label, value]) => lines.push(`- ${label}: ${value}`));
    lines.push('');
    lines.push('KPI - Vue courante');
    topRows.forEach((row, idx) => {
      lines.push(
        `${idx + 1}. ${String(row.kpiNom || '--')} | Periode: ${String(row.periode || '--')} | Valeur: ${formatDT(toNum(row.valeur))} | Evolution: ${formatPct(toNum(row.evolution))}`
      );
    });

    const paragraphs = lines
      .map((line) => `<w:p><w:r><w:t xml:space="preserve">${escapeXml(line)}</w:t></w:r></w:p>`)
      .join('');

    const contentTypes = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>`;
    const rootRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>`;
    const documentXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    ${paragraphs}
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>
    </w:sectPr>
  </w:body>
</w:document>`;
    const documentRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>`;
    const coreXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:dcmitype="http://purl.org/dc/dcmitype/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Rapport KPI</dc:title>
  <dc:creator>BusinessApp</dc:creator>
  <cp:lastModifiedBy>BusinessApp</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">${nowIso}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">${nowIso}</dcterms:modified>
</cp:coreProperties>`;
    const appXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
  xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>BusinessApp</Application>
</Properties>`;

    const zip = new window.JSZip();
    zip.file('[Content_Types].xml', contentTypes);
    zip.folder('_rels')?.file('.rels', rootRels);
    zip.folder('word')?.file('document.xml', documentXml);
    zip.folder('word')?.folder('_rels')?.file('document.xml.rels', documentRels);
    zip.folder('docProps')?.file('core.xml', coreXml);
    zip.folder('docProps')?.file('app.xml', appXml);

    const blob = await zip.generateAsync({ type: 'blob' });
    downloadBlob(blob, kpiFilename('docx'));
    toast('Rapport DOCX KPI genere', 'success');
  };

  const aiContext = () => {
    const computed = state.computed;
    if (!computed) return {};
    return {
      filters: readFilters(),
      top_kpis: computed.kpiCards.slice(0, 8).map((card) => ({
        label: card.label,
        value: card.value,
        trend: card.trend,
        context: card.context
      })),
      top_departments: computed.depTotals.slice(0, 6).map((row) => ({
        departement: row.name,
        net: row.net,
        amount: row.amount,
        tx_count: row.txCount
      })),
      tx_count: computed.txRowsCount,
      kpi_count: computed.kpiRowsCount,
      latest_month: computed.lastMonth?.month || null
    };
  };

  const askAI = async (question) => {
    if (!ensureSession()) return;
    const box = document.getElementById('aiResponse');
    box.classList.add('show');
    box.innerHTML = '<span class="ai-dot-anim"><span class="ai-dot"></span><span class="ai-dot"></span><span class="ai-dot"></span></span>';

    try {
      const response = await fetch('/api/chatbot/ask', {
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({ message: question, context: aiContext() })
      });
      const payload = await readJsonSafely(response);
      ensureAuthorized(response, payload);
      const answer = String(payload?.answer || 'Aucune reponse generee.');
      box.innerHTML =
        `<div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:6px;"><i class="bi bi-robot me-1"></i>Assistant BI</div>${answer
          .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
          .replace(/\n/g, '<br>')}`;
    } catch (error) {
      if (error?.code === 'AUTH_REQUIRED') {
        forceLogout(error.message || sessionExpiredMessage);
        return;
      }
      box.innerHTML = 'Connexion impossible avec le service assistant.';
    }
  };

  const applyFilters = () => {
    const f = readFilters();
    const term = normalize(f.search);

    state.filteredTx = state.allTx.filter((row) => {
      if (f.quarter && String(row.trimestre || '') !== f.quarter) return false;
      if (f.dept && String(row.departement || '') !== f.dept) return false;
      if (f.period && String(row.year_month || '') !== f.period) return false;
      if (f.typeDepense && String(row.type_depense || '') !== f.typeDepense) return false;
      if (f.typeTrans && String(row.type_transaction || '') !== f.typeTrans) return false;
      if (term && !globalSearchHit(row, term)) return false;
      return true;
    });

    state.filteredKpis = state.allKpis.filter((row) => {
      if (f.period && String(row.periode || '') !== f.period) return false;
      if (f.dept) {
        const dep = String(row.departementId || '');
        if (dep && dep !== f.dept) return false;
      }
      if (f.quarter) {
        const quarterKey = `q${f.quarter}`;
        const text = normalize(`${row.kpiNom || ''} ${row.periode || ''}`);
        if (!text.includes(quarterKey) && String(row.trimestre || '') !== f.quarter) return false;
      }
      if (term && !globalSearchHit(row, term)) return false;
      return true;
    });

    const count = Object.values(f).filter(Boolean).length;
    document.getElementById('activeCount').textContent = `${count} filtre${count > 1 ? 's' : ''}`;

    const hasData = state.filteredTx.length > 0 || state.filteredKpis.length > 0;
    if (!hasData) {
      state.computed = null;
      destroyCharts();

      const noDataMessage = (state.allTx.length === 0 && state.allKpis.length === 0)
        ? (state.lastAnalyticsMessage || defaultEmptyMessage)
        : 'Aucune donnee ne correspond aux filtres selectionnes.';

      const dataSubtitle = document.getElementById('dataSubtitle');
      if (dataSubtitle) dataSubtitle.textContent = noDataMessage;

      const lastUpdated = document.getElementById('lastUpdated');
      if (lastUpdated) lastUpdated.textContent = 'Derniere mise a jour: --';

      const summaryMetrics = document.getElementById('summaryMetrics');
      if (summaryMetrics) summaryMetrics.innerHTML = '';

      const insightCards = document.getElementById('insightCards');
      if (insightCards) insightCards.innerHTML = '';

      const kpiGrid = document.getElementById('kpiGrid');
      if (kpiGrid) kpiGrid.innerHTML = '';

      const topContributors = document.getElementById('topContributors');
      if (topContributors) topContributors.innerHTML = '<li class="contrib-item"><span>Aucune donnee</span><span>--</span></li>';

      const bottomContributors = document.getElementById('bottomContributors');
      if (bottomContributors) bottomContributors.innerHTML = '<li class="contrib-item"><span>Aucune donnee</span><span>--</span></li>';

      const kpiCountBadge = document.getElementById('kpiCountBadge');
      if (kpiCountBadge) kpiCountBadge.textContent = '0';

      renderTable();

      const emptyState = document.getElementById('emptyState');
      if (emptyState) emptyState.style.display = 'block';
      const emptyMessage = document.getElementById('emptyStateMessage');
      if (emptyMessage) emptyMessage.textContent = noDataMessage;
      return;
    }

    state.computed = computeMetrics(state.filteredTx, state.filteredKpis);
    renderSummary(state.computed);
    renderKpis(state.computed);
    renderInsights(state.computed);
    renderContributors(state.computed);
    renderCharts(state.computed);
    renderTable();

    const emptyState = document.getElementById('emptyState');
    if (emptyState) emptyState.style.display = 'none';
  };
  const refreshKpis = async () => {
    if (!ensureSession()) return;
    const btn = document.getElementById('btnRefreshKPI');
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Recalcul...';

    try {
      const response = await fetch('/api/analytics/kpi-refresh', {
        method: 'POST',
        headers: authHeaders(false)
      });
      const payload = await readJsonSafely(response);
      ensureAuthorized(response, payload);
      if (payload?.success) {
        toast(`${payload.inserted || 0} KPI recalcules`, 'success');
        await fetchData();
      } else {
        toast(payload?.error || 'Recalcul KPI echoue', 'danger');
      }
    } catch (error) {
      if (error?.code === 'AUTH_REQUIRED') {
        forceLogout(error.message || sessionExpiredMessage);
        return;
      }
      toast('Erreur reseau pendant le recalcul KPI', 'danger');
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  };

  const fetchData = async () => {
    if (!ensureSession()) return;
    const currentFetch = ++fetchSequence;
    clearDashboard('Chargement des donnees analytiques...');

    try {
      const [kpiRes, txRes] = await Promise.all([
        fetch('/api/kpi?limit=1000', { headers: authHeaders(false) }),
        fetch('/api/analytics/data', { headers: authHeaders(false) })
      ]);
      const [kpiPayload, txPayload] = await Promise.all([readJsonSafely(kpiRes), readJsonSafely(txRes)]);
      ensureAuthorized(kpiRes, kpiPayload);
      ensureAuthorized(txRes, txPayload);
      if (currentFetch !== fetchSequence || !ensureSession()) return;

      if (!kpiRes.ok || kpiPayload?.success === false) {
        throw new Error(kpiPayload?.message || kpiPayload?.error || 'API KPI indisponible');
      }
      if (!txRes.ok || txPayload?.success === false) {
        throw new Error(txPayload?.message || txPayload?.error || 'API analytics indisponible');
      }

      state.allKpis = Array.isArray(kpiPayload.kpis) ? kpiPayload.kpis : [];
      state.allTx = Array.isArray(txPayload.data) ? txPayload.data : [];
      state.lastAnalyticsMessage = txPayload?.analytics_ready === false
        ? (txPayload?.message || 'Aucune donnee analytics traitee disponible.')
        : '';

      if (!state.allKpis.length && state.allTx.length) {
        const refreshRes = await fetch('/api/analytics/kpi-refresh', {
          method: 'POST',
          headers: authHeaders(false)
        }).catch(() => null);
        if (refreshRes) {
          const refreshPayload = await readJsonSafely(refreshRes);
          ensureAuthorized(refreshRes, refreshPayload);
        }
        if (currentFetch !== fetchSequence || !ensureSession()) return;

        const refreshed = await fetch('/api/kpi?limit=1000', { headers: authHeaders(false) });
        const refreshedPayload = await readJsonSafely(refreshed);
        ensureAuthorized(refreshed, refreshedPayload);
        if (refreshed.ok) {
          state.allKpis = Array.isArray(refreshedPayload.kpis) ? refreshedPayload.kpis : [];
        }
      }
      if (currentFetch !== fetchSequence || !ensureSession()) return;

      populateFilters();
      applyFilters();
      if (txPayload?.analytics_ready === false) {
        toast(state.lastAnalyticsMessage, 'info');
      } else {
        toast(`${formatNumber(state.allTx.length, 0)} transactions chargees`, 'success');
      }
    } catch (error) {
      if (error?.code === 'AUTH_REQUIRED') {
        forceLogout(error.message || sessionExpiredMessage);
        return;
      }
      if (currentFetch !== fetchSequence) return;
      const message = error?.message || 'Erreur lors du chargement des analytics';
      clearDashboard(message);
      toast(message, 'danger');
    }
  };

  const bindUI = () => {
    document.querySelectorAll('#focusChips .focus-chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        document.querySelectorAll('#focusChips .focus-chip').forEach((c) => c.classList.remove('active'));
        chip.classList.add('active');
        state.activeFocus = chip.dataset.focus || 'all';
        if (state.computed) renderKpis(state.computed);
      });
    });

    ['fQuarter', 'fDept', 'fPeriod', 'fTypeDepense', 'fTypeTrans'].forEach((id) => {
      document.getElementById(id).addEventListener('change', () => {
        state.tablePage = 1;
        applyFilters();
      });
    });

    document.getElementById('fSearch').addEventListener('input', () => {
      state.tablePage = 1;
      applyFilters();
    });

    document.getElementById('btnReset').addEventListener('click', () => {
      ['fQuarter', 'fDept', 'fPeriod', 'fTypeDepense', 'fTypeTrans'].forEach((id) => {
        document.getElementById(id).value = '';
      });
      document.getElementById('fSearch').value = '';
      state.tablePage = 1;
      applyFilters();
      toast('Filtres reinitialises', 'info');
    });

    const bindExportItem = (id, label, handler) => {
      const node = document.getElementById(id);
      if (!node) return;
      node.addEventListener('click', () => {
        runExportAction(label, handler);
      });
    };
    bindExportItem('exportKpiImage', 'image', exportKpiImage);
    bindExportItem('exportKpiPdf', 'pdf', exportKpiPdf);
    bindExportItem('exportKpiDocx', 'docx', exportKpiDocx);
    bindExportItem('exportKpiCsv', 'csv', exportKpiCsv);
    document.getElementById('btnRefreshKPI').addEventListener('click', refreshKpis);

    document.getElementById('tableModeKpi').addEventListener('click', () => setTableMode('kpi'));
    document.getElementById('tableModeTxn').addEventListener('click', () => setTableMode('tx'));

    document.getElementById('aiSendBtn').addEventListener('click', () => {
      const q = document.getElementById('aiQuestion').value.trim();
      if (q) askAI(q);
    });

    document.getElementById('aiQuestion').addEventListener('keydown', (evt) => {
      if (evt.key === 'Enter' && evt.ctrlKey) {
        const q = evt.target.value.trim();
        if (q) askAI(q);
      }
    });

    document.querySelectorAll('#aiSuggestions .ai-chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        const text = chip.textContent.trim();
        document.getElementById('aiQuestion').value = text;
        askAI(text);
      });
    });

    document.addEventListener('themeChanged', () => {
      if (state.computed) {
        window.setTimeout(() => renderCharts(state.computed), 100);
      }
    });
  };

  window.addEventListener('pageshow', (event) => {
    if (!hasLiveSession()) {
      forceLogout(sessionExpiredMessage);
      return;
    }
    if (event.persisted) {
      fetchData();
    }
  });

  window.addEventListener('storage', (event) => {
    if (event.key && event.key !== 'user') return;
    if (!hasLiveSession()) {
      forceLogout('Session terminee. Veuillez vous reconnecter.');
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !hasLiveSession()) {
      forceLogout(sessionExpiredMessage);
    }
  });

  document.addEventListener('app:logout', () => {
    fetchSequence += 1;
    clearDashboard('Session terminee.');
  });

  bindUI();
  clearDashboard('Chargement des donnees analytiques...');
  if (ensureSession()) {
    fetchData();
  }
})();
