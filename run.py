import uvicorn
import os
import sys

# 添加src目录到Python路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

if __name__ == "__main__":
    threads_num = os.getenv("THREADS_NUM")
    workers = int(threads_num) if threads_num and int(threads_num) > 0 else 1

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8980,
        workers=workers,
        reload=True  # 建议在生产环境中设为 False
    )