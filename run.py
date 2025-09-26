import uvicorn
import os
import sys

# 添加src目录到Python路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

if __name__ == "__main__":
    # Waitress 的参数可以映射到 Uvicorn
    threads_num = os.getenv("THREADS_NUM")
    workers = int(threads_num) if threads_num else None

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8980,
        workers=workers,
        reload=True  # 在开发时使用
    )