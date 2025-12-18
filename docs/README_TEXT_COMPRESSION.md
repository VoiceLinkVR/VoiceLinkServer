# 文本重复字符压缩功能

## 功能简介
VoiceLinkVR Server 新增了文本重复字符压缩功能，可以将语音识别结果中的连续重复字符压缩成"字符*数量"的格式，减少文本冗余并提高可读性。

## 压缩规则
- 只压缩连续重复5次及以上的字符
- 压缩格式为：字符*数量（如：啊*5）
- 区分大小写
- 支持所有Unicode字符
- 可配置是否启用及最小重复次数

## 配置选项
在配置文件（config.py）中添加以下配置项：

```python
ENABLE_TEXT_COMPRESSION: bool = True      # 是否启用文本重复字符压缩
TEXT_COMPRESSION_MIN_REPEAT: int = 5     # 文本压缩最小重复次数（默认5次）
```

也可以通过环境变量设置：
```bash
export ENABLE_TEXT_COMPRESSION=true
export TEXT_COMPRESSION_MIN_REPEAT=5
```

## 使用示例

### 压缩效果示例
```
输入: "你好啊啊啊啊啊"
输出: "你好啊*5"

输入: "哈哈哈哈哈哈哈哈"
输出: "哈*8"

输入: "不不不不知道"      # 4次重复，不压缩
输出: "不不不不知道"

输入: "嗯嗯嗯嗯嗯"        # 5次重复，压缩
输出: "嗯*5"
```

### API集成
该功能已集成到以下API端点：

1. `/api/whisper/transcriptions` - Whisper语音识别
2. `/api/whisper/translations` - Whisper语音翻译
3. `/api/func/translateToEnglish` - 翻译到英文
4. `/api/func/translateToOtherLanguage` - 翻译到其他语言
5. `/api/func/multitranslateToOtherLanguage` - 多语言翻译

### 日志记录
当文本被压缩时，系统会记录日志：
```
[MULTITRANSLATE] 压缩重复字符 - 原始: '你好啊啊啊啊啊' -> 处理后: '你好啊*5'
```

## 性能说明
- 使用正则表达式实现，性能优异
- 平均处理时间约0.003毫秒/次
- 支持批量处理
- 预编译正则表达式以提高性能

## 测试方法
运行独立测试脚本验证压缩逻辑：
```bash
python test_compression_standalone.py
```

## 注意事项
1. 该功能默认启用，如需关闭可设置 `ENABLE_TEXT_COMPRESSION=false`
2. 最小重复次数可根据需求调整（建议保持5次）
3. 压缩后的文本长度会明显减少，特别是在重复字符较多的情况下
4. 该功能只影响返回给客户端的结果，不影响原始识别数据

## 更新记录
- 2025-10-06: 新增文本重复字符压缩功能
- 采用正则表达式方案，性能优于遍历算法1.5-2倍