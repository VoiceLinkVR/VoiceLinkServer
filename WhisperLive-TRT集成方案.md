# WhisperLive-TRT-Gemma 闆嗘垚鏂规

## 涓€銆佽儗鏅?

褰撳墠 VoiceLinkServer1 浣跨敤 FasterWhisper 浣滀负璇煶璇嗗埆鍚庣锛岄€氳繃 OpenAI 鍏煎鐨?REST API 璋冪敤銆傜幇闇€闆嗘垚鑷爺鐨?WhisperLive-TRT-Gemma 鍚庣锛堝熀浜?TensorRT 鍔犻€熺殑 Whisper Large V3锛夛紝瀹炵幇涓や釜鍚庣鍙垏鎹€?

### 涓や釜鍚庣瀵规瘮

| 鐗规€?| FasterWhisper | WhisperLive-TRT-Gemma |
|------|--------------|----------------------|
| 鎺ㄧ悊妗嗘灦 | CTranslate2 | TensorRT-LLM |
| GPU鍒╃敤鐜?| 涓瓑 | 楂橈紙TensorRT浼樺寲锛?|
| API鍗忚 | REST (OpenAI鍏煎) | REST (OpenAI鍏煎) + WebSocket娴佸紡 |
| 绔彛 | 8000 | REST: 8000, WebSocket: 9090 |
| 骞跺彂鑳藉姏 | 杈冨ソ | 鍙楅檺锛堥粯璁ax_clients=4锛?|
| 寤惰繜 | 涓瓑 | 浣庯紙TensorRT鍔犻€燂級 |
| 娴佸紡鏀寔 | 鏃?| 鏈夛紙WebSocket瀹炴椂娴佸紡锛?|
| 缈昏瘧鑳藉姏 | 浠?`/translations` 缈昏瘧涓鸿嫳鏂?| `transcriptions` 鍐呯疆缈昏瘧锛堟娴嬫簮璇█锛岃瑷€涓嶄竴鑷存椂閫氳繃 Gemma 鑷姩缈昏瘧锛?|

---

## 浜屻€佸叧閿喅绛栵細REST API vs WebSocket 娴佸紡

### 鏂规A锛歊EST API锛堟帹鑽愶級

**鍘熺悊**锛歐hisperLive-TRT-Gemma 鎻愪緵 `POST /v1/audio/transcriptions` REST 绔偣锛屾敮鎸侀煶棰戜笂浼犺瘑鍒紝骞跺唴缃瑷€妫€娴?缈昏瘧鍔熻兘銆?

**浼樺娍**锛?
1. **鏀瑰姩鏈€灏?* - 浠呴渶淇敼閰嶇疆椤瑰拰灏戦噺璋冪敤浠ｇ爜
2. **鏋舵瀯绋冲畾** - 涓嶉渶瑕佸紩鍏?WebSocket 杩炴帴绠＄悊銆侀噸杩炴満鍒剁瓑澶嶆潅閫昏緫
3. **閫傞厤鐜版湁鍦烘櫙** - VoiceLinkServer1 鐨勫鎴风涓婁紶瀹屾暣闊抽鏂囦欢锛岄潪瀹炴椂娴佸紡鍦烘櫙
4. **鏃犻渶鏀瑰姩瀹㈡埛绔?* - VR绔笂浼犳柟寮忎笉鍙?
5. **缈昏瘧涓€浣撳寲** - TRT 鍚庣鐨?transcriptions 鎺ュ彛宸查泦鎴愮炕璇戯紝浼犲叆 `language` 涓庢娴嬭瑷€涓嶅悓鏃惰嚜鍔ㄧ炕璇戯紝鏃犻渶棰濆璋冪敤缈昏瘧鏈嶅姟

**鍔ｅ娍**锛?
- 鏃犳硶鍒╃敤娴佸紡鑳藉姏锛屽繀椤荤瓑鏁翠釜闊抽澶勭悊瀹屾瘯鎵嶈繑鍥?

### 鏂规B锛歐ebSocket 娴佸紡

