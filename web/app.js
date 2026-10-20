/* Net Assistant — панель-мини-приложение.
   Ванильный JS, без сборки. Экран фиксированный, прокручиваются только
   внутренние области. Переписка хранится в ядре (data/history). */

'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const tg = window.Telegram && window.Telegram.WebApp;

// Токен можно передать ссылкой (?token=...) — удобно для скриптов и агента ВМ.
const urlToken = new URLSearchParams(location.search).get('token');
if (urlToken) {
  localStorage.setItem('na_token', urlToken);
  history.replaceState(null, '', location.pathname + location.hash);
}

const state = {
  token: localStorage.getItem('na_token') || '',
  session: '',
  devices: [],
  actions: {},
  pending: [],
  models: null,
  recorder: null,
  chunks: [],
  busy: false,
  recording: false,
  voice: localStorage.getItem('na_voice') === '1',
  screen: 'chat',
};

const SCREENS = {
  chat: { title: 'Ассистент', icon: 'edit', action: 'Новый диалог' },
  devices: { title: 'Устройства', icon: 'refresh', action: 'Обновить' },
  activity: { title: 'Журнал', icon: 'refresh', action: 'Обновить' },
  more: { title: 'Ещё', icon: null },
};

const SUGGESTIONS = [
  'Сделай скриншот',
  'Кто подключён к Wi-Fi?',
  'Сколько заряда у телефона?',
  'Поставь громкость 30%',
];

const EVENT_LABELS = {
  agent_connected: 'подключился',
  agent_disconnected: 'отключился',
  command: 'команда',
  confirmed: 'подтверждено',
  model_switch: 'смена модели',
  model_reset: 'сброс модели',
  chat: 'диалог',
};

/* ─────────────────────────── утилиты ─────────────────────────── */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function shortModel(model) {
  return String(model || '').split('/').pop();
}

function fmtTime(ts) {
  if (!ts) return '';
  const date = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
  if (Number.isNaN(date.getTime())) return '';
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  const time = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  if (sameDay) return time;
  const day = date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
  return `${day} ${time}`;
}

function haptic(type = 'light') {
  try { tg && tg.HapticFeedback && tg.HapticFeedback.impactOccurred(type); } catch { /* noop */ }
}

let toastTimer = null;
function toast(message) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add('hidden'), 2600);
}

