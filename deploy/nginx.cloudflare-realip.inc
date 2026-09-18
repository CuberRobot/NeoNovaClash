# Cloudflare 回源真实 IP 识别（被 80 / 443 两个 server 块 include）
#
# 安装位置：/www/server/panel/vhost/nginx/neonovaclash-cf-realip.inc
# 注意：文件名故意用 .inc 而不是 .conf —— 宝塔主配置里的
#      include /www/server/panel/vhost/nginx/*.conf 只吃 .conf，
#      这样本文件不会被误当成站点配置在 http 层重复加载。
#
# IP 段来自 Cloudflare 官方：https://www.cloudflare.com/ips-v4 与 ips-v6
# Cloudflare 会不定期更新，建议每隔几个月对一次。
#
# 127.0.0.1 / ::1 这两条是给 Cloudflare Tunnel 用的：
# 走隧道时请求来自本机的 cloudflared，真实访客 IP 在 CF-Connecting-IP 里。

set_real_ip_from 127.0.0.1;
set_real_ip_from ::1;
set_real_ip_from 173.245.48.0/20;
set_real_ip_from 103.21.244.0/22;
set_real_ip_from 103.22.200.0/22;
set_real_ip_from 103.31.4.0/22;
set_real_ip_from 141.101.64.0/18;
set_real_ip_from 108.162.192.0/18;
set_real_ip_from 190.93.240.0/20;
set_real_ip_from 188.114.96.0/20;
set_real_ip_from 197.234.240.0/22;
set_real_ip_from 198.41.128.0/17;
set_real_ip_from 162.158.0.0/15;
set_real_ip_from 104.16.0.0/13;
set_real_ip_from 104.24.0.0/14;
set_real_ip_from 172.64.0.0/13;
set_real_ip_from 131.0.72.0/22;
set_real_ip_from 2400:cb00::/32;
set_real_ip_from 2606:4700::/32;
set_real_ip_from 2803:f800::/32;
set_real_ip_from 2405:b500::/32;
set_real_ip_from 2405:8100::/32;
set_real_ip_from 2a06:98c0::/29;
set_real_ip_from 2c0f:f248::/32;

real_ip_header CF-Connecting-IP;
