/* Net Assistant — дашборд без сборки: ванильный JS + fetch. */

const $ = (sel) => document.querySelector(sel);
const state = {
  token: localStorage.getItem('na_token') || '',
  devices: [],
  actions: {},
  recorder: null,
  chunks: [],
  busy: false,
};

/* ─────────────────────────── API ─────────────────────────── */

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), Authorization: `Bearer ${state.token}` };
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    showLogin('Токен неверный или истёк');
    throw new Error('unauthorized');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

/* ─────────────────────────── вход ─────────────────────────── */

function showLogin(message = '') {
  $('#login').classList.remove('hidden');
  $('#app').classList.add('hidden');
  $('#login-error').textContent = message;
  $('#token').focus();
}

async function boot() {
  if (!state.token) await telegramLogin();
  if (!state.token) return showLogin();
  try {
    await refreshAll();
    state.planner = await fetchPlanner();
    await loadModels();
    $('#login').classList.add('hidden');
    $('#app').classList.remove('hidden');
    setStatus(onlineStatus(), true);
    setInterval(() => { if (!$('#app').classList.contains('hidden')) refreshMeta(); }, 5000);
  } catch (error) {
    if (error.message !== 'unauthorized') showLogin(error.message);
  }
}

/* ─────────────── вход через Telegram Mini App ─────────────── */

const tg = window.Telegram && window.Telegram.WebApp;

async function telegramLogin() {
  if (!tg || !tg.initData) return;          // обычный браузер — форма токена
  try { tg.ready(); tg.expand(); } catch {}
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
  } catch { /* не вышло — покажем форму */ }
}

function setStatus(text, ok) {
  const el = $('#status');
  el.textContent = text;
  el.classList.toggle('ok', Boolean(ok));
}

function onlineStatus() {
  const count = state.devices.filter(d => d.online).length;
  return `онлайн · устройств: ${count}${state.planner ? ' · ' + state.planner : ''}`;
}

async function fetchPlanner() {
  try {
    const health = await api('/health');
    if (!health.llm_configured) return 'ИИ не настроен';
    if (health.llm_backend === 'opencode') {
      const short = String(health.llm_model || '').split('/').pop();
      return `ИИ: OpenCode · ${short}`;
    }
    return `ИИ: ${health.llm_model || 'облако'}`;
  } catch {
    return '';
  }
}

/* ─────────────────────────── модель ИИ ─────────────────────────── */

async function loadModels() {
  const select = $('#model-select');
  const hint = $('#model-hint');
  if (!select) return;
  try {
    const data = await api('/models');
    state.models = data;
    select.innerHTML = '';
    for (const model of data.models || []) {
      const option = document.createElement('option');
      option.value = model;
      option.textContent = model.split('/').pop() + (model === data.current ? ' — текущая' : '');
      select.appendChild(option);
    }
    if (!(data.models || []).length) {
      const option = document.createElement('option');
      option.value = data.current;
      option.textContent = data.current;
      select.appendChild(option);
    }
    select.value = data.current;
    hint.textContent = data.custom
      ? `выбрана вручную · по умолчанию: ${data.default}`
      : `по умолчанию из .env: ${data.default}`;
  } catch (error) {
    hint.textContent = `список моделей недоступен: ${error.message}`;
  }
}