**鍘熺悊**锛氶€氳繃 WebSocket 杩炴帴鍒版柊鍚庣鐨?9090 绔彛锛屽疄鏃舵帹閫侀煶棰戝抚锛屾帴鏀惰瘑鍒墖娈点€?

**浼樺娍**锛?
1. **瀹炴椂鍙嶉** - 璇嗗埆缁撴灉閫愭杩斿洖锛屽欢杩熸洿浣?
2. **閫傚悎闀块煶棰?* - 闀块煶棰戞棤闇€绛夊叏閮ㄥ鐞嗗畬

**鍔ｅ娍**锛?
1. **鏀瑰姩宸ㄥぇ** - 闇€瑕侀噸鏋勬暣涓煶棰戝鐞嗛摼璺細
   - 鏂板 WebSocket 瀹㈡埛绔鐞嗗櫒锛堣繛鎺ユ睜銆侀噸杩炪€佽秴鏃讹級
   - 闊抽鏍煎紡闇€杞负 float32 PCM 16kHz锛堝綋鍓嶄负鏂囦欢瀛楄妭娴侊級
   - 闇€瑕佸疄鐜板紓姝ユ秷鎭仛鍚堥€昏緫锛堢瓑寰呮墍鏈?segment completed锛?
   - API 璺敱灞傞渶鏀寔娴佸紡鍝嶅簲鎴栫瓑寰呰仛鍚堢粨鏋?
2. **骞跺彂鍙楅檺** - 鏂板悗绔?WebSocket 榛樿浠呮敮鎸?4 涓苟鍙戝鎴风
3. **澶嶆潅搴﹂珮** - 寮曞叆杩炴帴鐘舵€佺鐞嗐€佸績璺虫満鍒躲€佹柇绾块噸杩?
4. **鍦烘櫙涓嶅尮閰?* - VR瀹㈡埛绔笂浼犵殑鏄畬鏁撮煶棰戠墖娈碉紙閫氬父1-10绉掞級锛岄潪杩炵画娴佸紡璇煶

### 缁撹锛氶€夋嫨 REST API

**鏍稿績鍘熷洜**锛歏oiceLinkServer1 鐨勪娇鐢ㄥ満鏅槸 VR 瀹㈡埛绔綍鍒跺畬鏁磋闊崇墖娈靛悗涓婁紶璇嗗埆锛屽睘浜庡吀鍨嬬殑銆岃姹?鍝嶅簲銆嶆ā寮忥紝REST API 澶╃劧閫傞厤銆俉ebSocket 娴佸紡閫傚悎瀹炴椂浼氳杞綍銆佺洿鎾瓧骞曠瓑鎸佺画杈撳叆鍦烘櫙锛屼笌褰撳墠鏋舵瀯涓嶅尮閰嶃€?

鏀瑰姩鎴愭湰瀵规瘮锛歊EST 鏂规绾﹂渶鏀瑰姩 2 涓枃浠躲€佹柊澧炵害 30 琛屼唬鐮侊紱WebSocket 鏂规闇€鏀瑰姩 5+ 涓枃浠躲€佹柊澧?200+ 琛屼唬鐮佸苟寮曞叆鏂颁緷璧栥€?

> **鏈潵鎵╁睍**锛氬鏋滃悗缁?VR 瀹㈡埛绔渶瑕佸疄鏃舵祦寮忚瘑鍒紙杈硅杈硅瘑鍒級锛屽彲浠ュ湪 REST 鏂规鍩虹涓婇澶栨坊鍔?WebSocket 閫氶亾锛屼袱鑰呬笉鍐茬獊銆?

---

## 涓夈€乀RT 鍚庣 REST API 鎺ュ彛璇存槑

### 3.1 杞綍绔偣

```
POST /v1/audio/transcriptions
Content-Type: multipart/form-data
```

**璇锋眰鍙傛暟**锛?

