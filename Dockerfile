# fin-intel-agent 镜像
FROM python:3.12-slim

# 时区:避免 loguru/调度器时间戳漂移(slim 镜像默认无 tzdata,需先安装)
ENV TZ=Asia/Shanghai
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# 国内环境可保留清华镜像加速
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

# 运行模式:
#   docker run 默认启动常驻调度(按 config.yaml 中 cron 定时执行);
#   想立即跑一轮验证,用:  docker run --rm fin-intel-agent python3 -m src.fin_intel --once --dry-run
# 配置外部挂载: -v /宿主机/config.yaml:/app/config/config.yaml  (以及 .env 到 /app/.env)
CMD ["python3", "-m", "src.fin_intel"]