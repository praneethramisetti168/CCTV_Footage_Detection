/* ============================================================
   Store Intelligence System — Dashboard JavaScript
   WebSocket client + Chart.js + UI updates
   ============================================================ */

'use strict';

// ─── Config ──────────────────────────────────────────────────
const WS_URL    = `ws://${location.host}/ws/live-events`;
const API_BASE  = '/api/v1';
const MAX_EVENTS = 200;   // keep in memory
const MAX_FEED   = 40;    // show in feed
const MAX_ANOM   = 50;

// ─── State ──────────────────────────────────────────────────
const state = {
  ws: null,
  connected: false,
  reconnectDelay: 1500,
  events: [],          // full event buffer
  anomalies: [],       // full anomaly buffer
  analytics: {},       // latest analytics snapshot
  currentView: 'overview',
  eventFilter: '',
  anomalyFilter: '',
  anomalyCountBySev: { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 },
};

// ─── DOM refs ────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ─── Chart.js setup ─────────────────────────────────────────
Chart.defaults.color = '#64748b';
Chart.defaults.borderColor = 'rgba(255,255,255,0.06)';
Chart.defaults.font.family = "'Inter', sans-serif";

let footfallChart = null;
let zoneChart     = null;

function initFootfallChart() {
  const ctx = $('footfall-chart').getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, 180);
  grad.addColorStop(0, 'rgba(99,102,241,0.35)');
  grad.addColorStop(1, 'rgba(99,102,241,0.0)');

  footfallChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'Active People',
        data: [],
        borderColor: '#6366f1',
        backgroundColor: grad,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        fill: true,
        tension: 0.4,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: 300 },
      plugins: { legend: { display: false }, tooltip: {
        backgroundColor: 'rgba(17,24,39,0.95)',
        borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
        callbacks: { title: items => items[0].label }
      }},
      scales: {
        x: { ticks: { maxTicksLimit: 8, font: { size: 10 } }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { stepSize: 1, font: { size: 10 } } }
      }
    }
  });
}

function initZoneChart() {
  const ctx = $('zone-chart').getContext('2d');
  zoneChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: [],
      datasets: [
        {
          label: 'Current',
          data: [],
          backgroundColor: [
            'rgba(57,255,20,0.6)', 'rgba(255,165,0,0.6)',
            'rgba(30,144,255,0.6)', 'rgba(255,0,200,0.6)', 'rgba(220,0,0,0.6)'
          ],
          borderColor: [
            '#39ff14', '#ffa500', '#1e90ff', '#ff00c8', '#dc0000'
          ],
          borderWidth: 1, borderRadius: 4,
        },
        {
          label: 'Capacity',
          data: [],
          backgroundColor: 'rgba(255,255,255,0.05)',
          borderColor: 'rgba(255,255,255,0.12)',
          borderWidth: 1, borderRadius: 4,
          type: 'bar',
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: 400 },
      plugins: { legend: { display: false }, tooltip: {
        backgroundColor: 'rgba(17,24,39,0.95)',
        borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
      }},
      scales: {
        x: { ticks: { font: { size: 10 } }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { stepSize: 1, font: { size: 10 } } }
      }
    }
  });
}

// ─── WebSocket ───────────────────────────────────────────────
function connectWS() {
  try {
    state.ws = new WebSocket(WS_URL);
  } catch (e) {
    scheduleReconnect();
    return;
  }

  state.ws.onopen = () => {
    state.connected = true;
    state.reconnectDelay = 1500;
    setConnectionStatus(true);
    console.log('[WS] Connected');
  };

  state.ws.onmessage = ({ data }) => {
    let msg;
    try { msg = JSON.parse(data); } catch { return; }
    handleMessage(msg);
  };

  state.ws.onclose = () => {
    state.connected = false;
    setConnectionStatus(false);
    scheduleReconnect();
  };

  state.ws.onerror = () => {
    state.ws.close();
  };
}

function scheduleReconnect() {
  setTimeout(() => {
    console.log(`[WS] Reconnecting in ${state.reconnectDelay}ms…`);
    connectWS();
    state.reconnectDelay = Math.min(state.reconnectDelay * 1.5, 10000);
  }, state.reconnectDelay);
}

