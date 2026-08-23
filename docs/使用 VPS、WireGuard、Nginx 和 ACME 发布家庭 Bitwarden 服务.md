  # 使用 VPS、WireGuard、Nginx 和 ACME 发布家庭 Bitwarden 服务

  ## 1. 目标架构

  目标域名：

  mypass.animagi.top

  网络链路：

  浏览器 / Bitwarden 客户端
          |
          | HTTPS 443
          v
  mypass.animagi.top
          |
          v
  VPS Nginx
          |
          | WireGuard 隧道
          v
  家庭 OpenWrt
          |
          v
  家庭局域网 Bitwarden/Vaultwarden

  示例地址：

  VPS公网IP：207.246.100.105
  VPS WireGuard：10.88.0.1
  OpenWrt WireGuard：10.88.0.2
  家庭OpenWrt LAN：192.168.100.254
  Vaultwarden：192.168.100.20:8080
  域名：mypass.animagi.top

  本方案中：

  - 域名继续在阿里云注册和管理；
  - mypass.animagi.top 指向 VPS；
  - VPS 使用 Nginx 处理 HTTPS；
  - VPS 和 OpenWrt 通过 WireGuard 连接；
  - Nginx 通过 WireGuard 访问家庭内网；
  - 家庭公网 IP 变化不影响 WireGuard 长连接；
  - 不需要把家庭 Bitwarden 端口直接暴露到公网；
  - Hysteria2 可以继续使用原来的 hy2.animagi.top 和 UDP 9443。

------

  ## 2. 使用前替换变量

  下面所有命令中的示例值都需要根据实际情况替换：

  VPS_PUBLIC_IP=207.246.100.105
  VPS_WG_IP=10.88.0.1
  OPENWRT_WG_IP=10.88.0.2
  OPENWRT_LAN_IP=192.168.100.254
  VAULTWARDEN_IP=192.168.100.20
  VAULTWARDEN_PORT=8080
  DOMAIN=mypass.animagi.top

  如果 Vaultwarden 实际端口不是 8080，将所有 192.168.100.20:8080 替换为实际地址。

  如果使用的是官方 Bitwarden，而不是 Vaultwarden，请以官方 Bitwarden 的服务端配置为准。本文主要按 Vaultwarden 或兼容的
  Web 密码库服务编写。

------

  ## 3. 阿里云配置 DNS

  在阿里云 DNS 控制台新增一条 A 记录：

  主机记录：mypass
  记录类型：A
  记录值：VPS公网IPv4
  TTL：600

  最终记录为：

  mypass.animagi.top -> VPS公网IP

  现有记录不需要删除：

  hy2.animagi.top
  opwrt.animagi.top

  同一个 VPS 可以承载多个域名：

  hy2.animagi.top      -> Hysteria2 UDP 9443
  mypass.animagi.top  -> Nginx TCP 443

  DNS 不需要为每个服务购买新的域名套餐。子域名只是 DNS 记录，通常可以创建很多个。

  确认 DNS 生效：

  Resolve-DnsName mypass.animagi.top

  预期返回 VPS 的公网 IP。

------

  ## 4. VPS 安装 WireGuard

  以下命令以 Debian/Ubuntu 为例。

  ### 4.1 安装软件

```bash
sudo apt update
sudo apt install -y wireguard
```

  ### 4.2 生成 VPS 密钥

```bash
sudo umask 077
sudo wg genkey | sudo tee /etc/wireguard/server_private.key
sudo cat /etc/wireguard/server_private.key | wg pubkey | sudo tee /etc/wireguard/server_public.key
```

  查看 VPS 公钥：

```bash
sudo cat /etc/wireguard/server_public.key
```

  记录这个值，后面配置 OpenWrt 时使用。

  ### 4.3 创建 VPS WireGuard 配置

  编辑：

```
sudo nano /etc/wireguard/wg0.conf
```

  写入：