| 鍙傛暟 | 绫诲瀷 | 蹇呭～ | 璇存槑 |
|------|------|------|------|
| file | File | 鏄?| 闊抽鏂囦欢锛圵AV/OPUS/MP3/FLAC绛夛級 |
| language | str | 鍚?| 鐩爣璇█浠ｇ爜锛岄粯璁?"en"銆傝嫢涓庢娴嬪埌鐨勬簮璇█涓嶅悓锛岃嚜鍔ㄨЕ鍙戠炕璇?|
| model | str | 鍚?| 妯″瀷鍚嶏紙淇℃伅鎬э紝TRT 蹇界暐姝ゅ€硷級 |
| response_format | str | 鍚?| "json"锛堥粯璁わ級鎴?"text" |

**鍝嶅簲鏍煎紡锛圝SON锛?*锛?

```json
// 鏃犵炕璇戯紙璇█涓€鑷达級
{
  "text": "璇嗗埆缁撴灉鏂囨湰",
  "source_language": "zh",
  "language": "zh",
  "translated": false,
  "target_language": "zh"
}

// 瑙﹀彂缈昏瘧锛堣瑷€涓嶄竴鑷达級
{
  "text": "translated result text",
  "original_text": "璇嗗埆缁撴灉鏂囨湰",
  "source_language": "zh",
  "language": "en",
  "translated": true,
  "target_language": "en"
}

// 缈昏瘧鏈嶅姟鏈厤缃椂锛堣繑鍥炴簮鏂囨湰 + 閿欒淇℃伅锛?
{
  "text": "璇嗗埆缁撴灉鏂囨湰",
  "source_language": "zh",
  "language": "en",
  "translated": false,
  "target_language": "en",
  "translation_error": "Translation service is not configured."
}
```

**鍏抽敭琛屼负**锛?
- `language` 鍙傛暟涓嶆敮鎸?`"auto"`锛屼細鍥為€€涓?`"en"`
- 褰?`language` 涓庨煶棰戞娴嬪埌鐨勬簮璇█涓€鑷存椂锛屼笉瑙﹀彂缈昏瘧
- 缈昏瘧渚濊禆 TRT 鍚庣渚ч厤缃殑 Gemma 缈昏瘧鏈嶅姟锛坄--translate_service_url`锛?
- 缈昏瘧澶辫触鏃朵笉鎶ラ敊锛岃繑鍥炴簮璇█鏂囨湰骞堕檮甯?`translation_error` 瀛楁

### 3.2 鍋ュ悍妫€鏌ョ鐐?

```
GET /healthz

鍝嶅簲锛?
{
  "status": "ok",
  "backend": "tensorrt",
  "translation_configured": true/false
}
```

---

## 鍥涖€佽缁嗗疄鐜版柟妗?

### 4.1 閰嶇疆鍙樻洿 (`src/core/config.py`)

鏂板閰嶇疆椤癸紝鏀寔鍚庣鍒囨崲锛?

```python
# Whisper 鍚庣閫夋嫨: "faster-whisper" 鎴?"whisper-trt"
WHISPER_BACKEND: str = "faster-whisper"

# WhisperLive-TRT 閰嶇疆锛堜粎 WHISPER_BACKEND=whisper-trt 鏃剁敓鏁堬級
WHISPER_TRT_HOST: str = "127.0.0.1"
WHISPER_TRT_PORT: int = 8000       # TRT 鍚庣鐨?REST API 绔彛
```

**鍒囨崲閫昏緫**锛?
- `WHISPER_BACKEND=faster-whisper` -> 浣跨敤鐜版湁 FasterWhisper + OpenAI SDK锛堥粯璁わ紝鍚戝悗鍏煎锛?
- `WHISPER_BACKEND=whisper-trt` -> 浣跨敤 WhisperLive-TRT-Gemma + httpx 鐩存帴璇锋眰

### 4.2 鏈嶅姟灞傚彉鏇?(`src/core/services.py`)

FasterWhisper 淇濈暀鐜版湁 OpenAI SDK 璋冪敤鏂瑰紡涓嶅彉銆俆RT 鍚庣浣跨敤 httpx 鐩存帴鍙戦€?HTTP 璇锋眰锛?

```python
import httpx

# TRT 鍚庣 URL
WHISPER_TRT_URL = f"http://{settings.WHISPER_TRT_HOST}:{settings.WHISPER_TRT_PORT}"