function sendPing() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send('ping');
  }
}

// ─── Message handler ─────────────────────────────────────────
function handleMessage(msg) {
  switch (msg.type) {
    case 'analytics':
      state.analytics = msg.data;
      updateAnalytics(msg.data);
      break;
    case 'event':
      handleEvent(msg.data);
      break;
    case 'anomaly':
      handleAnomaly(msg.data);
      break;
    case 'ping':
      sendPing();
      break;
  }
}

// ─── Analytics update ────────────────────────────────────────
function updateAnalytics(data) {
  // Stat cards
  animateNumber('active-persons', data.active_persons ?? 0);
  animateNumber('total-persons',  data.total_persons_today ?? 0);
  animateNumber('pipeline-fps',   Math.round(data.fps ?? 0));

  // Sidebar
  $('sb-active').textContent  = data.active_persons ?? 0;
  $('sb-total').textContent   = data.total_persons_today ?? 0;

  // Footfall timeseries
  if (data.footfall_timeseries?.length && footfallChart) {
    const ts = data.footfall_timeseries;
    footfallChart.data.labels   = ts.map(p => p.time);
    footfallChart.data.datasets[0].data = ts.map(p => p.count);
    footfallChart.update('none');
  }

  // Zone chart
  if (data.zone_occupancy && zoneChart) {
    const zones  = Object.entries(data.zone_occupancy);
    zoneChart.data.labels = zones.map(([, z]) => z.name.split(' ')[0]);
    zoneChart.data.datasets[0].data = zones.map(([, z]) => z.count);
    zoneChart.data.datasets[1].data = zones.map(([, z]) => z.max_capacity);
    zoneChart.update('none');
  }

  // Heatmap zones
  if (data.zone_occupancy) {
    updateHeatmap(data.zone_occupancy);
  }
}

// ─── Event handling ──────────────────────────────────────────
function handleEvent(evt) {
  // Skip FRAME_PROCESSED to avoid noise
  if (evt.event_type === 'FRAME_PROCESSED') return;

  state.events.unshift(evt);
  if (state.events.length > MAX_EVENTS) state.events.pop();

  // Update badges
  const evtBadge = $('events-badge');
  evtBadge.textContent = Math.min(state.events.length, 999);

  // Update feeds
  renderEventFeed();
  renderLogTable();
}

function renderEventFeed() {
  const feed = $('event-feed-list');
  const visible = state.events.slice(0, MAX_FEED);

  // Only update top — prepend new row
  if (state.events.length > 0) {
    const evt = state.events[0];
    if (!state.eventFilter || evt.event_type.includes(state.eventFilter)) {
      const row = buildEventRow(evt);
      feed.insertBefore(row, feed.firstChild);
      // Trim old rows
      while (feed.children.length > MAX_FEED) {
        feed.removeChild(feed.lastChild);
      }
    }
  }
}

function buildEventRow(evt) {
  const row = document.createElement('div');
  row.className = 'event-row';

  const pill = document.createElement('span');
  pill.className = 'event-type-pill ' + getPillClass(evt.event_type);
  pill.textContent = evt.event_type.replace('_', ' ');

  const pid = document.createElement('span');
  pid.className = 'event-pid';
  pid.textContent = evt.person_id ? evt.person_id.substring(0, 12) : '—';

  const zone = document.createElement('span');
  zone.className = 'event-zone';
  zone.textContent = evt.zone_id ? `→ ${evt.zone_id}` : '';

  const ts = document.createElement('span');
  ts.className = 'event-time';
  ts.textContent = formatTime(evt.timestamp);

  row.append(pill, pid, zone, ts);
  return row;
}

function getPillClass(type) {
  if (type.includes('ENTERED')) return 'pill-entered';
  if (type.includes('EXITED'))  return 'pill-exited';
  if (type.includes('ZONE'))    return 'pill-zone';
  return 'pill-default';
}

// ─── Anomaly handling ────────────────────────────────────────
let totalAnomalyCount = 0;

