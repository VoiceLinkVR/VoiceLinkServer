import httpx
import json
import os
import html
import traceback
import struct
import wave
from io import BytesIO
from openai import OpenAI
import opuslib
import emoji
from core.config import settings, WHISPER_URL, SENSEVOICE_URL, LOCAL_TRANS_URL, LOCAL_LANGUAGE_URL
from core.logging_config import logger

# 延迟导入translators，避免初始化时的网络问题
translators = None

def get_translators():
    global translators
    if translators is None:
        # 从环境变量获取地区设置，避免网络检查
        region = os.environ.get("translators_default_region", "CN")
        os.environ["translators_default_region"] = region
        import translators as _translators
        translators = _translators
    return translators

# --- 全局客户端和服务变量 ---
whisperclient = OpenAI(api_key=settings.WHISPER_APIKEY, base_url=WHISPER_URL)
glm_client = OpenAI(api_key="4c3d963619884fc69e3a02c581925691.mgFDmvFyfDWmGvub", base_url="https://open.bigmodel.cn/api/paas/v4/")
errorFilter = {}
# 硬编码的支持语言列表，不再动态从LibreTranslate获取
supportedLanguagesList = [
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en",
    "eo", "es", "et", "eu", "fa", "fi", "fr", "ga", "gl", "he",
    "hi", "hu", "id", "it", "ja", "ko", "lt", "lv", "ms", "nb",
    "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sq", "sv", "th",
    "tl", "tr", "uk", "ur", "zh", "zt"
]

# --- 语言代码 (从 server.py 复制) ---
whisperSupportedLanguageList = ["af","am","ar","as","az","ba","be","bg","bn","bo","br","bs","ca","cs","cy","da","de","el","en","es","et","eu","fa","fi","fo","fr","gl","gu","ha","haw","he","hi","hr","ht","hu","hy","id","is","it","ja","jw","ka","kk","km","kn","ko","la","lb","ln","lo","lt","lv","mg","mi","mk","ml","mn","mr","ms","mt","my","ne","nl","nn","no","oc","pa","pl","ps","pt","ro","ru","sa","sd","si","sk","sl","sn","so","sq","sr","su","sv","sw","ta","te","tg","th","tk","tl","tr","tt","uk","ur","uz","vi","yi","yo","yue","zh"]
codeTochinese = {'af':'阿非利堪斯语','am':'阿姆哈拉语','ar':'阿拉伯语','as':'阿萨姆语','az':'阿塞拜疆语','ba':'巴什基尔语','be':'白俄罗斯语','bg':'保加利亚语','bn':'孟加拉语','bo':'藏语','br':'布列塔尼语','bs':'波斯尼亚语','ca':'加泰罗尼亚语','cs':'捷克语','cy':'威尔士语','da':'丹麦语','de':'德语','el':'希腊语','en':'英语','es':'西班牙语','et':'爱沙尼亚语','eu':'巴斯克语','fa':'波斯语','fi':'芬兰语','fo':'法罗语','fr':'法语','gl':'加利西亚语','gu':'古吉拉特语','ha':'豪萨语','haw':'夏威夷语','he':'希伯来语','hi':'印地语','hr':'克罗地亚语','ht':'海地克里奥尔语','hu':'匈牙利语','hy':'亚美尼亚语','id':'印尼语','is':'冰岛语','it':'意大利语','ja':'日语','jw':'爪哇语','ka':'格鲁吉亚语','kk':'哈萨克语','km':'高棉语','kn':'卡纳达语','ko':'韩语','la':'拉丁语','lb':'卢森堡语','ln':'林加拉语','lo':'老挝语','lt':'立陶宛语','lv':'拉脱维亚语','mg':'马达加斯加语','mi':'毛利语','mk':'马其顿语','ml':'马拉雅拉姆语','mn':'蒙古语','mr':'马拉地语','ms':'马来语','mt':'马耳他语','my':'缅甸语','ne':'尼泊尔语','nl':'荷兰语','nn':'新挪威语','no':'挪威语','oc':'奥克语','pa':'旁遮普语','pl':'波兰语','ps':'普什图语','pt':'葡萄牙语','ro':'罗马尼亚语','ru':'俄语','sa':'梵语','sd':'信德语','si':'僧伽罗语','sk':'斯洛伐克语','sl':'斯洛文尼亚语','sn':'修纳语','so':'索马里语','sq':'阿尔巴尼亚语','sr':'塞尔维亚语','su':'巽他语','sv':'瑞典语','sw':'斯瓦希里语','ta':'泰米尔语','te':'泰卢固语','tg':'塔吉克语','th':'泰语','tk':'土库曼语','tl':'他加禄语','tr':'土耳其语','tt':'鞑靼语','uk':'乌克兰语','ur':'乌尔都语','uz':'乌兹别克语','vi':'越南语','yi':'意第绪语','yo':'约鲁巴语','yue':'粤语','zh':'简体中文','zt':'繁体中文','eo':'世界语','ga':'爱尔兰语','nb':'挪威语'}
transalte_zt = {'bing':'zh-Hant','baidu':'cht','alibaba':'zh-TW','sogou':'zh-CHT','iciba':'cnt','itranslate':'zh-TW','papago':'zh-TW'}


