FROM python:3.11-alpine

# 安装 iptables 和必要工具
RUN apk add --no-cache iptables ip6tables sqlite

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY src/ ./src/

# 创建数据目录
RUN mkdir -p /data

# 设置入口点
ENTRYPOINT ["python", "-m", "src.main"]
CMD ["daemon"]