async def async_transcribe_trt(audio_file: bytes, language: str = 'zh') -> str:
    """浣跨敤 WhisperLive-TRT 鍚庣杩涜璇煶璇嗗埆锛堝惈鑷姩缈昏瘧锛?""
    async with httpx.AsyncClient(timeout=30.0) as client:
        files = {'file': ('audio.wav', audio_file, 'audio/wav')}
        data = {
            'language': language,
            'response_format': 'json',
        }
        response = await client.post(
            f"{WHISPER_TRT_URL}/v1/audio/transcriptions",
            files=files,
            data=data,
        )
        response.raise_for_status()
        result = response.json()
        return result.get('text', '')
```

### 4.3 璺敱灞傞€傞厤 (`src/routers/api.py`)

鍦ㄨ皟鐢ㄨ瘑鍒殑鍦版柟鏍规嵁鍚庣閰嶇疆鍒嗗彂锛?

```python
# 缁熶竴璋冨害鍑芥暟
async def do_transcribe(audio_file: bytes, language: str = 'zh') -> str:
    if settings.WHISPER_BACKEND == "whisper-trt":
        return await async_transcribe_trt(audio_file, language=language)
    else:
        return await async_transcribe(audio_file, language=language)

async def do_translate_audio(audio_file: bytes) -> str:
    """闊抽缈昏瘧涓鸿嫳鏂?""
    if settings.WHISPER_BACKEND == "whisper-trt":
        # TRT 鍚庣锛歵ranscriptions 鎺ュ彛浼犲叆 language="en"
        # 鑻ユ娴嬪埌鐨勬簮璇█涓嶆槸鑻辨枃锛屼細鑷姩缈昏瘧
        return await async_transcribe_trt(audio_file, language="en")
    else:
        return await async_translate_audio(audio_file)
```

**鍏抽敭浼樺娍**锛歍RT 鍚庣鏃犻渶鍗曠嫭鐨?`/v1/audio/translations` 绔偣銆俙transcriptions` 鎺ュ彛閫氳繃 `language` 鍙傛暟鍗冲彲瀹炵幇銆岃瘑鍒?缈昏瘧銆嶄竴浣撳寲锛?
- 浼犲叆 `language="zh"` -> 璇嗗埆鍚庤嫢婧愯瑷€涓嶆槸涓枃鍒欒嚜鍔ㄧ炕璇戜负涓枃
- 浼犲叆 `language="en"` -> 璇嗗埆鍚庤嫢婧愯瑷€涓嶆槸鑻辨枃鍒欒嚜鍔ㄧ炕璇戜负鑻辨枃
- 浼犲叆涓庢簮璇█鐩稿悓鐨?`language` -> 浠呰瘑鍒笉缈昏瘧

### 4.4 娉ㄦ剰浜嬮」

#### 4.4.1 鍝嶅簲鏍煎紡宸紓

TRT 鍚庣杩斿洖涓板瘜鐨?JSON 缁撴瀯锛堝惈 `source_language`, `translated`, `original_text` 绛夊瓧娈碉級锛屼絾褰撳墠浠ｇ爜浠呬娇鐢?`text` 瀛楁锛岄澶栧瓧娈佃嚜鍔ㄥ拷鐣ワ紝鏃犲吋瀹规€ч棶棰樸€?

**鍙€夊寮?*锛氭湭鏉ュ彲鍒╃敤 TRT 鍝嶅簲涓殑 `translated` 鍜?`original_text` 瀛楁锛屽湪 VoiceLinkServer 鐨勭炕璇戠浉鍏虫帴鍙ｄ腑鐪佸幓浜屾缈昏瘧璋冪敤锛堢洿鎺ュ湪璇嗗埆闃舵瀹屾垚缈昏瘧锛夛紝浠庤€屽噺灏戝 LibreTranslate 鐨勪緷璧栥€?

#### 4.4.2 language 鍙傛暟璇箟宸紓

| 鍚庣 | language 鍙傛暟鍚箟 |
|------|------------------|
| FasterWhisper | 鎸囧畾闊抽鐨?*婧愯瑷€**锛堝府鍔╄瘑鍒紩鎿庝紭鍖栵級 |
| WhisperLive-TRT | 鎸囧畾**鐩爣璇█**锛堣瘑鍒紩鎿庤嚜鍔ㄦ娴嬫簮璇█锛岃嫢涓嶄竴鑷村垯缈昏瘧锛?|

