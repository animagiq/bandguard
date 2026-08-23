FROM python:3.11-alpine

# 安装 iptables、iproute2 和必要工具
RUN apk add --no-cache iptables ip6tables sqlite iproute2

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY src/ ./src/

# 创建数据目录
RUN mkdir -p /data

# 创建 CLI 别名脚本
RUN echo '#!/bin/sh\npython -m src.main "$@"' > /usr/local/bin/traffic-ctl && \
    chmod +x /usr/local/bin/traffic-ctl

# 设置入口点
ENTRYPOINT ["python", "-m", "src.main"]
CMD ["daemon"]