function handleAnomaly(anom) {
  state.anomalies.unshift(anom);
  if (state.anomalies.length > MAX_ANOM) state.anomalies.pop();

  totalAnomalyCount++;
  state.anomalyCountBySev[anom.severity] = (state.anomalyCountBySev[anom.severity] || 0) + 1;

  // Badges
  $('anomalies-badge').textContent = totalAnomalyCount;
  $('sb-anomalies').textContent    = totalAnomalyCount;
  $('anomaly-count').textContent   = totalAnomalyCount;
  $('unresolved-badge').textContent = `${totalAnomalyCount} active`;

  // Severity counters
  $('cnt-critical').textContent = state.anomalyCountBySev.CRITICAL || 0;
  $('cnt-high').textContent     = state.anomalyCountBySev.HIGH     || 0;
  $('cnt-medium').textContent   = state.anomalyCountBySev.MEDIUM   || 0;
  $('cnt-low').textContent      = state.anomalyCountBySev.LOW      || 0;

  renderAnomalyFeed(anom);
  renderAnomalyCards();
  showToast(anom);
}

function renderAnomalyFeed(anom) {
  const feed = $('anomaly-feed-list');

  // Remove empty state
  const empty = feed.querySelector('.empty-state');
  if (empty) empty.remove();

  const row = document.createElement('div');
  row.className = `anomaly-row ${anom.severity}`;
  row.innerHTML = `
    <div class="anomaly-header">
      <span class="sev-badge sev-${anom.severity}">${anom.severity}</span>
      <span class="anomaly-type">${anom.anomaly_type}</span>
    </div>
    <div class="anomaly-desc">${anom.description}</div>
    <div class="anomaly-meta">${formatTime(anom.timestamp)} ${anom.zone_id ? `· ${anom.zone_id}` : ''}</div>
  `;
  feed.insertBefore(row, feed.firstChild);
  while (feed.children.length > 15) feed.removeChild(feed.lastChild);
}

function renderAnomalyCards() {
  const list = $('anomaly-cards-list');
  const empty = list.querySelector('.empty-state');
  if (empty) empty.remove();

  // Rebuild (simplistic — for demo quality this is fine)
  list.innerHTML = '';
  for (const anom of state.anomalies.slice(0, 20)) {
    if (state.anomalyFilter && anom.severity !== state.anomalyFilter) continue;
    const card = document.createElement('div');
    card.className = `anomaly-card ${anom.severity}`;
    card.innerHTML = `
      <div class="anomaly-card-header">
        <span class="sev-badge sev-${anom.severity}">${anom.severity}</span>
        <span class="anomaly-type">${anom.anomaly_type}</span>
      </div>
      <div class="anomaly-card-body">${anom.description}</div>
      <div class="anomaly-card-footer">
        <span>${anom.zone_id || 'Store-wide'}</span>
        <span>${formatTime(anom.timestamp)}</span>
      </div>
    `;
    list.appendChild(card);
  }
}

// ─── Heatmap ─────────────────────────────────────────────────
const ZONE_LAYOUT = {
  entrance: { x: 0,   y: 0,   w: 213, h: 240, color: '#39ff14' },
  checkout: { x: 213, y: 0,   w: 214, h: 240, color: '#ffa500' },
  aisle_1:  { x: 0,   y: 240, w: 213, h: 240, color: '#1e90ff' },
  aisle_2:  { x: 213, y: 240, w: 214, h: 240, color: '#ff00c8' },
  storage:  { x: 427, y: 0,   w: 213, h: 480, color: '#dc0000' },
};

let hmZoneEls = {};
let legendEls = {};

