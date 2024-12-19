from waitress import serve
from logging.config import dictConfig
from datetime import date
import os
import json
today = date.today()
formatted_today = today.strftime('%Y-%m-%d')
checkdirList=['data','data/db','data/filterConfig','data/logs']
checkfileList=[
    {
        "source":'filter.json',
        "target":'data/filterConfig/filter.json'
    }

]
for t_path in checkdirList : 
    if not os.path.exists(t_path):os.mkdir(t_path)
for checkfile in checkfileList : 
    if not os.path.exists(checkfile["target"]):
        with open(checkfile["source"], 'rb') as src ,open(checkfile["target"], 'wb') as dest:
            dest.write(src.read())

dictConfig({
        "version": 1,
        "disable_existing_loggers": False,  # 不覆盖默认配置
        "formatters": {  # 日志输出样式
            "default": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",  # 控制台输出
                "level": "INFO",
                "formatter": "default",
            },
            "log_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "INFO",
                "formatter": "default",   # 日志输出样式对应formatters
                "filename": f"./data/logs/server.log",  # 指定log文件目录
                "maxBytes": 20*1024*1024,   # 文件最大20M
                "backupCount": 30,          # 最多30个文件
                "encoding": "utf8",         # 文件编码
            },

        },
        "root": {
            "level": "INFO",  # # handler中的level会覆盖掉这里的level
            "handlers": ["log_file","console"],
        },
    }
)

from server import app
serve(app, host='0.0.0.0', port=8980)