/* ─────────────────────────── API ─────────────────────────── */

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), Authorization: `Bearer ${state.token}` };
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  let response;
  try {
    response = await fetch(path, { ...options, headers, cache: 'no-store' });
  } catch (error) {
    throw new Error('нет связи с ядром');
  }
  if (response.status === 401) {
    if ($('#login').classList.contains('hidden')) showLogin('Токен неверный или истёк');
    throw new Error('unauthorized');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

async function apiBlob(path, options = {}) {
  const headers = { ...(options.headers || {}), Authorization: `Bearer ${state.token}` };
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(path, { ...options, headers, cache: 'no-store' });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${response.status}`);
  }
  return response.blob();
}

/* ─────────────────────────── Telegram ─────────────────────────── */

function initTelegram() {
  if (!tg) return;
  if (tg.initData) {
    document.body.classList.add('tg-mode');
    const line = $('#version-line');
    if (line) line.textContent = 'Версия 2.0 · панель в Telegram';
  }
  try { tg.ready(); tg.expand(); } catch { /* noop */ }
  try { tg.disableVerticalSwipes && tg.disableVerticalSwipes(); } catch { /* noop */ }
  try { tg.setHeaderColor('#111318'); tg.setBackgroundColor('#111318'); } catch { /* noop */ }
  try { tg.BackButton.onClick(() => showScreen('chat')); } catch { /* noop */ }

  const applyHeight = () => {
    const height = tg.viewportStableHeight || window.innerHeight;
    document.documentElement.style.setProperty('--app-height', `${height}px`);
  };
  applyHeight();
  try { tg.onEvent('viewportChanged', applyHeight); } catch { /* noop */ }
  window.addEventListener('resize', applyHeight);
}

async function telegramLogin() {
  if (!tg || !tg.initData) return;
  try {
    const response = await fetch('/tg/auth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data: tg.initData }),
    });
    if (!response.ok) return;
    const data = await response.json();
    if (data.token) {
      state.token = data.token;
      localStorage.setItem('na_token', data.token);
    }
  } catch { /* покажем форму токена */ }
}

function chatSource() {
  // В мини-приложении реплики и так в Telegram — бот их не пересылает.
  return tg && tg.initData ? 'tg' : 'web';
}

async function pickSession() {
  if (state.session) return;
  const userId = tg && tg.initDataUnsafe && tg.initDataUnsafe.user
    ? tg.initDataUnsafe.user.id : null;
  if (userId) {
    state.session = `tg-${userId}`;       // общая переписка с ботом в личном чате
    return;
  }
  let session = localStorage.getItem('na_session');
  if (!session || session.startsWith('web-')) {
    // Одна переписка на всех устройствах: сессию задаёт ядро.
    try {
      const data = await api('/session');
      session = data.session || session;
    } catch { /* ядро недоступно — останемся на локальной */ }
  }
  if (!session) session = `web-${Math.random().toString(36).slice(2, 10)}`;
  localStorage.setItem('na_session', session);
  state.session = session;
}

/* ─────────────────────────── вход ─────────────────────────── */

function showLogin(message = '') {
  $('#login').classList.remove('hidden');
  $('#app').classList.add('hidden');
  $('#login-error').textContent = message;
  if (state.token) $('#token').value = state.token;
  setTimeout(() => { try { $('#token').focus(); } catch { /* noop */ } }, 120);
}

function showApp() {
  $('#login').classList.add('hidden');
  $('#app').classList.remove('hidden');
}

/* Экран можно задать ссылкой: #devices, #activity, #more — удобно для
   закладок и для агента ВМ, который открывает нужный раздел без кликов. */
function initialScreen() {
  const name = (location.hash || '').replace(/^#/, '');
  return SCREENS[name] ? name : 'chat';
}

let syncTimer = null;

function startSync() {
  if (syncTimer) return;
  syncTimer = setInterval(refreshHistory, 4000);
}

async function boot() {
  initTelegram();
  if (!state.token && tg && tg.initData) await telegramLogin();
  if (!state.token) return showLogin();

  await pickSession();
  try {
    await refreshMeta(true);
  } catch (error) {
    if (error.message === 'unauthorized') return;
  }
  await Promise.all([loadModels(), loadHistory()]);
  showApp();
  showScreen(initialScreen(), { silent: true });
  updateAppbar();
  syncVoiceToggle();
  startSync();
  setInterval(() => {
    if (!document.hidden && !$('#app').classList.contains('hidden')) refreshMeta();
  }, 6000);
}

/* ─────────────────────────── экраны ─────────────────────────── */

function showScreen(name, options = {}) {
  if (!SCREENS[name]) return;
  state.screen = name;
  if (!options.silent && location.hash !== `#${name}`) {
    history.replaceState(null, '', `${location.pathname}${location.search}#${name}`);
  }
  $$('.screen').forEach((node) => node.classList.toggle('active', node.dataset.screen === name));
  $$('.nav-item').forEach((node) => node.classList.toggle('active', node.dataset.screen === name));
  $('#composer').classList.toggle('hidden', name !== 'chat');

  if (tg && tg.BackButton) {
    try { name === 'chat' ? tg.BackButton.hide() : tg.BackButton.show(); } catch { /* noop */ }
  }
  updateAppbar();

  if (name === 'chat') scrollBottom();
  if (name === 'devices') renderDevices();
  if (name === 'activity') refreshMeta();
  if (name === 'more') updateModelLabel();
  reportUiMap();
}

/* Координаты кнопок для агента ВМ: он нажимает точно, а не «на глаз».
   Панель присылает их ядру, планировщик добавляет к описанию снимка. */
let uiMapTimer = null;

