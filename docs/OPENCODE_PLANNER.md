# Планировщик на бесплатных моделях OpenCode

Ядро умеет два планировщика:

| Режим | Когда включается | Что нужно |
|---|---|---|
| `opencode` | `LLM_BACKEND=opencode`, либо `auto` и пустой `LLM_API_KEY`, либо просто нет ключа | CLI `opencode` на той же машине |
| `openai` | задан `LLM_API_KEY` (при `auto`) или `LLM_BACKEND=openai` | любой OpenAI-совместимый эндпоинт |

По умолчанию (`auto`) при пустом `LLM_API_KEY` ядро использует **бесплатные
модели OpenCode** — ничего платить и подписываться не нужно.

## Почему через CLI, а не напрямую по HTTP

У OpenCode есть OpenAI-совместимый шлюз
(`https://opencode.ai/inference/openai/v1/chat/completions`), но бесплатные
модели он отдаёт только «изнутри OpenCode»:

```json
{"type":"error","error":{"type":"FreeTierError",
 "message":"OpenCode's free tier can only be used from within OpenCode."}}
```

Поэтому ядро не ходит в шлюз само, а запускает локальный CLI `opencode run`.
Он установлен в той же ВМ, что и ядро (`~/.opencode/bin/opencode`), и его
запросы шлюз принимает.

## Как это работает

```
пользователь
   │  текст
   ▼
ядро ──> opencode run -m opencode/deepseek-v4.1-flash-free "<промпт>"
   │         │  строгий JSON:
   │         │  {"reply": "...", "actions": [{"device":"...","action":"...","args":{}}]}
   │         ▼
   ├─ проверка действий по каталогу (core/catalog.py)
   ├─ опасные → pending + токен подтверждения
   ├─ обычные → hub.dispatch() → агент на устройстве
   └─ результаты (observations) отправляются модели вторым запросом →
      короткий ответ пользователю (до 3 шагов)
```

Особенности реализации (`core/planner.py`):

- CLI запускается **с `stdin=/dev/null`** — иначе `opencode run`, запущенный из
  скрипта/сервиса, ждёт ввода и висит бесконечно;
- рабочий каталог — служебная папка `data/planner` **без** `opencode.json`;
- все запуски сериализованы (модель возвращает план, ядро исполняет его само —
  модель ничего не выполняет);
- ответ модели режется по каталогу: действие не из каталога игнорируется.

## Установка в ВМ

```bash
curl -fsSL https://opencode.ai/install | bash
export PATH="$HOME/.opencode/bin:$PATH"
opencode --version
opencode models | head            # в списке есть бесплатные (*-free)
```

Проверка, что бесплатный тариф работает:

```bash
cd ~ && opencode run -m opencode/deepseek-v4.1-flash-free "Say hi" < /dev/null
```

Требуется ~2–4 ГБ свободной памяти: CLI — тяжёлый бинарь, при нехватке памяти он
будет убит OOM-киллером (симптом: `Killed`, пустой вывод). ВМ должна быть с
фиксированной памятью (см. `tools/resize_lab_vm.ps1`), а не с динамической.

## Настройки (.env)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `LLM_BACKEND` | `auto` | `auto` / `opencode` / `openai` |
| `OPENCODE_BIN` | `opencode` | путь к CLI (в сервисе укажите абсолютный) |
| `OPENCODE_MODEL` | `opencode/deepseek-v4.1-flash-free` | любая модель из `opencode models` |
| `OPENCODE_TIMEOUT` | `150` | сколько секунд ждать ответ |
| `OPENCODE_AGENT` | пусто | **не заполняйте**, см. ниже |

После правки: `sudo systemctl restart net-assistant-core`.

## Грабли (проверено на практике)

| Симптом | Причина / решение |
|---|---|
| `FreeTierError: ... only be used from within OpenCode` | используется **свой агент** (`--agent planner`) или `opencode.json` в рабочем каталоге. Бесплатный тариф принимает только конфигурацию по умолчанию — не задавайте `OPENCODE_AGENT` и держите папку планировщика чистой |
| Процесс висит, вывод пустой | CLI ждёт stdin: запускать только с `stdin=/dev/null` (ядро так и делает) |
| `Killed`, пустой вывод, в `dmesg` oom-killer | мало памяти: дайте ВМ ≥ 4 ГБ **фиксированной** памяти |
| `OpenCode не ответил за 150 с` | модель/сеть медленные: увеличьте `OPENCODE_TIMEOUT` |
| Ответ не JSON | ядро отдаёт текст как есть; обычно модель просто ответила словами — это нормальный сценарий для «просто поговорить» |
| Долгий первый ответ | первый запуск CLI поднимает своё окружение; дальше 3–8 с на запрос |

## Платный вариант (по желанию)

Если хочется быстрее/стабильнее — впишите ключ любого OpenAI-совместимого
провайдера, и режим `auto` сам переключится на облако:

```ini
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

Проверить, что включилось: `curl -s localhost:8787/health` → `"llm_backend":"openai"`.
