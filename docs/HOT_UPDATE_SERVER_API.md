# VoiceLinkServer1 热更新接口说明（修订）

本次修订重点：

- **翻译模块更新的主体是翻译运行时（PyInstaller 打包的 translator.exe）**，不是翻译配置本身。
- 翻译“配置”只用于下发：
  - 支持的翻译引擎类型
  - 每个引擎支持的语言集合（来自 translators 库）

## 已提供接口

- 应用更新：`GET /api/updates/check?version={currentVersion}&platform=windows`
- 模型目录：`GET /api/models/list`
- 翻译运行时更新：`GET /api/translators/runtime?version={runtimeVersion}`
- 翻译能力矩阵：`GET /api/translations/capabilities`
- 兼容聚合接口：`GET /api/translations/profile?version={profileVersion}&runtimeVersion={runtimeVersion}`
  - `profile` 中包含 `translationCapabilities` + `translatorRuntime`

兼容旧接口：

- `GET /api/latestVersionInfo`

---

## 路径与文件

默认目录：

- 静态资源根：`src/data/update/files`
- 应用更新 manifest：`src/data/update/update_manifest.json`
- 模型 manifest：`src/data/update/models_manifest.json`
- 翻译能力 profile manifest：`src/data/update/translation_profile_manifest.json`
- 翻译运行时 manifest：`src/data/update/translator_runtime_manifest.json`

静态访问前缀：

- `/static/...`

例如：

- `src/data/update/files/translator-runtime/translator.exe`
- 对应 URL：`/static/translator-runtime/translator.exe`

---

## manifest 格式

## 1) translator_runtime_manifest.json（重点）

```json
{
  "version": "1.0.0",
  "downloadUrl": "translator-runtime/translator.exe",
  "checksum": "sha256:...",
  "fileSize": 12345678,
  "notes": "translator runtime build 1.0.0"
}
```

## 2) translation_profile_manifest.json（仅能力信息）

```json
{
  "version": "2026.02.09",
  "downloadUrl": "",
  "checksum": "",
  "profile": {
    "translationCapabilities": {
      "version": "20260209",
      "engines": [
        {
          "engine": "google",
          "languages": {
            "en": ["zh", "ja"],
            "zh": ["en", "ja"]
          }
        }
      ]
    }
  }
}
```

> 注：`/api/translations/capabilities` 会直接从 translators 库生成引擎与语言映射，并带缓存。

---

## 新增环境变量

- `TRANSLATOR_RUNTIME_MANIFEST_PATH`：翻译运行时 manifest 路径
- `TRANSLATION_CAPABILITIES_CACHE_SECONDS`：能力矩阵缓存秒数（默认 1800）
- `TRANSLATION_CAPABILITY_TIMEOUT`：单引擎语言探测超时秒数（默认 4.0）

已有相关变量：

- `UPDATE_PUBLIC_BASE_URL`
- `UPDATE_STATIC_ROOT`
- `UPDATE_MANIFEST_PATH`
- `MODEL_MANIFEST_PATH`
- `TRANSLATION_PROFILE_MANIFEST_PATH`

---

## 客户端对接建议

1. 用 `/api/translators/runtime` 检查并更新 translator.exe。
2. 用 `/api/translations/capabilities` 更新“可选引擎 + 引擎可用语言”UI。
3. `/api/translations/profile` 仅保留兼容，不建议再承载翻译 exe 下载的唯一入口。
