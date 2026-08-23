# 使用 Cloudflare 管理域名并通过 Tunnel 访问家庭内网服务

  ## 1. 目标

  保留现有域名和前缀不变，例如：

  hy2.animagi.top
  opwrt.animagi.top

  新增一个专门交给 Cloudflare 管理的子域：

  cf.animagi.top

  家庭内网服务统一使用这个子域：

  nas.cf.animagi.top
  photo.cf.animagi.top
  files.cf.animagi.top
  ha.cf.animagi.top

  整体链路：

  用户
    -> Cloudflare HTTPS
    -> Cloudflare Tunnel
    -> 家庭局域网 cloudflared
    -> 内网服务

  Cloudflare Tunnel 由家庭网络主动连接 Cloudflare，因此不需要家庭固定公网 IP，也不需要为每个服务单独配置公网端口和
  DDNS。

  ———

  ## 2. 域名和 DNS 规划

  域名注册和续费继续使用阿里云：

  注册商：阿里云
  域名：animagi.top

  建议新增一个子域专门用于 Cloudflare Tunnel：

  cf.animagi.top

  服务命名：

   服务              访问地址                 内网地址示例
  ━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━
   NAS 主页          nas.cf.animagi.top       192.168.100.20:5000
  ────────────────  ───────────────────────  ─────────────────────
   NAS 相册          photo.cf.animagi.top     192.168.100.20:7000
  ────────────────  ───────────────────────  ─────────────────────
   文件服务          files.cf.animagi.top     192.168.100.20:8080
  ────────────────  ───────────────────────  ─────────────────────
   Home Assistant    ha.cf.animagi.top        192.168.100.30:8123
  ────────────────  ───────────────────────  ─────────────────────
   Jellyfin          media.cf.animagi.top     192.168.100.40:8096
  ────────────────  ───────────────────────  ─────────────────────
   OpenWrt 管理页    router.cf.animagi.top    192.168.100.254:80
  ────────────────  ───────────────────────  ─────────────────────
   内网 API          api.cf.animagi.top       192.168.100.50:3000

  实际使用时，把示例 IP 和端口替换成自己的服务地址。

  ———

  ## 3. 关于“只把一个前缀交给 Cloudflare”

  你希望：

  animagi.top                  继续由阿里云管理
  cf.animagi.top               交给 Cloudflare
  nas.cf.animagi.top           由 Cloudflare Tunnel 使用

  技术上可以通过子域委派实现，但需要注意 Cloudflare 当前账户和套餐是否允许把 cf.animagi.top 作为独立 DNS Zone 添加。

  ### 阿里云控制台操作

  1. 在 Cloudflare 添加子域：

  cf.animagi.top

  2. Cloudflare 会分配一组 Nameserver，例如：

  xxxx.ns.cloudflare.com
  yyyy.ns.cloudflare.com

  3. 在阿里云 DNS 中新增两条 NS 记录：

  主机记录：cf
  记录类型：NS
  记录值：xxxx.ns.cloudflare.com

  主机记录：cf
  记录类型：NS
  记录值：yyyy.ns.cloudflare.com

  4. 等待 DNS 生效后，在 Cloudflare 中确认：

  cf.animagi.top

  已经完成激活。

  委派完成后的关系是：

  animagi.top
    -> 阿里云 DNS

  cf.animagi.top
    -> Cloudflare DNS

  nas.cf.animagi.top
    -> Cloudflare Tunnel

  现有记录不受影响：

  hy2.animagi.top
  opwrt.animagi.top

  仍然由阿里云 DNS 管理。

  ### 重要兼容性说明

  Cloudflare 的免费账户对“子域作为独立 Zone 委派”的支持可能受当前产品策略限制。如果 Cloudflare 不允许添加
  cf.animagi.top，采用下面的备用方案：

  把 animagi.top 的整个 DNS 托管迁移到 Cloudflare

  域名仍然在阿里云购买和续费，只把 Nameserver 改成 Cloudflare 的 Nameserver。这样现有前缀仍可以保持不变：

  hy2.animagi.top
  opwrt.animagi.top

  只是 DNS 管理由 Cloudflare 接管。

  ———

  ## 4. DNS 迁移前检查

  如果使用子域委派，只需要迁移 cf.animagi.top 相关配置。

  如果改为整个域名迁移到 Cloudflare，需要先复制阿里云中的所有 DNS 记录：

  A
  AAAA
  CNAME
  MX
  TXT
  SPF
  DKIM
  DMARC
  NS

  尤其检查：

  hy2.animagi.top
  opwrt.animagi.top
  邮件相关记录

  Cloudflare Tunnel 不依赖家庭公网 IP，因此 Tunnel 服务本身不需要 DDNS。

  原有 DDNS 是否保留：

   用途                                 是否需要 DDNS
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━
   Cloudflare Tunnel                           不需要
  ───────────────────────────────────  ───────────────
   直接访问家庭公网 IP                           需要
  ───────────────────────────────────  ───────────────
   Hysteria2 直接连接                        通常需要
  ───────────────────────────────────  ───────────────
   其他端口转发服务                              需要
  ───────────────────────────────────  ───────────────
   仅通过 Cloudflare 访问的 Web 服务           不需要

  ———

  ## 5. 安装 cloudflared

  在家庭网络中选择一台长期运行的设备安装 cloudflared，例如：

  iStoreOS
  NAS
  Linux 小主机
  Docker 主机

  建议运行在：

  iStoreOS 或 NAS

  该设备必须能够访问所有需要发布的内网服务。

  安装完成后确认：

  cloudflared --version

  登录 Cloudflare：

  cloudflared tunnel login

  命令会生成登录链接，在浏览器中选择对应的 Cloudflare 域名。

  创建 Tunnel：

  cloudflared tunnel create home-lan

  查看 Tunnel：

  cloudflared tunnel list

  记录 Tunnel ID。

  ———

  ## 6. Tunnel 配置

  创建配置文件：

  /etc/cloudflared/config.yml

  示例：

  tunnel: YOUR_TUNNEL_ID
  credentials-file: /etc/cloudflared/YOUR_TUNNEL_ID.json

  ingress:
    - hostname: nas.cf.animagi.top
      service: http://192.168.100.20:5000

    - hostname: photo.cf.animagi.top
      service: http://192.168.100.20:7000
    
    - hostname: files.cf.animagi.top
      service: http://192.168.100.20:8080
    
    - hostname: ha.cf.animagi.top
      service: http://192.168.100.30:8123
    
    - hostname: media.cf.animagi.top
      service: http://192.168.100.40:8096
    
    - hostname: api.cf.animagi.top
      service: http://192.168.100.50:3000
    
    - service: http_status:404

  最后一条必须保留：

  - service: http_status:404

  它表示没有匹配到域名时返回 404，不允许请求随意转发到其他内网地址。

  ———

  ## 7. 创建域名路由

  为每个服务创建 DNS 路由：

  cloudflared tunnel route dns home-lan nas.cf.animagi.top
  cloudflared tunnel route dns home-lan photo.cf.animagi.top
  cloudflared tunnel route dns home-lan files.cf.animagi.top
  cloudflared tunnel route dns home-lan ha.cf.animagi.top
  cloudflared tunnel route dns home-lan media.cf.animagi.top
  cloudflared tunnel route dns home-lan api.cf.animagi.top

  这些命令通常会在 Cloudflare DNS 中自动创建类似记录：

  nas.cf.animagi.top
    CNAME
    YOUR_TUNNEL_ID.cfargotunnel.com

  不要再在阿里云中为这些服务创建 A 记录指向家庭公网 IP。

  ———

  ## 8. 启动和测试 Tunnel

  前台测试：

  cloudflared tunnel --config /etc/cloudflared/config.yml run home-lan

  如果连接正常，应看到 Tunnel 已连接的日志。

  确认家庭设备能访问内网服务：

  curl -I http://192.168.100.20:5000
  curl -I http://192.168.100.30:8123

  然后从外部网络访问：

  https://nas.cf.animagi.top
  https://photo.cf.animagi.top
  https://ha.cf.animagi.top

  测试时使用手机移动网络，避免因为处于家庭 Wi-Fi 而误判。

  ———

  ## 9. 配置为开机自启

  如果系统支持 systemd：

  sudo cloudflared service install
  sudo systemctl enable cloudflared
  sudo systemctl start cloudflared

  查看状态：

  systemctl status cloudflared

  如果运行在 Docker 中，可以使用：

  services:
    cloudflared:
      image: cloudflare/cloudflared:latest
      restart: unless-stopped
      command: tunnel --config /etc/cloudflared/config.yml run
      volumes:
        - ./cloudflared:/etc/cloudflared

  注意把凭据文件和配置文件放到：

  ./cloudflared/

  ———

  ## 10. 安全配置

  不建议把以下服务直接暴露给整个公网：

  OpenWrt 管理页
  NAS 管理页
  Home Assistant 管理页
  数据库管理页
  Docker 管理页

  建议在 Cloudflare Zero Trust 中配置 Access：

  只允许自己的邮箱登录
  启用一次性验证码或第三方身份认证

  推荐策略：

   服务                建议
  ━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━
   博客、主页          可以公开
  ──────────────────  ─────────────────────
   状态页              可以公开或加 Access
  ──────────────────  ─────────────────────
   NAS 主页            加 Access
  ──────────────────  ─────────────────────
   相册                根据内容决定
  ──────────────────  ─────────────────────
   文件服务            加 Access
  ──────────────────  ─────────────────────
   Home Assistant      加 Access
  ──────────────────  ─────────────────────
   OpenWrt 管理页      必须加 Access
  ──────────────────  ─────────────────────
   数据库和内部 API    不建议直接公开

  Cloudflare Access 的基本逻辑：

  用户访问服务
    -> Cloudflare Access 登录
    -> 身份验证通过
    -> Cloudflare Tunnel
    -> 家庭服务

  ———

  ## 11. 带宽和流量规划

  Cloudflare Tunnel 不经过你的 VPS，但访问流量仍然经过 Cloudflare：

  用户 -> Cloudflare -> 家庭网络

  适合：

  管理后台
  博客
  相册缩略图
  轻量 API
  Home Assistant
  状态页

  不建议长期用于：

  NAS 大文件下载
  视频流媒体
  Jellyfin 高码率播放
  监控录像
  备份文件传输

  原因：

  - 家庭上行带宽可能成为瓶颈；
  - Cloudflare 代理会增加传输链路；
  - 大流量服务可能受到 Cloudflare 产品条款和流量策略限制；
  - 移动网络访问大文件的成本较高。

  建议把媒体服务配置为：

  只允许自己访问
  限制并发连接
  限制上传和下载
  必要时使用专门的远程访问方案

  ———

  ## 12. 推荐的最终结构

  animagi.top
  |
  +-- hy2.animagi.top
  |     -> 现有 Hysteria2 服务
  |     -> 保持现状
  |
  +-- opwrt.animagi.top
  |     -> 现有服务
  |     -> 保持现状
  |
  +-- cf.animagi.top
        -> Cloudflare DNS / Tunnel 子域
        |
        +-- nas.cf.animagi.top
        +-- photo.cf.animagi.top
        +-- files.cf.animagi.top
        +-- ha.cf.animagi.top
        +-- media.cf.animagi.top
        +-- api.cf.animagi.top

  推荐的低维护组合：

  主页/博客：Cloudflare Pages
  家庭 Web 服务：Cloudflare Tunnel
  Hysteria2：继续使用现有域名
  高流量媒体和大文件：不要通过 Cloudflare Tunnel
  路由器和管理后台：Cloudflare Access 保护

  ## 13. 实施顺序

  建议按以下顺序执行：

  1. 在 Cloudflare 添加并验证 cf.animagi.top；
  2. 在阿里云配置 cf 子域的 NS 委派；
  3. 在 iStoreOS 或 NAS 安装 cloudflared；
  4. 先只接入一个低风险测试服务；
  5. 从手机移动网络访问测试域名；
  6. 配置 Cloudflare Access；
  7. 再逐个添加 NAS、相册、Home Assistant 等服务；
  8. 最后决定是否迁移整个 animagi.top 的 DNS。

  不要一开始就把 NAS、路由器和媒体服务全部暴露。先使用一个测试服务确认 Tunnel、DNS、认证和回源都正常。