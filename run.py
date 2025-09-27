# run.py

import uvicorn
import os
import sys
import logging

# 配置日志，以便在启动时就能看到信息
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 添加src目录到Python路径，确保main:app可以被找到
# (注意：在Dockerfile中，工作目录已经是 /usr/src/app/src，所以这行在Docker中不是必须的，
# 但为了本地运行的兼容性，保留它是个好习惯)
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

if __name__ == "__main__":
    # 从环境变量 'UVICORN_WORKERS' 中获取 worker 数量
    # os.getenv 的第二个参数是默认值，如果环境变量未设置，则使用它
    workers_env = os.getenv("UVICORN_WORKERS", "4")
    
    try:
        # 尝试将环境变量转换为整数
        workers_count = int(workers_env)
        if workers_count <= 0:
            logging.warning(f"Worker count must be a positive integer, but got {workers_count}. Defaulting to 1.")
            workers_count = 1
    except (ValueError, TypeError):
        logging.error(f"Invalid value for UVICORN_WORKERS: '{workers_env}'. Must be an integer. Defaulting to 4.")
        workers_count = 4

    # 检查是否处于开发模式（通过 RELOAD 环境变量控制）
    # 这让您可以在开发时开启热重载，在生产环境中关闭
    reload_env = os.getenv("UVICORN_RELOAD", "False").lower()
    enable_reload = reload_env in ["true", "1", "t"]

    if enable_reload and workers_count > 1:
        logging.warning("Reload mode is enabled, which is incompatible with multiple workers. Forcing workers to 1.")
        workers_count = 1
        
    logging.info(f"Starting Uvicorn server...")
    logging.info(f"Host: 0.0.0.0, Port: 8980")
    logging.info(f"Workers: {workers_count}")
    logging.info(f"Reload mode: {enable_reload}")

    # 以编程方式启动 uvicorn
    # reload=False 是生产环境的最佳实践
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8980,
        workers=workers_count,
        reload=enable_reload
    )