# --- 初始化函数 ---
def load_filter_config():
    global errorFilter
    filter_path = os.path.join(os.path.dirname(__file__), '..', 'filter.json')
    try:
        if settings.FILTER_WEB_URL:
            with httpx.Client() as client:
                response = client.get(settings.FILTER_WEB_URL)
                response.raise_for_status()
                errorFilter = response.json()
                logger.info("Successfully loaded filter config from web.")
        else:
            raise Exception("FILTER_WEB_URL not set")
    except Exception as e:
        logger.warning(f"Failed to load filter from web, falling back to local file. Error: {e}")
        with open(filter_path, 'r', encoding='utf-8') as f:
            errorFilter = json.load(f)
            logger.info("Loaded filter config from local file.")

def init_supported_languages():
    """
    初始化支持语言列表 - 现在使用硬编码的固定列表，不再动态获取
    原有server.py中是从LibreTranslate动态获取，现在改为固定列表以提高稳定性
    """
    global supportedLanguagesList
    # supportedLanguagesList 现在已经在模块级别硬编码初始化
    # 这个函数保留用于向后兼容，只记录日志
    logger.info(f"[INIT] 使用硬编码的支持语言列表，包含 {len(supportedLanguagesList)} 种语言: {supportedLanguagesList}")

# --- 业务逻辑函数 ---

def do_translate(text: str, from_: str, to: str):
    tol = transalte_zt.get(settings.TRANSLATOR_SERVICE, 'zt') if to == 'zt' else to
    translators_module = get_translators()
    return html.unescape(translators_module.translate_text(text, translator=settings.TRANSLATOR_SERVICE, from_language=from_, to_language=tol))

async def translate_local(text: str, source: str, target: str) -> str:
    params = {
        "q": text, "source": source, "target": target, "format": "text",
        "api_key": settings.LIBRETRANSLATE_APIKEY
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(LOCAL_TRANS_URL, params=params)
            response.raise_for_status()
            result = response.json()
            return result.get('translatedText', text)
        except Exception as e:
            logger.error(f"Local translation failed: {e}")
            return text

def packaged_opus_stream_to_wav_bytes(packaged_opus_data: bytes, sample_rate: int) -> bytes:
    # (此函数逻辑复杂且纯粹，直接从 server.py 复制)
    channels = 1
    frame_duration = 20
    sample_width = 2
    if not packaged_opus_data: return b''
    try:
        decoder = opuslib.Decoder(sample_rate, channels)
    except opuslib.OpusError as e:
        raise RuntimeError(f"创建 Opus 解码器失败: {e}")
    samples_per_packet_frame = int(sample_rate * frame_duration / 1000)
    all_decoded_pcm = bytearray()
    offset = 0
    while offset < len(packaged_opus_data):
        if offset + 4 > len(packaged_opus_data): break
        try:
            packet_len = struct.unpack('>I', packaged_opus_data[offset:offset+4])[0]
        except struct.error: break
        offset += 4
        if offset + packet_len > len(packaged_opus_data): break
        opus_packet = packaged_opus_data[offset:offset+packet_len]
        offset += packet_len
        if not opus_packet: continue
        try:
            decoded_pcm = decoder.decode(opus_packet, samples_per_packet_frame)
            all_decoded_pcm.extend(decoded_pcm)
        except opuslib.OpusError as e:
            logger.warning(f"Opus 解码错误 (跳过包): {e}")
    if not all_decoded_pcm: return b''
    wav_buffer = BytesIO()
    with wave.open(wav_buffer, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(all_decoded_pcm))
    return wav_buffer.getvalue()