```conf
[Interface]
  Address = 10.88.0.1/24
  ListenPort = 51820
  PrivateKey = VPS_PRIVATE_KEY

[Peer]
# OpenWrt
PublicKey = OPENWRT_PUBLIC_KEY
AllowedIPs = 10.88.0.2/32, 192.168.100.20/32
```

  替换：

  VPS_PRIVATE_KEY
  OPENWRT_PUBLIC_KEY

  其中：

  - VPS_PRIVATE_KEY 是 VPS 的私钥；
  - OPENWRT_PUBLIC_KEY 是 OpenWrt 生成的公钥；
  - 192.168.100.20/32 表示 VPS 只通过该隧道访问 Vaultwarden 这台主机。

  不要把 VPS 私钥发给任何人。

  启动 WireGuard：

```bash
sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0
```

  查看状态：

```bash
 sudo wg show
 ip addr show wg0
```

------

  ## 5. OpenWrt 配置 WireGuard

  推荐使用 OpenWrt 的 LuCI 图形界面配置。

  进入：

  网络 -> 接口 -> 添加新接口

  配置：

  接口名称：wg_vps
  协议：WireGuard VPN

  ### 5.1 OpenWrt WireGuard 私钥

  在 OpenWrt SSH 中执行：

```bash
 umask 077
 wg genkey | tee /etc/wireguard/openwrt_private.key
 cat /etc/wireguard/openwrt_private.key | wg pubkey
```

  记录输出的公钥，将它填入 VPS 的：

  PublicKey = OPENWRT_PUBLIC_KEY

  在 LuCI 的 WireGuard 接口中填入：

  私钥：OpenWrt 私钥
  IP 地址：10.88.0.2/24
  监听端口：51820

  ### 5.2 OpenWrt 添加 VPS Peer

  在 wg_vps 接口中新增 Peer：

  公钥：VPS_PUBLIC_KEY
  允许的 IP：10.88.0.1/32
  Endpoint 主机：VPS公网IP
  Endpoint 端口：51820
  Persistent Keepalive：25

  如果 LuCI 中有“路由允许的 IP”选项，填写：

  10.88.0.1/32

  不要填写：

  0.0.0.0/0

  否则可能导致 OpenWrt 的全部流量经过 VPS。

  ### 5.3 OpenWrt 防火墙区域

  在：

  网络 -> 防火墙 -> 常规设置

  创建一个新的防火墙区域：

  名称：wg_vps
  输入：拒绝
  输出：接受
  转发：拒绝
  网络：wg_vps

  添加转发：

  来源区域：wg_vps
  目标区域：lan

  然后添加一条仅允许访问 Vaultwarden 的流量规则：

  名称：Allow-WireGuard-to-Vaultwarden
  协议：TCP
  来源区域：wg_vps
  来源地址：10.88.0.1
  目标区域：lan
  目标地址：192.168.100.20
  目标端口：8080
  动作：接受

  如果 Vaultwarden 使用其他端口，替换目标端口。

  

------

  ## 6. 配置家庭网络回程路由

  WireGuard 通道建立后，VPS 发往：

  192.168.100.20

  的流量会进入 OpenWrt。

  但是 Vaultwarden 返回给：

  10.88.0.1

  的流量需要有正确回程路径。

  ### 方案 A：主路由添加静态路由，推荐

  在爱快主路由中添加静态路由：

  目标网段：10.88.0.0/24
  下一跳：192.168.100.254
  接口：LAN

  这样家庭设备知道：

  访问 10.88.0.0/24
      -> 交给 OpenWrt 192.168.100.254

  ### 方案 B：OpenWrt 对访问流量做源地址伪装

  如果不方便修改爱快路由，可以在 OpenWrt 的 wg_vps -> lan 转发上开启 Masquerade。

  这样 Vaultwarden 看到的访问来源会变成：

  192.168.100.254

  而不是：

  10.88.0.1

  优点是不用改主路由。

  缺点是 Vaultwarden 看不到真实的 VPS WireGuard 地址。

  如果使用方案 B，需要确认 OpenWrt 防火墙区域中启用了：

  IP 动态伪装：开启

  只对 wg_vps -> lan 使用，不要开启全局任意 NAT。