**褰卞搷**锛氬湪 FasterWhisper 妯″紡涓嬶紝`language="zh"` 琛ㄧず銆岄煶棰戞槸涓枃锛岃鎸変腑鏂囪瘑鍒€嶏紱鍦?TRT 妯″紡涓嬶紝`language="zh"` 琛ㄧず銆屼笉绠￠煶棰戞槸浠€涔堣瑷€锛屾渶缁堣緭鍑轰腑鏂囥€嶃€?

**澶勭悊鏂瑰紡**锛氬綋鍓?VoiceLinkServer 鐨勪娇鐢ㄥ満鏅腑锛屽鎴风宸茬煡婧愯瑷€骞朵紶鍏ュ搴旂殑 `language`锛屼袱绉嶈涔夊湪銆屾簮璇█=鐩爣璇█銆嶆椂琛屼负涓€鑷达紙閮芥槸姝ｅ父璇嗗埆锛夈€傚樊寮備粎鍦ㄧ炕璇戝満鏅腑浣撶幇锛岀敱 `do_translate_audio()` 缁熶竴澶勭悊銆?

#### 4.4.3 auto 璇█涓嶆敮鎸?

TRT 鍚庣鐨?REST API 涓嶆敮鎸?`language="auto"`锛屼細鍥為€€涓?`"en"`銆傝嫢 VoiceLinkServer 鏈変紶鍏?`auto` 鐨勫満鏅紝闇€鍦ㄨ皟鐢ㄥ墠鎷︽埅澶勭悊銆?

### 4.5 Docker 閮ㄧ讲閰嶇疆

鍦?`compose.yaml` 涓柊澧?TRT 鏈嶅姟閫夐」锛?

```yaml
# 鏂规1锛氭浛鎹?faster-whisper 鏈嶅姟
whisper-trt-server:
  image: whisper-live-trt:latest
  ports:
    - "8000:8000"    # REST API
    - "9090:9090"    # WebSocket锛堥鐣欙級
  environment:
    - ENABLE_REST=true
    - REST_PORT=8000
    - TRT_MULTILINGUAL=true
    - TRANSLATE_SERVICE_URL=http://gemma-server:8080  # Gemma 缈昏瘧鏈嶅姟
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]

VoiceLinkVR-server:
  environment:
    - WHISPER_BACKEND=whisper-trt
    - WHISPER_TRT_HOST=whisper-trt-server
    - WHISPER_TRT_PORT=8000
```

---

## 浜斻€佸彉鏇存枃浠舵竻鍗?

| 鏂囦欢 | 鍙樻洿鍐呭 | 鏀瑰姩閲?|
|------|---------|--------|
| `src/core/config.py` | 鏂板 `WHISPER_BACKEND`, `WHISPER_TRT_HOST`, `WHISPER_TRT_PORT` | ~4琛?|
| `src/core/services.py` | 鏂板 `async_transcribe_trt()` 鍑芥暟锛坔ttpx 鐩存帴璇锋眰锛?| ~15琛?|
| `src/routers/api.py` | 鏇挎崲璇嗗埆/缈昏瘧璋冪敤涓虹粺涓€璋冨害鍑芥暟 `do_transcribe()` / `do_translate_audio()` | ~15琛?|
| `compose.yaml` | 鏂板 whisper-trt-server 鏈嶅姟瀹氫箟 | ~15琛?|
| `.env.example` | 鏂板 TRT 鐩稿叧鐜鍙橀噺绀轰緥 | ~3琛?|

**鎬绘敼鍔ㄩ噺锛氱害 50 琛屼唬鐮侊紝鏃犻渶鏂板渚濊禆鍖?*锛坔ttpx 宸插湪椤圭洰渚濊禆涓級銆?

---

## 鍏€佹祴璇曢獙璇佽鍒?

### 6.1 鍩虹鍔熻兘娴嬭瘯

