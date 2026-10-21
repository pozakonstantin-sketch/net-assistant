# Tailscale и exit node на роутере

Роутер (OpenWrt, `openwrt`, `100.68.176.36`) — единственный exit node в
tailnet: он анонсирует `0.0.0.0/0`, `::/0` и подсеть `192.168.1.0/24`.
ВМ `astra-hub` exit node не анонсирует и маршруты не принимает.

## Симптомы

1. На телефоне включён exit node `openwrt`, интернет вроде есть (Google
   открывается, `api.ipify.org` показывает `185.170.54.59`), но **сайты,
   которые на роутере идут через VPN (tachyon), не открываются**.
2. Или интернет пропадает полностью — это отдельная проблема с зонами
   firewall, см. раздел «Зоны firewall».

## Причина №1: tachyon исключает трафик из tailscale0

На роутере установлен **tachyon** (sing-box, режим tproxy) с fake-IP DNS
`198.18.0.0/15`. В сгенерированной им цепочке `TachyonTable mangle` было
правило:

```
iifname "tailscale0" return
```

Оно стояло **до** правил маркировки, поэтому весь трафик, приходящий из
exit-node, полностью выходил из проксирования и уходил напрямую в WAN.
Заблокированные сайты, естественно, не открывались.

## Причина №2: DNS телефона не попадал в tachyon

LAN-клиенты получают от dnsmasq fake-IP (`youtube.com -> 198.18.0.120`) и
потом идут на этот адрес — tachyon его перехватывает. Телефон же через
exit node использовал DNS мобильного оператора и получал **реальные IP**.
Маршрутизация по IP в tachyon не работала (набор `tachyon_subnets` пуст),
а fake-IP не появлялся — трафик шёл напрямую и блокировался.

Штатный механизм tachyon `dns_redirect` для этого не годится: набор
`tachyon_dns_sources` пуст, а правило редиректа указывает на порт `1603`,
который sing-box не слушает.

## Исправление (выполнено)

На роутере создан идемпотентный скрипт
`/usr/local/sbin/tachyon-tailnet-fix.sh`:

1. удаляет из `inet TachyonTable` цепочки `mangle` правило
   `iifname "tailscale0" return` (защита tailnet `100.64.0.0/10` и локальных
   подсетей остаётся);
2. добавляет в цепочку `dns_redirect` два правила DNAT для DNS-запросов,
   приходящих из `tailscale0`, на dnsmasq роутера:

```sh
nft add rule inet TachyonTable dns_redirect iifname tailscale0 udp dport 53 \
    counter dnat ip to 192.168.1.1:53
nft add rule inet TachyonTable dns_redirect iifname tailscale0 tcp dport 53 \
    counter dnat ip to 192.168.1.1:53
```

dnsmasq уже слушает `100.68.176.36:53`, форвардит на `127.0.0.42`
(sing-box DNS) и возвращает fake-IP.

Так как tachyon пересобирает свои nft-правила при каждом reload, скрипт
запускается демоном:

`/etc/init.d/tachyon-tailnet-fix` (procd, `S99`), внутри цикл
`fix; sleep 15`. Включён в автозапуск. После перезапуска tachyon правила
восстанавливаются максимум за 15 секунд.

Код: `deploy/tachyon-tailnet-fix.sh`,
`deploy/tachyon-tailnet-fix.init`.

## Проверка

На роутере:

```sh
nft list chain inet TachyonTable mangle | head -8
# не должно быть строки iifname "tailscale0" return

nft list chain inet TachyonTable dns_redirect | tail -3
# должны быть два правила dnat ip to 192.168.1.1:53
```

С ВМ (тот же путь, что у телефона). Важно: `tailscale up` требует указывать
все нестандартные флаги, иначе молча падает с ошибкой:

```sh
sudo tailscale up --exit-node=100.68.176.36 --exit-node-allow-lan-access \
     --accept-dns=false --hostname=astra-hub --operator=assistant
ip route get 8.8.8.8        # должен идти через tailscale0, table 52
curl -s -o /dev/null -w '%{http_code}\n' https://www.youtube.com   # 200
sudo tailscale set --exit-node=
```

С телефона (ADB):

```sh
adb shell ping -c 1 youtube.com     # должен показать 198.18.0.120
adb shell curl -s -m 15 -o /dev/null -w '%{http_code}\n' https://youtube.com
```

Ожидаемо: `200`, внешний IP `185.170.54.59`.

## Зоны firewall (старая проблема)

Если интернет через exit node пропадает полностью, проверить зоны: на
роутере была вторая зона `ts` для `tailscale0` без forwarding в `wan`.
Лечится добавлением forwarding `ts -> wan` и `ts -> lan` и `fw4 reload`.

## Если снова перестанет работать

1. `ps w | grep tachyon-tailnet` — демон должен быть запущен.
2. `/usr/local/sbin/tachyon-tailnet-fix.sh` — выполнить вручную.
3. Проверить, что после обновления tachyon не изменились имена цепочек
   (`nft list tables`, `TachyonTable`).
4. После обновления прошивки проверить, что в `/etc/rc.d` есть
   `S99tachyon-tailnet-fix`, а скрипт на месте.