function initHeatmap() {
  const map = $('store-map');
  const legend = $('zone-legend');
  map.innerHTML = '';
  legend.innerHTML = '';
  hmZoneEls = {};
  legendEls = {};

  for (const [zid, layout] of Object.entries(ZONE_LAYOUT)) {
    const el = document.createElement('div');
    el.className = 'hm-zone';
    el.id = `hm-${zid}`;
    Object.assign(el.style, {
      left:   layout.x + 'px',
      top:    layout.y + 'px',
      width:  layout.w + 'px',
      height: layout.h + 'px',
      border: `1px solid ${layout.color}44`,
      background: `${layout.color}15`,
    });
    el.innerHTML = `
      <div class="hm-zone-name" id="hmn-${zid}">…</div>
      <div class="hm-zone-count" id="hmc-${zid}" style="color:${layout.color}">0</div>
      <div class="hm-zone-util" id="hmu-${zid}">0%</div>
    `;
    map.appendChild(el);
    hmZoneEls[zid] = el;

    // Legend
    const li = document.createElement('div');
    li.className = 'legend-item';
    li.innerHTML = `
      <div class="legend-name" style="color:${layout.color}" id="lgn-${zid}">…</div>
      <div class="legend-bar-wrap"><div class="legend-bar" id="lgb-${zid}" style="background:${layout.color}; width:0%"></div></div>
      <div class="legend-count"><span id="lgc-${zid}">0/10</span><span id="lgu-${zid}">0%</span></div>
    `;
    legend.appendChild(li);
    legendEls[zid] = li;
  }
}

function updateHeatmap(zoneOcc) {
  for (const [zid, occ] of Object.entries(zoneOcc)) {
    const layout = ZONE_LAYOUT[zid];
    if (!layout) continue;
    const count = occ.count || 0;
    const max   = occ.max_capacity || 10;
    const util  = occ.utilization_pct || 0;
    const ratio = Math.min(count / max, 1);

    // Zone bg intensity
    const el = hmZoneEls[zid];
    if (el) {
      const alpha = (0.1 + ratio * 0.6).toFixed(2);
      el.style.background  = `${layout.color}${Math.round(ratio * 200 + 20).toString(16).padStart(2,'0')}`;
      el.style.boxShadow   = ratio > 0.5 ? `inset 0 0 20px ${layout.color}44, 0 0 15px ${layout.color}22` : 'none';
      $(`hmn-${zid}`).textContent = occ.name || zid;
      $(`hmc-${zid}`).textContent = count;
      $(`hmu-${zid}`).textContent = util + '%';
    }

    // Legend
    const bar = $(`lgb-${zid}`);
    if (bar) {
      bar.style.width = util + '%';
      $(`lgn-${zid}`).textContent = occ.name || zid;
      $(`lgc-${zid}`).textContent = `${count}/${max}`;
      $(`lgu-${zid}`).textContent = util + '%';
    }
  }
}

// ─── Log Table ───────────────────────────────────────────────
function renderLogTable() {
  const tbody = $('log-tbody');
  const filter = ($('filter-event-type').value || '').trim();
  const toShow = state.events.filter(e =>
    !filter || e.event_type === filter
  ).slice(0, 100);

  // Prepend only new top row if no filter active
  if (!filter && state.events.length > 0) {
    const evt = state.events[0];
    const tr  = document.createElement('tr');
    tr.innerHTML = `
      <td class="mono">${formatTime(evt.timestamp)}</td>
      <td><span class="event-type-pill ${getPillClass(evt.event_type)}" style="display:inline-block">${evt.event_type}</span></td>
      <td class="mono">${evt.person_id || '—'}</td>
      <td>${evt.zone_id || '—'}</td>
      <td class="mono" style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${evt.metadata ? JSON.stringify(evt.metadata).substring(0,60) : '—'}</td>
    `;
    tbody.insertBefore(tr, tbody.firstChild);
    while (tbody.children.length > 100) tbody.removeChild(tbody.lastChild);
  } else {
    // Full re-render for filter
    tbody.innerHTML = toShow.map(evt => `
      <tr>
        <td class="mono">${formatTime(evt.timestamp)}</td>
        <td><span class="event-type-pill ${getPillClass(evt.event_type)}" style="display:inline-block">${evt.event_type}</span></td>
        <td class="mono">${evt.person_id || '—'}</td>
        <td>${evt.zone_id || '—'}</td>
        <td class="mono">${evt.metadata ? JSON.stringify(evt.metadata).substring(0,60) : '—'}</td>
      </tr>
    `).join('');
  }
}

function clearEventLog() {
  state.events = [];
  $('log-tbody').innerHTML = '';
  $('event-feed-list').innerHTML = '';
  $('events-badge').textContent = 0;
}