async function applyModel() {
  const button = $('#model-apply');
  const model = $('#model-select').value;
  if (!model) return;
  button.disabled = true;
  try {
    const data = await api('/models', { method: 'POST', body: JSON.stringify({ model }) });
    state.models = data;
    await loadModels();
    state.planner = await fetchPlanner();
    setStatus(onlineStatus(), true);
    $('#model-hint').textContent = `переключено на ${data.current}`;
  } catch (error) {
    $('#model-hint').textContent = `не удалось переключить: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

/* ─────────────────────────── данные ─────────────────────────── */

async function refreshAll() {
  const [devices, actions] = await Promise.all([api('/devices'), api('/actions')]);
  state.devices = devices.devices;
  state.actions = actions;
  renderDevices();
  fillSelectors();
}

async function refreshMeta() {
  try {
    const [devices, pending, audit] = await Promise.all([
      api('/devices'), api('/pending'), api('/audit?limit=25'),
    ]);
    state.devices = devices.devices;
    renderDevices();
    fillSelectors();
    renderPending(pending.pending);
    renderAudit(audit.records);
    setStatus(onlineStatus(), true);
  } catch (error) {
    if (error.message !== 'unauthorized') setStatus('нет связи с ядром', false);
  }
}

function renderDevices() {
  const box = $('#devices');
  box.innerHTML = '';
  $('#device-count').textContent = String(state.devices.length);
  if (!state.devices.length) {
    box.innerHTML = '<p class="muted">Ни одно устройство не подключено.</p>';
    return;
  }
  for (const device of state.devices) {
    const card = document.createElement('div');
    card.className = `device ${device.online ? 'online' : 'offline'}`;
    card.innerHTML = `
      <div class="name"><span>${escapeHtml(device.name)}</span><span class="dot"></span></div>
      <div class="meta">${escapeHtml(device.device_id)} · ${escapeHtml(device.platform)} ·
        команд: ${device.actions.length}</div>`;
    card.onclick = () => {
      $('#cmd-device').value = device.device_id;
      fillActions();
    };
    box.appendChild(card);
  }
}

function fillSelectors() {
  const deviceSelect = $('#cmd-device');
  const previous = deviceSelect.value;
  deviceSelect.innerHTML = '';
  for (const device of state.devices) {
    const option = document.createElement('option');
    option.value = device.device_id;
    option.textContent = `${device.name}${device.online ? '' : ' (офлайн)'}`;
    deviceSelect.appendChild(option);
  }
  if (previous) deviceSelect.value = previous;
  fillActions();
}

function fillActions() {
  const deviceId = $('#cmd-device').value;
  const device = state.devices.find(d => d.device_id === deviceId);
  const select = $('#cmd-action');
  const previous = select.value;
  select.innerHTML = '';
  if (!device) return;
  for (const name of device.available_actions || []) {
    const option = document.createElement('option');
    option.value = name;
    const entry = state.actions[name] || {};
    option.textContent = entry.dangerous ? `${name} ⚠` : name;
    select.appendChild(option);
  }
  // автообновление не должно сбрасывать выбор пользователя
  const stillThere = Array.from(select.options).some(o => o.value === previous);
  if (stillThere) {
    select.value = previous;
  } else {
    updateArgsTemplate();
  }
}

function updateArgsTemplate() {
  const entry = state.actions[$('#cmd-action').value] || {};
  const skeleton = {};
  for (const [name, rule] of Object.entries(entry.args || {})) {
    if (rule.required) skeleton[name] = rule.type === 'int' || rule.type === 'float' ? 0 : '';
  }
  $('#cmd-args').value = JSON.stringify(skeleton, null, 2);
}

/* ─────────────────────────── подтверждения ─────────────────────────── */

function renderPending(items) {
  const box = $('#pending');
  box.innerHTML = '';
  if (!items || !items.length) {
    box.innerHTML = '<p class="muted">нет ожидающих действий</p>';
    return;
  }
  for (const item of items) {
    const card = document.createElement('div');
    card.className = 'item';
    card.innerHTML = `<div class="q">${escapeHtml(item.question)}</div>`;
    const actions = document.createElement('div');
    actions.className = 'actions';

    const confirmButton = document.createElement('button');
    confirmButton.className = 'ok small';
    confirmButton.textContent = 'Подтвердить';
    confirmButton.onclick = () => confirmAction(item.token);

    const cancelButton = document.createElement('button');
    cancelButton.className = 'ghost small';
    cancelButton.textContent = 'Отмена';
    cancelButton.onclick = () => sendMessage('отмена');

    actions.append(confirmButton, cancelButton);
    card.appendChild(actions);
    box.appendChild(card);
  }
}

async function confirmAction(token) {
  try {
    const result = await api(`/confirm/${token}`, { method: 'POST' });
    addMessage('bot', `Готово: ${result.action} → ${JSON.stringify(result.result)}`);
  } catch (error) {
    addMessage('system', `ошибка подтверждения: ${error.message}`);
  }
  refreshMeta();
}

/* ─────────────────────────── журнал ─────────────────────────── */

function renderAudit(records) {
  const box = $('#audit');
  box.innerHTML = '';
  if (!records || !records.length) {
    box.innerHTML = '<p class="muted">пусто</p>';
    return;
  }
  for (const record of records) {
    const row = document.createElement('div');
    row.className = 'audit-row';
    const detail = record.action || record.platform || '';
    const extra = record.ok === false ? ' ✕' : '';
    row.innerHTML = `<span class="t">${escapeHtml((record.iso || '').slice(11))}</span>
      <span class="e ${escapeHtml(record.event)}">${escapeHtml(record.event)}</span>
      <span class="muted">${escapeHtml(record.device || '')} ${escapeHtml(detail)}${extra}</span>`;
    box.appendChild(row);
  }
}

/* ─────────────────────────── чат ─────────────────────────── */

function addMessage(role, text, executed) {
  const box = $('#messages');
  const message = document.createElement('div');
  message.className = `msg ${role}`;
  message.textContent = text;

  if (executed && executed.length) {
    for (const item of executed) {
      if (item.result && item.result.url) {
        const image = document.createElement('img');
        image.src = item.result.url;
        image.alt = 'результат команды';
        message.appendChild(image);
      }
    }
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = `выполнено команд: ${executed.length}`;
    details.appendChild(summary);
    for (const item of executed) {
      const pre = document.createElement('pre');
      const status = item.ok === false ? 'ошибка' : 'ok';
      const payload = item.result ? JSON.stringify(item.result, null, 2).slice(0, 1200) : '';
      pre.textContent = `${item.device} :: ${item.action} → ${status}\n${payload}`;
      details.appendChild(pre);
    }
    message.appendChild(details);
  }

  box.appendChild(message);
  box.scrollTop = box.scrollHeight;
  return message;
}

async function sendMessage(text) {
  if (!text || state.busy) return;
  state.busy = true;
  $('#send').disabled = true;
  addMessage('user', text);
  const thinking = addMessage('bot', '…');
  try {
    const result = await api('/chat', { method: 'POST', body: JSON.stringify({ message: text }) });
    thinking.remove();
    addMessage('bot', result.reply || '(пустой ответ)', result.executed);
    if (result.pending && result.pending.length) {
      addMessage('system', `нужно подтверждение: ${result.pending.map(p => p.token).join(', ')}`);
    }
    if ($('#voice-reply').checked) speak(result.reply);
    refreshMeta();
  } catch (error) {
    thinking.remove();
    addMessage('system', `ошибка: ${error.message}`);
  } finally {
    state.busy = false;
    $('#send').disabled = false;
  }
}

function speak(text) {
  if (!text || !window.speechSynthesis) return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'ru-RU';
  speechSynthesis.cancel();
  speechSynthesis.speak(utterance);
}

/* ─────────────────────────── голос ─────────────────────────── */

async function toggleRecording() {
  const button = $('#mic');
  if (state.recorder && state.recorder.state === 'recording') {
    state.recorder.stop();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.chunks = [];
    state.recorder = new MediaRecorder(stream);
    state.recorder.ondataavailable = (event) => state.chunks.push(event.data);
    state.recorder.onstop = async () => {
      stream.getTracks().forEach(track => track.stop());
      button.classList.remove('recording');
      const blob = new Blob(state.chunks, { type: state.recorder.mimeType || 'audio/webm' });
      await sendVoice(blob);
    };
    state.recorder.start();
    button.classList.add('recording');
  } catch (error) {
    addMessage('system', `микрофон недоступен: ${error.message}`);
  }
}

async function sendVoice(blob) {
  const form = new FormData();
  form.append('file', blob, 'voice.webm');
  const thinking = addMessage('system', 'распознаю…');
  try {
    const result = await api('/voice?chat=true', { method: 'POST', body: form });
    thinking.remove();
    if (!result.text) return addMessage('system', 'речь не распознана');
    addMessage('user', result.text);
    if (result.reply) {
      addMessage('bot', result.reply, result.executed);
      if ($('#voice-reply').checked) speak(result.reply);
    }
  } catch (error) {
    thinking.remove();
    addMessage('system', `голос: ${error.message}`);
  }
}

/* ─────────────────────────── прямые команды ─────────────────────────── */

async function runCommand() {
  const device = $('#cmd-device').value;
  const action = $('#cmd-action').value;
  if (!device || !action) return;
  let args = {};
  try {
    args = JSON.parse($('#cmd-args').value || '{}');
  } catch (error) {
    $('#cmd-result').textContent = `аргументы не JSON: ${error.message}`;
    return;
  }
  const box = $('#cmd-result');
  box.textContent = 'выполняю…';
  try {
    const result = await api('/cmd', {
      method: 'POST',
      body: JSON.stringify({ device, action, args }),
    });
    box.textContent = '';
    if (result.status === 'awaiting_confirmation') {
      box.textContent = `нужно подтверждение (${result.token}): ${result.question}`;
      refreshMeta();
      return;
    }
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify(result.result, null, 2);
    box.appendChild(pre);
    if (result.result && result.result.url) {
      const image = document.createElement('img');
      image.src = result.result.url;
      box.appendChild(image);
    }
  } catch (error) {
    box.textContent = `ошибка: ${error.message}`;
  }
  refreshMeta();
}

/* ─────────────────────────── утилиты ─────────────────────────── */

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));
}

/* ─────────────────────────── обработчики ─────────────────────────── */

$('#save-token').onclick = async () => {
  const token = $('#token').value.trim();
  if (!token) return;
  state.token = token;
  try {
    await refreshAll();
    state.planner = await fetchPlanner();
    localStorage.setItem('na_token', token);
    $('#login').classList.add('hidden');
    $('#app').classList.remove('hidden');
    setStatus(onlineStatus(), true);
  } catch (error) {
    $('#login-error').textContent = error.message;
  }
};

$('#change-token').onclick = () => showLogin();
$('#token').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') $('#save-token').click();
});
$('#send').onclick = () => {
  const text = $('#message').value.trim();
  $('#message').value = '';
  sendMessage(text);
};
$('#message').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    $('#send').click();
  }
});
$('#mic').onclick = toggleRecording;
$('#cmd-device').onchange = fillActions;
$('#cmd-action').onchange = updateArgsTemplate;
$('#cmd-run').onclick = runCommand;
$('#model-apply').onclick = applyModel;
$('#model-select').onchange = () => { $('#model-hint').textContent = 'нажмите «Переключить», чтобы применить'; };
$('#new-chat').onclick = async () => {
  try { await api('/session/default', { method: 'DELETE' }); } catch (_) { /* не критично */ }
  $('#messages').innerHTML = '';
  addMessage('system', 'контекст очищен');
};

boot();
