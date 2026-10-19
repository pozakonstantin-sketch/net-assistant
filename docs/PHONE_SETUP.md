# Телефон: пульт управления и агент (Android + Termux)

Телефон (у нас Honor FLC-AN00, Android 16, без root) играет две роли:

1. **Пульт** — дашборд ядра в браузере/на главном экране: переписка, кнопки,
   подтверждения.
2. **Устройство** — в Termux живёт `android_agent`, который сам подключается к
   ядру по WebSocket и выполняет команды (батарея, Wi-Fi, SMS, уведомления,
   фонарик, TTS, приложения, файлы, оболочка).

Наружу порты не открываются: телефон и ПК — клиенты Tailscale, ядро живёт в ВМ.

```
Honor (браузер/PWA) ─┐
                     ├─ Tailscale ──> ВМ assistant (ядро + OpenCode)
Honor (Termux-агент) ┘                       100.67.45.90:8787
```

---

## 0. Что понадобится

| Что | Откуда | Зачем |
|---|---|---|
| Tailscale | Play Store или [F-Droid](https://f-droid.org/packages/com.tailscale.ipn/) | доступ к ядру из любой сети |
| Termux | **только F-Droid** (версия в Play Store устарела) | среда для агента |
| Termux:API | F-Droid | мост к функциям Android (`termux-battery-status` и т.п.) |
| Termux:Boot | F-Droid | автозапуск агента после перезагрузки |

> Termux и Termux:API должны быть из одного источника (F-Droid): пакеты Play
> Store и F-Droid несовместимы — `termux-*` команды просто не найдутся.

---

## 1. Tailscale на телефоне

1. Установите Tailscale, войдите **в тот же аккаунт**, что и на ПК/в ВМ.
2. Проверьте, что виден узел `astra-hub` с адресом `100.67.45.90`.
3. Откройте `https://astra-hub.taile4385b.ts.net` — экран ввода токена.

Если узла нет:

```bash
# в ВМ
tailscale status
sudo systemctl restart tailscaled
```

---

## 2. Установка агента в Termux (одна команда)

Откройте Termux (из F-Droid) и выполните **одну** команду, подставив токен:

```bash
curl -fsSL "http://100.67.45.90:8787/android/install.sh?token=ВАШ_ТОКЕН" | bash
```

Важно: запрос должен идти **на tailnet-адрес ВМ** — ядро подставляет его в
сгенерированный скрипт, чтобы агент потом подключался именно туда. С `127.0.0.1`
в скрипт попадёт `127.0.0.1`, и агент не найдёт ядро.

Что делает скрипт:

1. `pkg install python termux-api curl tar` (если чего-то нет);
2. скачивает `android/bundle.tar.gz` (агент + минимальные модули ядра) в
   `~/net-assistant`;
3. пишет `~/net-assistant/.env`:
   ```ini
   CORE_URL=ws://100.67.45.90:8787
   ASSISTANT_TOKEN=...
   ANDROID_DEVICE_ID=phone
   ANDROID_DEVICE_NAME=Honor
   ```
4. ставит `websockets` и `python-dotenv`;
5. кладёт автозапуск в `~/.termux/boot/start-agent.sh` (**с логированием** в
   `~/agent.log`);
6. берёт `termux-wake-lock` и запускает агента.

Проверка:

```bash
tail -f ~/agent.log
pgrep -f agents.android_agent
```

В дашборде появится устройство **phone (Honor)**.

Повторный запуск той же команды = обновление: код и `.env` перезапишутся,
процесс перезапустится.

---

## 3. Разрешения Android (важный шаг)

Без этого часть команд будет молча падать или висеть.

1. **Termux:API.** Разрешения можно выдать через adb (с ПК, кабель + отладка по
   USB) — так быстрее, чем ловить системные диалоги:

   ```bash
   for p in ACCESS_FINE_LOCATION ACCESS_COARSE_LOCATION READ_SMS SEND_SMS \
            RECEIVE_SMS CAMERA POST_NOTIFICATIONS READ_PHONE_STATE READ_CONTACTS \
            RECORD_AUDIO READ_CALENDAR WRITE_CALENDAR; do
     adb shell pm grant com.termux.api android.permission.$p
   done
   adb shell pm grant com.termux android.permission.POST_NOTIFICATIONS
   ```

2. **Отключить оптимизацию батареи** для Termux (иначе система усыпит агент):

   ```bash
   adb shell cmd deviceidle whitelist +com.termux
   adb shell cmd deviceidle whitelist +com.termux.api
   adb shell cmd deviceidle whitelist +com.termux.boot
   ```

   То же руками: Настройки → Приложения → Termux → Батарея → **Без ограничений**.

3. **Termux:Boot** — открыть приложение один раз после установки, чтобы система
   зарегистрировала автозапуск.

4. **Termux:API** — открыть один раз: сервис не поднимается из фона, пока
   приложение ни разу не запускали (иначе `termux-*` будут висеть).

> **После каждой перезагрузки телефон нужно один раз разблокировать.** До
> первого разблокирования данные приложений зашифрованы (Direct Boot): Termux
> физически не может запуститься, и агент временно офлайн. Termux:Boot стартует
> сразу после разблокировки — нажатий больше не требуется.

---

## 4. Что умеет телефонный агент

| Действие | Что делает |
|---|---|
| `sys.info` | модель, бренд, версия Android, ABI |
| `battery.status` | заряд, температура, режим зарядки |
| `net.status` | интерфейсы, адреса, наличие интернета, сведения о Wi-Fi |
| `wifi.info` | сеть, IP, скорость, уровень сигнала |
| `apps.list` | список приложений (`third_party=true` — только пользовательские) |
| `app.info`, `app.current` | сведения о приложении / что на переднем плане |
| `app.open` | запуск приложения (пакет, например `org.telegram.messenger`) |
| `app.stop` ⚠ | принудительная остановка приложения |
| `notify.send` | показать уведомление |
| `tts.speak` | озвучить текст |
| `sms.list` | последние SMS |
| `sms.send` ⚠ | отправить SMS |
| `torch.set` | фонарик |
| `location.get` | последняя известная точка (см. ограничения) |
| `fs.list`, `fs.read` | файлы в домашнем каталоге и общих хранилищах |
| `shell.run` ⚠ | команды из белого списка (в `full` — любые) |
| `exec.run`, `fs.write`, `fs.delete`, `proc.kill` ⚠ | полный доступ при `ACCESS_MODE=full` |

⚠ — требуют подтверждения токеном (`REQUIRE_CONFIRM=true`).

### Ограничения (важно понимать)

* **Скриншот, разбор интерфейса и нажатия недоступны.** Android запрещает
  обычным приложениям захват экрана (`READ_FRAME_BUFFER`) и ввод событий
  (`INJECT_EVENTS`): `screencap` отвечает «Capturing failed», `input tap` —
  `SecurityException`. Это ограничение системы, а не настройка Termux. Работает
  только через root или adb (отладка по USB/сети) — тогда команды идут с ПК.
  Поэтому в каталоге у телефона нет `screen.*` и `ui.dump`.
* **Геолокация** отдаёт только последнюю известную точку и часто недоступна
  (`Failed to get location`): у Honor нет свежего фикса, а запрос «свежей» точки
  в Termux:API умеет ждать минуты, занимая очередь сервиса. Агент использует
  мгновенный режим и, если точки нет, честно сообщает об этом.
* **SSID Wi-Fi** может отображаться как `<unknown ssid>` — так Android скрывает
  его от приложений без «полного» доступа к геолокации.
* **Ввод кириллицы** через `input text` невозможен; но экранные команды всё
  равно недоступны (см. выше).

---

## 5. Ручной запуск и диагностика

Штатный запуск — через Termux:Boot (`~/.termux/boot/start-agent.sh`). Если
запускать агента вручную **по adb** (`run-as com.termux ...`), то у процесса не
будет окружения Termux, и `termux-*` не найдутся: `FileNotFoundError:
termux-battery-status`. Поэтому в проекте есть `~/start-agent.sh` — он выставляет
`PATH`, `PREFIX`, `TMPDIR`, `LD_PRELOAD` и запускает агента:

```bash
sh ~/start-agent.sh
```

| Симптом | Что проверить |
|---|---|
| Устройства `phone` нет в дашборде | `pgrep -f agents.android_agent`, `tail -30 ~/agent.log`, `tailscale status` на телефоне |
| Агент мигает в дашборде (online/offline) | запущено **два** агента с одним `ANDROID_DEVICE_ID` — они выбивают друг друга: `pkill -f agents.android_agent`, запустить один |
| `termux-*: command not found` | запуск без окружения Termux (см. выше) или Termux:API не из F-Droid |
| `termux-location`/уведомления виснут без ответа | сервис Termux:API не поднят или занят: откройте приложение Termux:API, при необходимости `am force-stop com.termux.api` |
| `sms.*` не работает | разрешение SMS не выдано Termux:API |
| `app.open` не запускает | приложение не установлено (`app.info` проверит пакет) |
| Агент умирает через минуты | отключите оптимизацию батареи для Termux и Termux:API |
| После перезагрузки агент офлайн | телефон не разблокирован: до первого разблокирования данные Termux зашифрованы (Direct Boot). Разблокируйте — агент поднимется сам |

---

## 6. По желанию: HTTPS через `tailscale serve`

Даёт адрес `https://astra-hub.<tailnet>.ts.net`, настоящую установку PWA и
шифрование. В ВМ (уже сделано):

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:8787
tailscale serve status
```

Установщик агента тогда берут по HTTPS-адресу — ядро само подставит `wss://`.

---

## 7. Безопасность

- Токен из ссылки установщика попадает в историю Termux — это общий секрет;
  после установки при желании очистите историю: `history -c`.
- Ядро слушает `0.0.0.0:8787`, но доступно только из локалки и tailnet; наружу
  порт не публикуется.
- `ACCESS_MODE=full` на телефоне разрешает произвольные команды Termux и запись
  файлов; опасные действия (SMS, `app.stop`, `shell.run`…) требуют подтверждения
  токеном (`REQUIRE_CONFIRM=true`).