------

  ## 7. 验证 WireGuard 连接

  ### 7.1 VPS 查看握手

  sudo wg show

  正常时应看到：

  latest handshake: 几秒前
  transfer: received ... sent ...

  如果没有 latest handshake：

  - 检查 VPS UDP 51820 是否放行；
  - 检查 OpenWrt Endpoint 是否正确；
  - 检查 VPS 和 OpenWrt 的公钥是否填反；
  - 检查 OpenWrt 是否启用了 WireGuard 接口。

  ### 7.2 VPS Ping OpenWrt

  ping -c 4 10.88.0.2

  如果成功，说明 WireGuard 基础连接正常。

  ### 7.3 VPS 测试 Vaultwarden

  

```bash
curl -v \
    --connect-timeout 5 \
    --max-time 10 \
    http://192.168.100.20:8080
```

  如果 Vaultwarden 使用 HTTPS：

```bash
 curl -vk \
    --connect-timeout 5 \
    --max-time 10 \
    https://192.168.100.20:8443
```

  必须先保证 VPS 能访问家庭内网服务，再配置 Nginx。

------

  ## 8. VPS 安装 Nginx

```
 sudo apt update
 sudo apt install -y nginx
```

  确认 Nginx 正常：

```
sudo systemctl enable nginx
sudo systemctl start nginx
sudo systemctl status nginx
```

  放行 HTTP 和 HTTPS：

```
 sudo ufw allow 80/tcp
 sudo ufw allow 443/tcp
```

  如果使用 VPS 云安全组，也需要放行：

  TCP 80
  TCP 443
  UDP 51820
  UDP 9443

  不要放行家庭 Vaultwarden 的端口，例如：

  8080
  8443

  这些端口只应该通过 WireGuard 访问。

------

  ## 9. 配置 Nginx HTTP 站点

  创建目录：

```
sudo mkdir -p /var/www/acme
sudo chown -R www-data:www-data /var/www/acme
```

  创建初始 Nginx 配置：

```
sudo nano /etc/nginx/sites-available/mypass.animagi.top
```

  写入：

```nginx
server {
	listen 80;
	listen [::]: 80;
    
	server_name mypass.animagi.top;
    
	location /.well-known/acme-challenge/ {
		root /var/www/acme;
    }
	location / {
		return 404;
	}
}
```

  启用站点：

```
sudo ln -s /etc/nginx/sites-available/mypass.animagi.top \
    /etc/nginx/sites-enabled/mypass.animagi.top
```

  检查配置：

```
sudo nginx -t
```

  重新加载：

```
sudo systemctl reload nginx
```

  测试 ACME 目录：

```
echo test | sudo tee /var/www/acme/test.txt
```

  外部访问：

  http://mypass.animagi.top/.well-known/acme-challenge/test.txt

  确认能访问后删除测试文件：

```
sudo rm /var/www/acme/test.txt
```

------

  ## 10. 使用 Certbot 申请证书

  安装 Certbot：

```
sudo apt install -y certbot
```

  申请证书：

```
sudo certbot certonly \
    --webroot \
    -w /var/www/acme \
    -d mypass.animagi.top \
    --email your-email@example.com \
    --agree-tos \
    --no-eff-email
```

  成功后证书位置通常是：

  /etc/letsencrypt/live/mypass.animagi.top/fullchain.pem
  /etc/letsencrypt/live/mypass.animagi.top/privkey.pem

  检查证书：

```
sudo certbot certificates
```

  测试自动续期：

```
 sudo certbot renew --dry-run
```

  配置续期后自动重载 Nginx：

```
sudo certbot renew \
    --deploy-hook "systemctl reload nginx"
```

  如果系统已经安装了 Certbot systemd timer，只需要确认：

```
systemctl list-timers | grep certbot
```

------

  ## 11. 配置 Nginx HTTPS 反向代理

  编辑站点配置：

```
sudo nano /etc/nginx/sites-available/mypass.animagi.top
```

  替换为：

