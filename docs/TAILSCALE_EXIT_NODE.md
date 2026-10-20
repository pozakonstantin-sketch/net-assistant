# Tailscale и exit node на роутере

Роутер (OpenWrt, `openwrt`, `100.68.176.36`) — единственный exit node в
tailnet: он анонсирует `0.0.0.0/0`, `::/0` и подсеть `192.168.1.0/24`.
ВМ `astra-hub` exit node не анонсирует и маршруты не принимает.

## Симптом

На телефоне включаешь exit node `openwrt` — интернет пропадает. При этом
DNS на телефон приходит (запросы к `192.168.1.1` идут через туннель и роутер
их обслуживает), а сайты не открываются.

## Причина

На роутере оказалось **две зоны firewall для одного интерфейса
`tailscale0`**:

| Секция | Имя зоны | Что было |
|---|---|---|
| `@zone[2]` | `tailscale` | forwarding в `lan` и `wan` — правильная |
| `tstz` | `ts` | только `input/output/forward ACCEPT`, **без forwarding** |

fw4 отправлял трафик с `tailscale0` в зону `ts`, её цепочка `forward_ts`
принимала только трафик **в** tailscale, а дальше срабатывал
`handle_reject` — роутер отвечал `Destination Port Unreachable`.
Поэтому работал только доступ к самому роутеру (DNS), но не выход в интернет.

## Исправление (выполнено)

```sh
uci add firewall forwarding
uci set firewall.@forwarding[-1].src='ts'
uci set firewall.@forwarding[-1].dest='wan'

uci add firewall forwarding
uci set firewall.@forwarding[-1].src='ts'
uci set firewall.@forwarding[-1].dest='lan'

uci commit firewall
fw4 reload
```

После этого цепочка выглядит так:

```
chain forward_ts {
    jump accept_to_wan  # Accept ts to wan forwarding
    jump accept_to_lan  # Accept ts to lan forwarding
    jump accept_to_ts
}
```

## Проверка

Проверять удобно с ВМ, не трогая телефон:

```sh
sudo tailscale set --exit-node=100.68.176.36 --exit-node-allow-lan-access=false
ping -c 2 8.8.8.8
curl -s https://ifconfig.me          # внешний IP роутера
getent hosts ya.ru                   # DNS через exit node
sudo tailscale set --exit-node=      # вернуть как было
```

Ожидаемо: ping проходит, `ifconfig.me` отдаёт публичный IP роутера
(`185.170.54.59`), DNS отвечает.

## Диагностика

```sh
/opt/net-assistant/.venv/bin/python tools/router_tailscale_diag.py
```

Скрипт только читает настройки: версию, статус, prefs, адреса, маршруты,
`ip_forward`, зоны и forwarding-правила firewall, цепочку `forward_ts`,
проверку интернета и DNS с роутера.

## Если снова пропадёт интернет на телефоне

1. Выключить и снова включить exit node (или перезапустить Tailscale на телефоне).
2. Проверить, что в админке Tailscale exit node `openwrt` по-прежнему одобрен.
3. Запустить диагностику выше и посмотреть `forward_ts` и `ip_forward`.
4. Убедиться, что после обновления прошивки не появилась третья зона для
   `tailscale0` (в `uci show firewall` у всех зон должен быть forwarding в `wan`).
