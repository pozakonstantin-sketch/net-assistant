#!/bin/sh
# Allow Tailscale clients that use this router as an exit node to:
#   1) have their traffic proxied by tachyon (remove tailscale0 bypass)
#   2) resolve DNS via dnsmasq -> tachyon fake-IP DNS
# Idempotent: safe to run every few seconds.

[ -d /sys/class/net/tailscale0 ] || exit 0
nft list table inet TachyonTable >/dev/null 2>&1 || exit 0

# 1) drop the "iifname tailscale0 return" bypass in the prerouting mangle chain
nft -a list chain inet TachyonTable mangle 2>/dev/null | while IFS= read -r line; do
	case "$line" in
		*'iifname "tailscale0" return'*)
			h=$(printf '%s\n' "$line" | sed -n 's/.*# handle \([0-9][0-9]*\).*/\1/p')
			[ -n "$h" ] && nft delete rule inet TachyonTable mangle handle "$h" 2>/dev/null
			;;
	esac
done

# 2) DNS from tailnet clients -> dnsmasq on the router (which forwards to tachyon DNS)
nft list chain inet TachyonTable dns_redirect 2>/dev/null | grep -q 'iifname "tailscale0" udp dport 53' || \
	nft add rule inet TachyonTable dns_redirect iifname tailscale0 udp dport 53 counter dnat ip to 192.168.1.1:53 2>/dev/null

nft list chain inet TachyonTable dns_redirect 2>/dev/null | grep -q 'iifname "tailscale0" tcp dport 53' || \
	nft add rule inet TachyonTable dns_redirect iifname tailscale0 tcp dport 53 counter dnat ip to 192.168.1.1:53 2>/dev/null

exit 0