// ─── Toast notifications ─────────────────────────────────────
const SEVER_ICON = { CRITICAL: '🚨', HIGH: '⚠️', MEDIUM: '⚡', LOW: 'ℹ️' };

function showToast(anom) {
  const container = $('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${anom.severity.toLowerCase()}`;
  toast.innerHTML = `
    <div class="toast-icon">${SEVER_ICON[anom.severity] || '⚠️'}</div>
    <div>
      <div class="toast-title">${anom.anomaly_type}</div>
      <div class="toast-body">${anom.description}</div>
    </div>
  `;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4200);
}

// ─── Navigation ──────────────────────────────────────────────
function switchView(viewId) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.sidebar-btn').forEach(b => b.classList.remove('active'));
  $(`view-${viewId}`).classList.add('active');
  $(`btn-${viewId}`).classList.add('active');
  state.currentView = viewId;
}

// ─── Utilities ───────────────────────────────────────────────
function formatTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts.includes('Z') || ts.includes('+') ? ts : ts + 'Z');
  return isNaN(d) ? ts : d.toLocaleTimeString();
}

function animateNumber(id, target) {
  const el = $(id);
  if (!el) return;
  const current = parseInt(el.textContent, 10) || 0;
  if (current === target) return;
  // Simple direct set (animation via CSS transition on stat-value)
  el.textContent = target;
}

function updateClock() {
  const now = new Date();
  const t = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  $('nav-time').textContent = t;
}

function setConnectionStatus(connected) {
  const dot   = $('status-dot');
  const label = $('status-label');
  if (connected) {
    dot.className   = 'status-dot connected';
    label.textContent = 'Live  ·  WebSocket connected';
  } else {
    dot.className   = 'status-dot error';
    label.textContent = 'Reconnecting…';
  }
}

// ─── Initial data fetch ──────────────────────────────────────
async function fetchInitialData() {
  try {
    const [analyticsResp, anomResp] = await Promise.all([
      fetch(`${API_BASE}/analytics/summary`),
      fetch(`${API_BASE}/anomalies?limit=20`),
    ]);
    if (analyticsResp.ok) {
      const data = await analyticsResp.json();
      updateAnalytics(data);
    }
    if (anomResp.ok) {
      const data = await anomResp.json();
      if (data.anomalies?.length) {
        for (const a of [...data.anomalies].reverse()) {
          state.anomalies.push(a);
          state.anomalyCountBySev[a.severity] = (state.anomalyCountBySev[a.severity]||0)+1;
          totalAnomalyCount++;
        }
        $('anomalies-badge').textContent = totalAnomalyCount;
        $('sb-anomalies').textContent    = totalAnomalyCount;
        $('anomaly-count').textContent   = totalAnomalyCount;
        $('cnt-critical').textContent    = state.anomalyCountBySev.CRITICAL || 0;
        $('cnt-high').textContent        = state.anomalyCountBySev.HIGH     || 0;
        $('cnt-medium').textContent      = state.anomalyCountBySev.MEDIUM   || 0;
        $('cnt-low').textContent         = state.anomalyCountBySev.LOW      || 0;
        renderAnomalyCards();
      }
    }
  } catch (e) {
    console.warn('[fetch] Initial data fetch failed:', e);
  }
}

// ─── Boot ────────────────────────────────────────────────────
function init() {
  // Charts
  initFootfallChart();
  initZoneChart();
  initHeatmap();

  // Nav
  document.querySelectorAll('.sidebar-btn').forEach(btn => {
    btn.addEventListener('click', () => switchView(btn.dataset.view));
  });

  // Filters
  $('filter-event-type').addEventListener('change', () => renderLogTable());
  $('filter-severity').addEventListener('change', () => {
    state.anomalyFilter = $('filter-severity').value;
    renderAnomalyCards();
  });

  // Clock
  updateClock();
  setInterval(updateClock, 1000);

  // Ping keepalive
  setInterval(sendPing, 20000);

  // WS
  connectWS();

  // Fetch bootstrap data
  fetchInitialData();

  console.log('%c Store Intelligence System ', 'background:#6366f1;color:#fff;font-size:14px;padding:4px 8px;border-radius:4px;');
}

document.addEventListener('DOMContentLoaded', init);