function reportUiMap() {
  if (!state.token) return;
  clearTimeout(uiMapTimer);
  uiMapTimer = setTimeout(() => {
    try {
      const nodes = $$('.nav-item, .screen.active button, .screen.active .device-card, '
        + '.screen.active a, .screen.active input, .screen.active textarea');
      const chromeX = window.screenX + Math.max(0, (window.outerWidth - window.innerWidth) / 2);
      const chromeY = window.screenY + Math.max(0, window.outerHeight - window.innerHeight);
      // Firefox знает точное начало области страницы на экране (без рамок окна).
      const originX = typeof window.mozInnerScreenX === 'number' ? window.mozInnerScreenX : chromeX;
      const originY = typeof window.mozInnerScreenY === 'number' ? window.mozInnerScreenY : chromeY;
      const targets = [];
      nodes.forEach((node) => {
        const rect = node.getBoundingClientRect();
        if (rect.width < 4 || rect.height < 4) return;
        const label = (node.getAttribute('aria-label') || node.innerText
          || node.placeholder || node.title || '').trim().split('\n')[0].slice(0, 60);
        if (!label) return;
        targets.push({
          label,
          x: Math.round(originX + rect.left + rect.width / 2),
          y: Math.round(originY + rect.top + rect.height / 2),
        });
      });
      fetch('/ui/map', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${state.token}` },
        body: JSON.stringify({
          screen: state.screen,
          width: window.innerWidth,
          height: window.innerHeight,
          targets: targets.slice(0, 120),
        }),
      }).catch(() => {});
    } catch { /* noop */ }
  }, 400);
}

function updateAppbar() {  const config = SCREENS[state.screen] || SCREENS.chat;
  $('#screen-title').textContent = config.title;
  const button = $('#appbar-action');
  if (!config.icon) {
    button.classList.add('hidden');
    return;
  }
  button.classList.remove('hidden');
  button.title = config.action || '';
  button.innerHTML = `<svg><use href="#i-${config.icon}"></use></svg>`;
  button.onclick = () => {
    haptic();
    if (config.icon === 'edit') newChat();
    else refreshMeta();
  };
}

function setOnline(ok) {
  const status = $('#status');
  status.classList.toggle('off', !ok);
  const online = state.devices.filter((d) => d.online).length;
  const total = state.devices.length;
  const side = $('#side-status');
  const sideModel = $('#side-model');

  if (!ok) {
    $('#status-text').textContent = 'нет связи с ядром';
    if (side) side.textContent = 'нет связи с ядром';
    return;
  }
  const parts = [];
  parts.push(total ? `${online} из ${total} на связи` : 'нет устройств');
  if (state.models && state.models.current) parts.push(shortModel(state.models.current));
  $('#status-text').textContent = parts.join(' · ');
  if (side) side.textContent = total ? `${online} из ${total} на связи` : 'нет устройств';
  if (sideModel) {
    sideModel.textContent = state.models && state.models.current
      ? `ИИ: ${shortModel(state.models.current)}` : '';
  }
}

/* ─────────────────────────── данные ─────────────────────────── */

async function refreshMeta(initial = false) {
  const jobs = [api('/devices'), api('/pending'), api('/audit?limit=40')];
  if (initial || !Object.keys(state.actions).length) jobs.push(api('/actions'));
  const [devices, pending, audit, actions] = await Promise.all(jobs);

  state.devices = devices.devices || [];
  state.pending = pending.pending || [];
  if (actions) state.actions = actions;

  renderDevices();
  renderPending();
  renderAudit(audit.records || []);
  updateBadge();
  setOnline(true);
}

function renderDevices() {
  const box = $('#devices');
  if (!box) return;
  box.innerHTML = '';
  if (!state.devices.length) {
    box.append(el('div', 'pending-empty', 'Ни одно устройство не подключено.'));
    return;
  }
  for (const device of state.devices) {
    const card = el('button', 'device-card ripple-host');
    const icon = el('span', 'device-icon');
    icon.innerHTML = `<svg><use href="#i-${platformIcon(device.platform)}"></use></svg>`;
    const main = el('span', 'device-main');
    main.append(el('span', 'device-name', device.name || device.device_id));
    main.append(el('span', 'device-sub',
      `${device.platform || 'unknown'} · ${device.device_id} · команд: ${(device.available_actions || []).length}`));
    const pill = el('span', `status-pill${device.online ? '' : ' off'}`, device.online ? 'в сети' : 'офлайн');
    card.append(icon, main, pill);
    card.onclick = () => { haptic(); openDeviceSheet(device); };
    box.append(card);
  }
  renderSideDevices();
  reportUiMap();
}

function renderSideDevices() {
  const box = $('#side-devices');
  if (!box) return;
  box.innerHTML = '';
  if (!state.devices.length) {
    box.append(el('div', 'side-empty', 'нет устройств'));
    return;
  }
  for (const device of state.devices) {
    const row = el('div', 'side-device');
    row.append(el('span', `side-dot${device.online ? ' on' : ''}`));
    row.append(el('span', 'side-device-name', device.name || device.device_id));
    box.append(row);
  }
}

function platformIcon(platform) {
  const value = String(platform || '').toLowerCase();
  if (value.includes('android')) return 'phone';
  if (value.includes('win') || value === 'pc') return 'monitor';
  if (value.includes('router') || value.includes('openwrt')) return 'wifi';
  return 'devices';
}

function openDeviceSheet(device) {
  const body = document.createDocumentFragment();
  body.append(el('p', 'sheet-text',
    `${device.online ? 'В сети' : 'Офлайн'} · ${device.platform || 'unknown'} · ${device.device_id}`));

  const list = el('div', 'sheet-actions');
  const actions = device.available_actions || [];
  if (!actions.length) {
    body.append(el('p', 'sheet-text', 'Для этого устройства нет доступных команд.'));
  }
  for (const name of actions) {
    const entry = state.actions[name] || {};
    const button = el('button', 'sheet-action ripple-host');
    button.innerHTML = `<svg><use href="#i-${entry.dangerous ? 'alert' : 'chevron'}"></use></svg>`;
    const text = el('span');
    text.innerHTML = `<span>${esc(name)}</span>`;
    if (entry.dangerous) {
      const badge = el('span', 'code', ' · опасно');
      text.append(badge);
    }
    button.append(text);
    button.onclick = () => openActionSheet(device, name);
    list.append(button);
  }
  body.append(list);
  openSheet(device.name || device.device_id, body);
}

function openActionSheet(device, actionName) {
  const entry = state.actions[actionName] || {};
  const body = document.createDocumentFragment();
  if (entry.desc) body.append(el('p', 'sheet-text', entry.desc));

  const skeleton = {};
  for (const [name, rule] of Object.entries(entry.args || {})) {
    if (rule.required) skeleton[name] = rule.type === 'int' || rule.type === 'float' ? 0 : '';
  }
  const area = el('textarea');
  area.spellcheck = false;
  area.value = JSON.stringify(skeleton, null, 2);
  body.append(area);

  const run = el('button', 'btn filled ripple-host', 'Выполнить');
  const result = el('div', 'sheet-result');
  run.onclick = async () => {
    let args;
    try {
      args = JSON.parse(area.value || '{}');
    } catch (error) {
      result.innerHTML = '';
      result.append(el('p', 'sheet-text', `Аргументы не JSON: ${error.message}`));
      return;
    }
    await runDirect(device.device_id, actionName, args, result, run);
  };
  body.append(run, result);
  openSheet(`${device.name || device.device_id} · ${actionName}`, body);
}

async function runDirect(deviceId, actionName, args, resultBox, button) {
  button.disabled = true;
  resultBox.innerHTML = '';
  resultBox.append(el('p', 'sheet-text', 'выполняю…'));
  try {
    const response = await api('/cmd', {
      method: 'POST',
      body: JSON.stringify({ device: deviceId, action: actionName, args }),
    });
    resultBox.innerHTML = '';
    if (response.status === 'awaiting_confirmation') {
      const card = el('div', 'pending-card');
      card.append(el('div', 'pending-q', response.question || 'Требуется подтверждение'));
      const ok = el('button', 'btn filled', 'Подтвердить');
      ok.onclick = async () => {
        ok.disabled = true;
        try {
          await api(`/confirm/${encodeURIComponent(response.token)}?session=${encodeURIComponent(state.session)}`,
            { method: 'POST' });
          resultBox.innerHTML = '';
          resultBox.append(el('p', 'sheet-text', 'Выполнено.'));
          toast('Выполнено');
          closeSheet();
          refreshMeta();
          if (state.screen === 'chat') loadHistory();
        } catch (error) {
          toast(`Ошибка: ${error.message}`);
          ok.disabled = false;
        }
      };
      card.append(ok);
      resultBox.append(card);
      return;
    }
    const pre = el('pre');
    pre.textContent = JSON.stringify(response.result, null, 2);
    resultBox.append(pre);
    if (response.result && response.result.url) {
      const image = el('img');
      image.src = response.result.url;
      image.onclick = () => openViewer(image.src);
      resultBox.append(image);
    }
    toast('Готово');
    refreshMeta();
  } catch (error) {
    resultBox.innerHTML = '';
    resultBox.append(el('p', 'sheet-text', `Ошибка: ${error.message}`));
  } finally {
    button.disabled = false;
  }
}

/* ─────────────────────────── журнал ─────────────────────────── */

function renderPending() {
  const box = $('#pending');
  if (!box) return;
  box.innerHTML = '';
  if (!state.pending.length) {
    box.append(el('div', 'pending-empty', 'Нет действий, ожидающих подтверждения'));
    return;
  }
  for (const item of state.pending) {
    const card = el('div', 'pending-card');
    card.append(el('div', 'pending-q', item.question || 'Подтвердить действие?'));
    const row = el('div', 'confirm-actions');
    const ok = el('button', 'btn filled', 'Подтвердить');
    ok.onclick = () => confirmPending(item.token, ok);
    const cancel = el('button', 'btn text', 'Отмена');
    cancel.onclick = () => { closeSheet(); sendMessage('отмена'); };
    row.append(ok, cancel);
    card.append(row);
    box.append(card);
  }
}

function updateBadge() {
  const badge = $('#pending-badge');
  if (!badge) return;
  if (state.pending.length) {
    badge.textContent = String(state.pending.length);
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
}

function renderAudit(records) {
  const box = $('#audit');
  if (!box) return;
  box.innerHTML = '';
  if (!records.length) {
    box.append(el('div', 'audit-empty', 'Пока пусто'));
    return;
  }
  for (const record of records) {
    const row = el('div', 'audit-row');
    row.append(el('span', 'audit-time', (record.iso || '').slice(11, 19)));
    row.append(el('span', `audit-event ${record.ok === false ? 'fail' : 'info'}`,
      EVENT_LABELS[record.event] || record.event || ''));
    row.append(el('span', 'audit-detail',
      [record.device, record.action].filter(Boolean).join(' · ') || record.platform || ''));
    box.append(row);
  }
}

/* ─────────────────────────── чат ─────────────────────────── */

let historyStamp = '';

function stampOfHistory(messages) {
  const last = messages[messages.length - 1];
  return `${messages.length}:${last ? last.ts : 0}`;
}

async function loadHistory() {
  try {
    const data = await api(`/history?session=${encodeURIComponent(state.session)}&limit=200`);
    const messages = data.messages || [];
    historyStamp = stampOfHistory(messages);
    renderHistory(messages);
  } catch (error) {
    if (error.message !== 'unauthorized') renderHistory([]);
  }
}

// Подтягиваем чужие реплики: чат общий для телефона, бота и этого браузера.
async function refreshHistory() {
  if (document.hidden || !state.session || state.busy) return;
  if ($('#app').classList.contains('hidden')) return;
  try {
    const data = await api(`/history?session=${encodeURIComponent(state.session)}&limit=200`);
    const messages = data.messages || [];
    const stamp = stampOfHistory(messages);
    if (stamp === historyStamp) return;
    historyStamp = stamp;
    renderHistory(messages);
  } catch { /* сеть моргнула — попробуем на следующем тике */ }
}

function renderHistory(messages) {
  const box = $('#messages');
  const distance = box.scrollHeight - box.scrollTop - box.clientHeight;
  box.innerHTML = '';
  if (!messages.length) {
    renderEmptyState();
    return;
  }
  for (const message of messages) {
    appendMessage(message.role, message.text, message.executed, message.pending, message.ts);
  }
  if (distance > 120) {
    // Пользователь читает старые сообщения — не дёргаем экран.
    box.scrollTop = box.scrollHeight - box.clientHeight - distance;
  } else {
    scrollBottom(true);
  }
}

function renderEmptyState() {
  const box = $('#messages');
  if (box.querySelector('.empty')) return;
  const wrap = el('div', 'empty');
  const icon = el('div', 'empty-icon');
  icon.innerHTML = '<svg><use href="#i-logo"></use></svg>';
  wrap.append(icon, el('h3', null, 'Чем помочь?'));
  wrap.append(el('p', null, 'Напишите задачу словами — ассистент сам выберет устройство и команду.'));
  const chips = el('div', 'chips');
  for (const text of SUGGESTIONS) {
    const chip = el('button', 'chip ripple-host', text);
    chip.onclick = () => { haptic(); sendMessage(text); };
    chips.append(chip);
  }
  wrap.append(chips);
  box.append(wrap);
}

function hideEmpty() {
  const empty = $('#messages .empty');
  if (empty) empty.remove();
}

function appendMessage(role, text, executed, pending, ts) {
  const box = $('#messages');
  hideEmpty();
  const row = el('div', `msg-row ${role}`);

  if (role === 'system') {
    row.append(el('div', 'msg-system', text));
    box.append(row);
    return row;
  }

  const bubble = el('div', 'bubble');
  bubble.append(el('div', 'msg-text', text || ''));

  if (Array.isArray(executed) && executed.length) {
    bubble.append(actionsBlock(executed));
    const details = detailsBlock(executed);
    if (details) bubble.append(details);
    for (const item of executed) {
      const url = item && item.result && item.result.url;
      if (url) {
        const image = el('img', 'msg-shot');
        image.src = url;
        image.alt = 'результат команды';
        image.loading = 'lazy';
        image.onclick = () => openViewer(url);
        image.addEventListener('load', () => scrollBottom());
        bubble.append(image);
      }
    }
  }

  if (Array.isArray(pending) && pending.length) {
    bubble.append(pendingBlock(pending));
  }

  bubble.append(el('div', 'msg-meta', fmtTime(ts || Date.now() / 1000)));
  row.append(bubble);
  box.append(row);
  return row;
}

function actionsBlock(executed) {
  const wrap = el('div', 'msg-actions');
  for (const item of executed) {
    const ok = item.ok !== false;
    const chip = el('button', `action-chip ${ok ? 'ok' : 'fail'} ripple-host`);
    chip.innerHTML = `<svg><use href="#i-${ok ? 'check' : 'close'}"></use></svg>`;
    const label = [item.device, item.action].filter(Boolean).join(' · ') || 'действие';
    chip.append(el('span', null, label));
    chip.onclick = () => openExecutedSheet(item);
    wrap.append(chip);
  }
  return wrap;
}

function detailsBlock(executed) {
  const details = el('details', 'msg-details');
  details.append(el('summary', null, `выполнено команд: ${executed.length}`));
  for (const item of executed) {
    const pre = el('pre');
    const status = item.ok === false ? 'ошибка' : 'ok';
    const payload = item.result === undefined ? '' : JSON.stringify(item.result, null, 2);
    pre.textContent = `${item.device || ''} :: ${item.action || ''} → ${status}\n${payload.slice(0, 1500)}`;
    details.append(pre);
  }
  return details;
}

function pendingBlock(items) {
  const card = el('div', 'confirm-card');
  const head = el('div', 'confirm-head');
  head.innerHTML = '<svg><use href="#i-alert"></use></svg>';
  head.append(el('span', null, 'Требуется подтверждение'));
  card.append(head);
  for (const item of items) {
    card.append(el('div', 'confirm-q', item.question || `Подтвердить ${item.token}?`));
    const row = el('div', 'confirm-actions');
    const ok = el('button', 'btn filled', 'Подтвердить');
    ok.onclick = () => confirmPending(item.token, ok);
    const cancel = el('button', 'btn text', 'Отмена');
    cancel.onclick = () => sendMessage('отмена');
    row.append(ok, cancel);
    card.append(row);
  }
  return card;
}

function openExecutedSheet(item) {
  const body = document.createElement('div');
  const pre = el('pre');
  pre.textContent = JSON.stringify(item.result === undefined ? item : item.result, null, 2);
  body.append(pre);
  if (item.result && item.result.url) {
    const image = el('img');
    image.src = item.result.url;
    image.style.cssText = 'width:100%;border-radius:12px;margin-top:10px;background:#000';
    image.onclick = () => openViewer(image.src);
    body.append(image);
  }
  openSheet([item.device, item.action].filter(Boolean).join(' · ') || 'Результат', body);
}

function scrollBottom(force = false) {
  const box = $('#messages');
  if (!box) return;
  if (!force) {
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 420;
    if (!nearBottom) return;
  }
  requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });
}

function appendTyping() {
  hideEmpty();
  const row = el('div', 'msg-row assistant');
  const bubble = el('div', 'bubble');
  const dots = el('div', 'typing');
  dots.append(el('span'), el('span'), el('span'));
  bubble.append(dots);
  row.append(bubble);
  $('#messages').append(row);
  scrollBottom(true);
  return row;
}

async function sendMessage(rawText) {
  const text = String(rawText || '').trim();
  if (!text || state.busy) return;
  state.busy = true;
  updateSend();
  haptic('medium');

  appendMessage('user', text, null, null, Date.now() / 1000);
  scrollBottom(true);
  const typing = appendTyping();

  try {
    const result = await api(`/chat?source=${chatSource()}`, {
      method: 'POST',
      body: JSON.stringify({ message: text, session: state.session }),
    });
    typing.remove();
    appendMessage('assistant', result.reply || '(пустой ответ)',
      result.executed, result.pending, Date.now() / 1000);
    scrollBottom();
    if (result.pending && result.pending.length) refreshMeta();
    if (state.voice && result.reply) speak(result.reply);
  } catch (error) {
    typing.remove();
    if (error.message !== 'unauthorized') {
      appendMessage('system', `Ошибка: ${error.message}`);
      scrollBottom(true);
    }
  } finally {
    state.busy = false;
    updateSend();
    refreshMeta().catch(() => {});
  }
}

async function confirmPending(token, button) {
  if (button) button.disabled = true;
  try {
    await api(`/confirm/${encodeURIComponent(token)}?session=${encodeURIComponent(state.session)}`,
      { method: 'POST' });
    toast('Выполнено');
    if (state.screen === 'chat') await loadHistory();
    await refreshMeta();
  } catch (error) {
    if (error.message !== 'unauthorized') toast(`Ошибка: ${error.message}`);
    if (button) button.disabled = false;
  }
}

function newChat() {
  openDialog('Начать новый диалог?', 'История текущего диалога будет удалена на сервере.',
    'Очистить', async () => {
      try {
        await api(`/session/${encodeURIComponent(state.session)}`, { method: 'DELETE' });
      } catch { /* не критично */ }
      renderHistory([]);
      refreshMeta().catch(() => {});
      toast('История очищена');
      haptic();
    });
}

let speechAudio = null;

function speakFallback(text) {
  if (!window.speechSynthesis) return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'ru-RU';
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
}

async function speak(text) {
  if (!text) return;
  try {
    if (speechAudio) {
      speechAudio.pause();
      speechAudio = null;
    }
    const blob = await apiBlob('/tts', { method: 'POST', body: JSON.stringify({ text }) });
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    speechAudio = audio;
    audio.addEventListener('ended', () => URL.revokeObjectURL(url));
    audio.addEventListener('error', () => URL.revokeObjectURL(url));
    await audio.play();
  } catch (error) {
    // серверный синтез недоступен — читаем голосом устройства
    speakFallback(text);
  }
}

/* ─────────────────────────── голос ─────────────────────────── */

async function toggleRecording() {
  if (state.recording && state.recorder) {
    state.recorder.stop();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.chunks = [];
    state.recorder = new MediaRecorder(stream);
    state.recorder.ondataavailable = (event) => state.chunks.push(event.data);
    state.recorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      state.recording = false;
      $('#mic').classList.remove('recording');
      const blob = new Blob(state.chunks, { type: state.recorder.mimeType || 'audio/webm' });
      await sendVoice(blob);
    };
    state.recorder.start();
    state.recording = true;
    $('#mic').classList.add('recording');
    haptic();
  } catch (error) {
    appendMessage('system', `Микрофон недоступен: ${error.message}`);
    scrollBottom(true);
  }
}

async function sendVoice(blob) {
  const typing = appendTyping();
  try {
    const form = new FormData();
    form.append('file', blob, 'voice.webm');
    const result = await api(`/voice?chat=true&source=${chatSource()}&session=${encodeURIComponent(state.session)}`,
      { method: 'POST', body: form });
    typing.remove();
    if (!result.text) {
      appendMessage('system', 'Речь не распознана');
      scrollBottom(true);
      return;
    }
    await loadHistory();
    if (state.voice && result.reply) speak(result.reply);
  } catch (error) {
    typing.remove();
    if (error.message !== 'unauthorized') {
      appendMessage('system', `Голос: ${error.message}`);
      scrollBottom(true);
    }
  }
}

/* ─────────────────────────── модели ─────────────────────────── */

async function loadModels() {
  try {
    state.models = await api('/models');
    updateModelLabel();
  } catch (error) {
    $('#model-current').textContent = 'список недоступен';
  }
}

function updateModelLabel() {
  const label = $('#model-current');
  if (!label) return;
  if (!state.models || !state.models.current) {
    label.textContent = 'не выбрана';
    return;
  }
  label.textContent = shortModel(state.models.current)
    + (state.models.custom ? ' · выбрана вручную' : ' · по умолчанию');
  const sideModel = $('#side-model');
  if (sideModel) sideModel.textContent = `ИИ: ${shortModel(state.models.current)}`;
  setOnline(!$('#status').classList.contains('off'));
}

function openModelsSheet() {
  const body = document.createElement('div');
  body.append(el('p', 'sheet-text', 'Модель планировщика. Переключение действует сразу, без перезапуска ядра.'));

  const list = el('div', 'sheet-actions');
  const data = state.models || { models: [], current: '' };
  const models = (data.models && data.models.length) ? data.models : (data.current ? [data.current] : []);
  if (!models.length) body.append(el('p', 'sheet-text', 'Список моделей пуст.'));

  for (const model of models) {
    const option = el('button', `model-option ripple-host${model === data.current ? ' current' : ''}`);
    option.append(el('span', 'radio'));
    option.append(el('span', 'name', model));
    option.onclick = async () => {
      option.disabled = true;
      try {
        state.models = await api('/models', {
          method: 'POST',
          body: JSON.stringify({ model }),
        });
        updateModelLabel();
        closeSheet();
        toast(`Модель: ${shortModel(model)}`);
        haptic();
      } catch (error) {
        toast(`Не удалось: ${error.message}`);
        option.disabled = false;
      }
    };
    list.append(option);
  }
  body.append(list);
  openSheet('Модель ИИ', body);
}

/* ─────────────────────────── слои поверх ─────────────────────────── */

function openSheet(title, content) {
  $('#sheet-title').textContent = title;
  const body = $('#sheet-body');
  body.innerHTML = '';
  body.append(content);
  body.scrollTop = 0;
  $('#sheet').classList.remove('hidden');
  $('#scrim').classList.remove('hidden');
}

function closeSheet() {
  $('#sheet').classList.add('hidden');
  $('#scrim').classList.add('hidden');
}

let dialogAction = null;

function openDialog(title, text, okLabel, onOk) {
  $('#dialog-title').textContent = title;
  $('#dialog-text').textContent = text;
  $('#dialog-ok').textContent = okLabel || 'ОК';
  dialogAction = onOk || null;
  $('#dialog').classList.remove('hidden');
}

function closeDialog() {
  $('#dialog').classList.add('hidden');
  dialogAction = null;
}

function openViewer(url) {
  $('#viewer-img').src = url;
  $('#viewer').classList.remove('hidden');
}

function syncVoiceToggle() {
  const checkbox = $('#voice-reply');
  if (checkbox) checkbox.checked = state.voice;
}

function updateSend() {
  const input = $('#message');
  const button = $('#send');
  if (!input || !button) return;
  button.disabled = state.busy || !input.value.trim();
}

/* ─────────────────────────── обработчики ─────────────────────────── */

$('#save-token').onclick = async () => {
  const token = $('#token').value.trim();
  if (!token) return;
  state.token = token;
  try {
    const devices = await api('/devices');
    localStorage.setItem('na_token', token);
    state.devices = devices.devices || [];
    await pickSession();
    await refreshMeta(true);
    await Promise.all([loadModels(), loadHistory()]);
    showApp();
    updateAppbar();
    syncVoiceToggle();
    startSync();
  } catch (error) {
    if (error.message !== 'unauthorized') $('#login-error').textContent = error.message;
  }
};

$('#token').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') $('#save-token').click();
});

$('#change-token').onclick = () => showLogin();

$$('.nav-item').forEach((item) => {
  item.onclick = () => {
    haptic();
    showScreen(item.dataset.screen);
  };
});

window.addEventListener('hashchange', () => {
  const name = initialScreen();
  if (name !== state.screen) showScreen(name, { silent: true });
});

window.addEventListener('resize', reportUiMap);

$('#send').onclick = () => {
  const input = $('#message');
  const text = input.value;
  input.value = '';
  autoGrow();
  sendMessage(text);
};

$('#message').addEventListener('input', autoGrow);
$('#message').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    $('#send').click();
  }
});

$('#mic').onclick = toggleRecording;

$('#sheet-close').onclick = closeSheet;
$('#scrim').onclick = closeSheet;

$('#dialog-ok').onclick = async () => {
  const action = dialogAction;
  closeDialog();
  if (action) await action();
};
$('#dialog-cancel').onclick = closeDialog;

$('#viewer').onclick = () => {
  $('#viewer').classList.add('hidden');
  $('#viewer-img').src = '';
};
$('#viewer-close').onclick = (event) => {
  event.stopPropagation();
  $('#viewer').classList.add('hidden');
  $('#viewer-img').src = '';
};

$('#open-models').onclick = () => { haptic(); openModelsSheet(); };

$('#clear-chat').onclick = () => {
  openDialog('Очистить историю?', 'Переписка будет удалена на сервере без возможности восстановления.',
    'Очистить', async () => {
      try {
        await api(`/session/${encodeURIComponent(state.session)}`, { method: 'DELETE' });
        renderHistory([]);
        refreshMeta().catch(() => {});
        toast('История очищена');
      } catch (error) {
        toast(`Ошибка: ${error.message}`);
      }
    });
};

$('#voice-reply').addEventListener('change', (event) => {
  state.voice = event.target.checked;
  localStorage.setItem('na_voice', state.voice ? '1' : '0');
  toast(state.voice ? 'Ответы будут озвучиваться' : 'Озвучивание выключено');
});

/* рябь на кнопках */
document.addEventListener('pointerdown', (event) => {
  const host = event.target.closest('.ripple-host');
  if (!host) return;
  const rect = host.getBoundingClientRect();
  const size = Math.max(rect.width, rect.height) * 2;
  const ripple = document.createElement('span');
  ripple.className = 'ripple';
  ripple.style.width = `${size}px`;
  ripple.style.height = `${size}px`;
  ripple.style.left = `${event.clientX - rect.left - size / 2}px`;
  ripple.style.top = `${event.clientY - rect.top - size / 2}px`;
  host.append(ripple);
  setTimeout(() => ripple.remove(), 520);
}, { passive: true });

function autoGrow() {
  const input = $('#message');
  if (!input) return;
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 128)}px`;
  updateSend();
}

updateSend();
boot();