```nginx
server {
    listen 80;
    listen [::]:80;

    server_name mypass.animagi.top;

    location /.well-known/acme-challenge/ {
        root /var/www/acme;
    }

    location / {
        return 301 https://$host$request_uri;
    }
  }

  server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;

    server_name mypass.animagi.top;

    ssl_certificate /etc/letsencrypt/live/mypass.animagi.top/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mypass.animagi.top/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;
    ssl_session_tickets off;

    client_max_body_size 128M;

    location / {
        proxy_pass http://192.168.100.20:8080;

        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host $host;

        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;

        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
  }
```

  为了让 WebSocket 连接正常，在 Nginx 主配置中增加 map：

```
sudo nano /etc/nginx/nginx.conf
```

  在 http { 块中加入：

```nginx
map $http_upgrade $connection_upgrade {
      default upgrade;
      '' close;
  }
```

  最终结构类似：

```nginx
http {
    map $http_upgrade $connection_upgrade {
        default upgrade;
            '' close;
    }
​    ...
}
```

  检查并重新加载：

```
sudo nginx -t
sudo systemctl reload nginx
```

  如果实际 Vaultwarden 使用 HTTPS 回源，将：

  proxy_pass http://192.168.100.20:8080;

  改为：

  proxy_pass https://192.168.100.20:8443;

  如果内网证书是自签名证书，还需要：

  proxy_ssl_verify off;

  但优先使用 HTTP 内网回源，外部访问由 VPS Nginx 提供 HTTPS。

------

  ## 12. Vaultwarden 配置

  如果使用 Docker Compose，环境变量建议包含：

  environment:
    DOMAIN: "https://mypass.animagi.top"
    WEBSOCKET_ENABLED: "true"

  示例：

  services:
    vaultwarden:
      image: vaultwarden/server:latest
      container_name: vaultwarden
      restart: unless-stopped

​      environment:
​        DOMAIN: "https://mypass.animagi.top"
​        WEBSOCKET_ENABLED: "true"
​        SIGNUPS_ALLOWED: "false"

​      volumes:
​        - ./vw-data:/data

​      ports:
​        - "192.168.100.20:8080:80"

  说明：

  DOMAIN

  必须填写外部 HTTPS 地址：

  https://mypass.animagi.top

  不要填写：

  http://192.168.100.20:8080

  如果你已经有 Vaultwarden 配置，不要直接覆盖原有环境变量，只补充或修改：

  DOMAIN
  WEBSOCKET_ENABLED
  SIGNUPS_ALLOWED

  修改后重启：

  docker compose up -d

  确认容器状态：

  docker compose ps
  docker compose logs --tail 100 vaultwarden

------

  ## 13. 外部访问测试

  先在 VPS 本机测试：

  curl -Ik https://mypass.animagi.top

  再从家庭 Wi-Fi 外部测试，例如手机关闭 Wi-Fi 使用移动网络：

  https://mypass.animagi.top

  检查证书：

  openssl s_client \
    -connect mypass.animagi.top:443 \
    -servername mypass.animagi.top </dev/null 2>/dev/null \
    | openssl x509 -noout -subject -issuer -dates

  测试 WebSocket 路径时，重点关注 Vaultwarden 的：

  /notifications/hub

  如果网页可以打开但客户端无法同步，优先检查：

  - DOMAIN 是否是 HTTPS 外部域名；
  - WEBSOCKET_ENABLED 是否为 true；
  - Nginx 是否保留了 Upgrade 和 Connection 请求头；
  - Nginx proxy_read_timeout 是否足够长；
  - VPS 是否能通过 WireGuard访问 192.168.100.20:8080。

------

  ## 14. 防火墙最小开放范围

  ### VPS 云安全组

  只开放：

  TCP 80
  TCP 443
  UDP 51820
  UDP 9443

  其中：

  TCP 80  -> ACME HTTP 验证和跳转
  TCP 443 -> Nginx HTTPS
  UDP 51820 -> WireGuard
  UDP 9443 -> Hysteria2

  ### VPS 系统防火墙

  sudo ufw allow 80/tcp
  sudo ufw allow 443/tcp
  sudo ufw allow 51820/udp
  sudo ufw allow 9443/udp
  sudo ufw enable
  sudo ufw status

  不开放：

  TCP 8080
  TCP 8443
  TCP 2084

  家庭 Vaultwarden 端口只通过 WireGuard访问。

------

  ## 15. 故障排查顺序

  ### WireGuard 无握手

  在 VPS：

  sudo wg show

  检查：

  latest handshake
  transfer: received
  transfer: sent

  无握手时检查：

  - OpenWrt Endpoint 是否填写 VPS 公网 IP；
  - VPS UDP 51820 是否放行；
  - 云安全组是否放行 UDP 51820；
  - 两端公钥是否填反；
  - OpenWrt 是否启用 PersistentKeepalive 25。

  ### VPS 无法访问 Vaultwarden

  ping -c 4 10.88.0.2
  curl -v http://192.168.100.20:8080

  如果能 Ping 10.88.0.2，不能访问 192.168.100.20:8080，检查：

  - OpenWrt wg_vps -> lan 转发；
  - OpenWrt 目标地址和端口规则；
  - Vaultwarden 是否监听正确地址；
  - 主路由回程路由；
  - 是否需要启用 OpenWrt Masquerade。

  ### 证书申请失败

  sudo certbot renew --dry-run

  检查：

  Resolve-DnsName mypass.animagi.top
  sudo systemctl status nginx
  sudo nginx -t

  确保：

  mypass.animagi.top 指向 VPS
  VPS TCP 80 可访问
  Nginx 正在监听 80
  .well-known/acme-challenge/ 路径没有被拦截

  ### 网页能打开但客户端无法同步

  检查：

  DOMAIN=https://mypass.animagi.top
  WEBSOCKET_ENABLED=true

  并确认 Nginx 配置中存在：

  proxy_http_version 1.1;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection $connection_upgrade;

------

  ## 16. 安全建议

  Bitwarden/Vaultwarden 保存的是高价值凭据，建议：

  - 关闭公开注册：

  SIGNUPS_ALLOWED=false

  - 设置强管理员令牌；
  - 定期备份 Vaultwarden 数据目录；
  - VPS 只允许 Nginx 访问家庭 Vaultwarden；
  - 不要将家庭整个 192.168.100.0/24 暴露给 VPS；
  - 不要开放家庭 Vaultwarden 的公网端口；
  - 不要把 WireGuard 私钥提交到 Git；
  - 定期检查 Nginx 访问日志；
  - 保持 Vaultwarden 和系统及时更新；
  - 证书续期失败时及时处理；
  - 开启 Bitwarden 客户端的生物识别或系统密钥保护。

  备份示例：

  docker compose down
  tar -czf vaultwarden-backup-$(date +%F).tar.gz ./vw-data
  docker compose up -d

  备份文件应保存到另一台设备或离线存储，不要只保存在 Vaultwarden 所在磁盘。

------

  ## 17. 最终检查清单

- [ ] 阿里云已添加 mypass.animagi.top A 记录
- [ ] DNS 已解析到 VPS 公网 IP
- [ ] VPS UDP 51820 已放行
- [ ] VPS 和 OpenWrt WireGuard 已握手
- [ ] VPS 能访问 10.88.0.2
- [ ] VPS 能访问 192.168.100.20:8080
- [ ] VPS TCP 80/443 已放行
- [ ] Certbot 已成功申请证书
- [ ] certbot renew --dry-run 成功
- [ ] Nginx 配置检查通过
- [ ] Vaultwarden DOMAIN 使用 HTTPS 外部域名
- [ ] Vaultwarden WebSocket 已启用
- [ ] 浏览器可以访问 https://mypass.animagi.top
- [ ] Bitwarden 客户端可以同步
- [ ] Vaultwarden 未开放公网端口
- [ ] Vaultwarden 数据已完成备份


最终访问地址：

https://mypass.animagi.top

最终流量路径：

用户
  -> VPS:443
  -> Nginx
  -> WireGuard:10.88.0.2
  -> OpenWrt
  -> 192.168.100.20:8080