| 娴嬭瘯椤?| 楠岃瘉鍐呭 |
|--------|---------|
| 涓枃杞綍 | `/api/whisper/transcriptions` 浣跨敤 TRT 鍚庣锛屼紶鍏?`language=zh`锛屾甯歌瘑鍒腑鏂?|
| 鑻辨枃杞綍 | 鍚屼笂锛屼紶鍏?`language=en`锛岄獙璇佽嫳鏂?|
| 闊抽缈昏瘧涓鸿嫳鏂?| `/api/whisper/translations` 鍦?TRT 妯″紡涓嬮€氳繃 `transcriptions+language=en` 瀹炵幇缈昏瘧 |
| 璺ㄨ瑷€缈昏瘧 | 涓枃闊抽 + `language=en`锛岄獙璇?TRT 鍚庣鑷姩缈昏瘧涓鸿嫳鏂?|
| OPUS鏍煎紡 | OPUS 闊抽缁忚浆鎹㈠悗 TRT 鍚庣鍙甯稿鐞?|
| 澶氳瑷€杞綍 | `/api/whisper/multitranscription` 涓枃璧?SenseVoice锛屽叾浠栬蛋 TRT |

### 6.2 鍒囨崲娴嬭瘯

| 娴嬭瘯椤?| 楠岃瘉鍐呭 |
|--------|---------|
| 榛樿妯″紡 | `WHISPER_BACKEND` 涓嶈缃椂榛樿浣跨敤 FasterWhisper |
| 鍒囨崲鍒癟RT | 璁剧疆 `WHISPER_BACKEND=whisper-trt` 鍚庢湇鍔℃甯稿惎鍔?|
| 鍒囨崲鍥炴潵 | 鏀瑰洖 `faster-whisper` 鍚庢仮澶嶅師琛屼负 |

### 6.3 寮傚父娴嬭瘯

| 娴嬭瘯椤?| 楠岃瘉鍐呭 |
|--------|---------|
| TRT鏈嶅姟涓嶅彲鐢?| 璇锋眰杩斿洖鍚堢悊閿欒淇℃伅锛屼笉瀵艰嚧鏈嶅姟宕╂簝 |
| Gemma缈昏瘧鏈厤缃?| TRT 鍚庣鏃?Gemma 鏃讹紝璺ㄨ瑷€璇锋眰杩斿洖婧愯瑷€鏂囨湰 + `translation_error` |

---

## 涓冦€佹湭鏉ユ墿灞曡矾寰?

1. **WebSocket 娴佸紡閫氶亾锛堜紭鍏堢骇涓級** - 濡?VR 瀹㈡埛绔崌绾ф敮鎸佽竟璇磋竟浼狅紝鍙柊澧?`/ws/stream` 绔偣锛岄€氳繃 WebSocket 杞彂鍒?TRT 鍚庣鐨?9090 绔彛
2. **鍒╃敤 TRT 鍐呯疆缈昏瘧鏇夸唬 LibreTranslate锛堜紭鍏堢骇涓級** - TRT 鍚庣鐨?transcriptions 鎺ュ彛宸查泦鎴?Gemma 缈昏瘧锛屽彲鍦?`multitranslateToOtherLanguage` 绛夋帴鍙ｄ腑鐩存帴浼犲叆鐩爣 `language`锛岀渷鍘诲崟鐙皟鐢?LibreTranslate 鐨勬楠わ紝鍑忓皯鏈嶅姟渚濊禆鍜屽欢杩?
3. **鍒╃敤 TRT 鍝嶅簲鍏冩暟鎹紙浼樺厛绾т綆锛?* - 浣跨敤 `source_language` 瀛楁瀹炵幇鑷姩璇█妫€娴嬶紝浣跨敤 `original_text` + `text` 瀛楁鍚屾椂杩斿洖鍘熸枃鍜岃瘧鏂?
4. **璐熻浇鍧囪　锛堜紭鍏堢骇浣庯級** - 楂樺苟鍙戝満鏅笅閮ㄧ讲澶氫釜 TRT 瀹炰緥锛屽湪 VoiceLinkServer 灞傚仛杞
