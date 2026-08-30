# fin-intel-agent 镜像
FROM python:3.12-slim

# 时区:避免 loguru/调度器时间戳漂移
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

COPY requirements.txt .
# 国内环境可保留清华镜像加速
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

# 常驻调度模式;webhook 配置外部挂载 /app/config/config.yaml
CMD ["python3", "-m", "src.fin_intel"]