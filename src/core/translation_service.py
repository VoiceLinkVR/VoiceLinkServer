import json
import html
from typing import Dict, Any, Optional, List
from openai import OpenAI
import logging

from core.config import settings
from core.services import codeTochinese, logger

# OpenAI客户端配置
# 注意：这里需要配置正确的API密钥
llm_client = OpenAI(
    api_key="your-api-key-here",  # 应该从配置中获取
    base_url="https://open.bigmodel.cn/api/paas/v4/"
)

class TranslationService:
    def __init__(self):
        self.client = llm_client

    def openai_translate(self, text: str, target_lang1: str, target_lang2: str = 'none', target_lang3: str = 'none') -> Dict[str, Any]:
        """使用OpenAI进行多语言翻译"""
        try:
            c1 = f'2. 将收到的文字翻译成{codeTochinese[target_lang2]}。' if target_lang2 != "none" else ''
            c2 = f'3. 将收到的文字翻译成{codeTochinese[target_lang3]}。' if target_lang3 != "none" else ''
            o1 = f',"translatedText2":{codeTochinese[target_lang2]}译文' if target_lang2 != "none" else ''
            o2 = f',"translatedText3":{codeTochinese[target_lang3]}译文' if target_lang3 != "none" else ''

            content_text = f'''你是翻译助手。你的任务是：
1. 将收到的文字翻译成{codeTochinese[target_lang1]}。
{c1}
{c2}
请严格按照如下格式仅输出JSON，不要输出Python代码或其他信息，JSON字段使用顿号【、】区隔：''' + '{' + f'"text":收到的文字,"translatedText":{codeTochinese[target_lang1]}译文{o1}{o2}' + '}'

            logger.info(f"LLM send: {content_text}")

            completion = self.client.chat.completions.create(
                model="glm-4-flash-250414",  # 这个模型可能需要更新
                messages=[
                    {"role": "system", "content": content_text},
                    {"role": "user", "content": text}
                ],
                temperature=0.3,
                max_tokens=2000
            )

            message_content = completion.choices[0].message.content
            logger.info(f"LLM response: {message_content}")

            # 解析响应
            remaining = message_content
            result = {}

            try:
                data = json.loads(remaining)
                result = {
                    'text': data.get('text', ''),
                    'translatedText': data.get('translatedText', ''),
                    'translatedText2': data.get('translatedText2', '') if target_lang2 != 'none' else '',
                    'translatedText3': data.get('translatedText3', '') if target_lang3 != 'none' else ''
                }
            except json.JSONDecodeError:
                # 如果JSON解析失败，尝试提取关键信息
                logger.warning("Failed to parse JSON from LLM response, using fallback parsing")
                result = {
                    'text': text,
                    'translatedText': remaining,  # 返回原文和完整响应
                    'translatedText2': '',
                    'translatedText3': ''
                }

            return result

        except Exception as e:
            logger.error(f"OpenAI translation error: {e}")
            return {
                'error': 'Translation failed',
                'details': str(e),
                'text': text,
                'translatedText': ''
            }

    def multitranslate_with_prompt(self, text: str, source_lang: str, target_lang: str,
                                 target_lang2: str = 'none', target_lang3: str = 'none') -> Dict[str, Any]:
        """使用系统提示进行多语言翻译"""
        try:
            # 构建分隔符
            seps = [codeTochinese[source_lang] + ':', codeTochinese[target_lang] + ':']

            # 构建系统提示
            system_prompt_content = f"""你是一个高级的语音处理助手。你的任务是：
1.首先将音频内容转录成其原始语言的文本。
2. 将转录的文本翻译成{codeTochinese[source_lang]}。
3. 将转录的文本翻译成{codeTochinese[target_lang]}。
"""

            if target_lang2 != 'none':
                seps.append(codeTochinese[target_lang2] + ':')
                system_prompt_content += f"4. 将转录的文本翻译成{codeTochinese[target_lang2]}\n"

            if target_lang3 != 'none':
                seps.append(codeTochinese[target_lang3] + ':')
                system_prompt_content += f"5. 将转录的文本翻译成{codeTochinese[target_lang3]}\n"

            system_prompt_content += "请按照以下格式清晰地组织你的输出：\n"
            system_prompt_content += '{"原文":"原始语言文本",'
            system_prompt_content += f'"{codeTochinese[source_lang]}":"{codeTochinese[source_lang]}文本",'
            system_prompt_content += f'"{codeTochinese[target_lang]}":"{codeTochinese[target_lang]}文本"'

            if target_lang2 != 'none':
                system_prompt_content += f',"{codeTochinese[target_lang2]}":"{codeTochinese[target_lang2]}文本"'
            if target_lang3 != 'none':
                system_prompt_content += f',"{codeTochinese[target_lang3]}":"{codeTochinese[target_lang3]}文本"'

            system_prompt_content += '}\n'

            logger.info(f"Target language: {target_lang}, Source language: {source_lang}")

            completion = self.client.chat.completions.create(
                model="glm-4-flash-250414",
                messages=[
                    {"role": "system", "content": system_prompt_content},
                    {"role": "user", "content": text}
                ],
                temperature=0.3,
                max_tokens=2000
            )

            message_content = completion.choices[0].message.content
            logger.info(message_content)

            result = {}
            try:
                data = json.loads(message_content)
                result = {
                    'text': data.get(codeTochinese[source_lang], ''),
                    'translatedText': data.get(codeTochinese[target_lang], ''),
                    'translatedText2': data.get(codeTochinese[target_lang2], '') if target_lang2 != 'none' else '',
                    'translatedText3': data.get(codeTochinese[target_lang3], '') if target_lang3 != 'none' else ''
                }
            except json.JSONDecodeError:
                logger.warning("Failed to parse JSON from LLM response")
                result = {
                    'text': text,
                    'translatedText': message_content,
                    'translatedText2': '',
                    'translatedText3': ''
                }

            return result

        except Exception as e:
            logger.error(f"Multitranslate with prompt error: {e}")
            return {
                'error': 'Translation failed',
                'details': str(e),
                'text': text,
                'translatedText': ''
            }

# 创建翻译服务实例
translation_service = TranslationService()