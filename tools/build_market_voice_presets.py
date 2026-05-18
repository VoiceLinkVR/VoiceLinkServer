from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


LANGUAGES = {
    "中文": ("zh", "中文"),
    "日语": ("ja", "日语"),
    "英语": ("en", "英语"),
    "韩语": ("ko", "韩语"),
    "粤语": ("yue", "粤语"),
}


EMOTION_PRIORITY = ("中立", "开心", "高兴", "喜悦", "其他", "难过", "生气", "吃惊", "恐惧", "厌恶")


@dataclass(frozen=True)
class PresetTarget:
    game_dir: str
    game_name: str
    character: str
    preset_id: str
    aliases: tuple[str, ...] = ()


TARGETS: tuple[PresetTarget, ...] = (
    # Honkai: Star Rail
    PresetTarget("星穹铁道", "星穹铁道", "流萤", "hsr-firefly"),
    PresetTarget("星穹铁道", "星穹铁道", "卡芙卡", "hsr-kafka", ("卡夫卡",)),
    PresetTarget("星穹铁道", "星穹铁道", "花火", "hsr-sparkle", ("火花",)),
    PresetTarget("星穹铁道", "星穹铁道", "银狼", "hsr-silver-wolf"),
    PresetTarget("星穹铁道", "星穹铁道", "三月七", "hsr-march-7th"),
    PresetTarget("星穹铁道", "星穹铁道", "丹恒", "hsr-dan-heng"),
    PresetTarget("星穹铁道", "星穹铁道", "景元", "hsr-jing-yuan"),
    PresetTarget("星穹铁道", "星穹铁道", "刃", "hsr-blade"),
    PresetTarget("星穹铁道", "星穹铁道", "砂金", "hsr-aventurine"),
    PresetTarget("星穹铁道", "星穹铁道", "知更鸟", "hsr-robin"),
    PresetTarget("星穹铁道", "星穹铁道", "星期日", "hsr-sunday"),
    PresetTarget("星穹铁道", "星穹铁道", "飞霄", "hsr-feixiao"),
    PresetTarget("星穹铁道", "星穹铁道", "镜流", "hsr-jingliu"),
    PresetTarget("星穹铁道", "星穹铁道", "希儿", "hsr-seele"),
    PresetTarget("星穹铁道", "星穹铁道", "符玄", "hsr-fu-xuan"),
    PresetTarget("星穹铁道", "星穹铁道", "阮•梅", "hsr-ruan-mei", ("阮梅",)),
    PresetTarget("星穹铁道", "星穹铁道", "藿藿", "hsr-huohuo"),
    PresetTarget("星穹铁道", "星穹铁道", "开拓者(男)", "hsr-trailblazer-male", ("开拓者（男）",)),
    PresetTarget("星穹铁道", "星穹铁道", "开拓者(女)", "hsr-trailblazer-female", ("开拓者（女）",)),
    # Genshin Impact
    PresetTarget("原神", "原神", "枫原万叶", "genshin-kazuha"),
    PresetTarget("原神", "原神", "胡桃", "genshin-hu-tao"),
    PresetTarget("原神", "原神", "钟离", "genshin-zhongli"),
    PresetTarget("原神", "原神", "芙宁娜", "genshin-furina"),
    PresetTarget("原神", "原神", "纳西妲", "genshin-nahida"),
    PresetTarget("原神", "原神", "流浪者", "genshin-wanderer"),
    PresetTarget("原神", "原神", "玛薇卡", "genshin-mavuika"),
    PresetTarget("原神", "原神", "那维莱特", "genshin-neuvillette"),
    PresetTarget("原神", "原神", "神里绫华", "genshin-ayaka"),
    PresetTarget("原神", "原神", "雷电将军", "genshin-raiden-shogun", ("雷电将军",)),
    PresetTarget("原神", "原神", "魈", "genshin-xiao"),
    PresetTarget("原神", "原神", "娜维娅", "genshin-navia"),
    PresetTarget("原神", "原神", "希诺宁", "genshin-xilonen"),
    PresetTarget("原神", "原神", "茜特菈莉", "genshin-citlali"),
    PresetTarget("原神", "原神", "珊瑚宫心海", "genshin-kokomi"),
    PresetTarget("原神", "原神", "闲云", "genshin-xianyun"),
    PresetTarget("原神", "原神", "恰斯卡", "genshin-chasca"),
    PresetTarget("原神", "原神", "甘雨", "genshin-ganyu"),
    PresetTarget("原神", "原神", "夜兰", "genshin-yelan"),
    PresetTarget("原神", "原神", "温迪", "genshin-venti"),
    PresetTarget("原神", "原神", "八重神子", "genshin-yae-miko"),
    PresetTarget("原神", "原神", "可莉", "genshin-klee"),
    # Honkai Impact 3rd
    PresetTarget("崩坏三", "崩坏三", "爱莉希雅", "hi3-elysia"),
    PresetTarget("崩坏三", "崩坏三", "琪亚娜", "hi3-kiana"),
    PresetTarget("崩坏三", "崩坏三", "芽衣", "hi3-mei"),
    PresetTarget("崩坏三", "崩坏三", "布洛妮娅", "hi3-bronya"),
    PresetTarget("崩坏三", "崩坏三", "希儿", "hi3-seele"),
    PresetTarget("崩坏三", "崩坏三", "德丽莎", "hi3-theresa"),
    PresetTarget("崩坏三", "崩坏三", "符华", "hi3-fu-hua"),
    PresetTarget("崩坏三", "崩坏三", "格蕾修", "hi3-griseo"),
    PresetTarget("崩坏三", "崩坏三", "姬子", "hi3-himeko"),
    PresetTarget("崩坏三", "崩坏三", "帕朵菲莉丝", "hi3-pardofelis"),
    PresetTarget("崩坏三", "崩坏三", "西琳", "hi3-sirin"),
    PresetTarget("崩坏三", "崩坏三", "幽兰黛尔", "hi3-durandal"),
    PresetTarget("崩坏三", "崩坏三", "丽塔", "hi3-rita"),
    PresetTarget("崩坏三", "崩坏三", "维尔薇", "hi3-vill-v"),
    PresetTarget("崩坏三", "崩坏三", "李素裳", "hi3-li-sushang"),
    PresetTarget("崩坏三", "崩坏三", "八重樱", "hi3-yae-sakura"),
    # Arknights
    PresetTarget("明日方舟", "明日方舟", "拉普兰德", "arknights-lappland"),
    PresetTarget("明日方舟", "明日方舟", "德克萨斯", "arknights-texas"),
    PresetTarget("明日方舟", "明日方舟", "缄默德克萨斯", "arknights-texas-the-omertosa"),
    PresetTarget("明日方舟", "明日方舟", "能天使", "arknights-exusiai"),
    PresetTarget("明日方舟", "明日方舟", "W", "arknights-w"),
    PresetTarget("明日方舟", "明日方舟", "史尔特尔", "arknights-surtr"),
    PresetTarget("明日方舟", "明日方舟", "斯卡蒂", "arknights-skadi"),
    PresetTarget("明日方舟", "明日方舟", "浊心斯卡蒂", "arknights-skadi-the-corrupting-heart"),
    PresetTarget("明日方舟", "明日方舟", "凯尔希", "arknights-kaltsit"),
    PresetTarget("明日方舟", "明日方舟", "艾雅法拉", "arknights-eyjafjalla"),
    PresetTarget("明日方舟", "明日方舟", "令", "arknights-ling"),
    PresetTarget("明日方舟", "明日方舟", "年", "arknights-nian"),
    PresetTarget("明日方舟", "明日方舟", "玛恩纳", "arknights-mlynar"),
    PresetTarget("明日方舟", "明日方舟", "缪尔赛思", "arknights-muelsyse"),
    PresetTarget("明日方舟", "明日方舟", "水月", "arknights-mizuki"),
    PresetTarget("明日方舟", "明日方舟", "银灰", "arknights-silverash"),
    PresetTarget("明日方舟", "明日方舟", "伊内丝", "arknights-ines"),
    PresetTarget("明日方舟", "明日方舟", "逻各斯", "arknights-logos"),
    PresetTarget("明日方舟", "明日方舟", "提丰", "arknights-typhon"),
    PresetTarget("明日方舟", "明日方舟", "泥岩", "arknights-mudrock"),
    PresetTarget("明日方舟", "明日方舟", "煌", "arknights-blaze"),
    PresetTarget("明日方舟", "明日方舟", "塞雷娅", "arknights-saria"),
    PresetTarget("明日方舟", "明日方舟", "夜莺", "arknights-nightingale"),
    # Zenless Zone Zero
    PresetTarget("绝区零", "绝区零", "妮可", "zzz-nicole"),
    PresetTarget("绝区零", "绝区零", "凯撒", "zzz-caesar"),
    PresetTarget("绝区零", "绝区零", "伊芙琳", "zzz-evelyn"),
    PresetTarget("绝区零", "绝区零", "丽娜", "zzz-rina"),
    PresetTarget("绝区零", "绝区零", "可琳", "zzz-corin"),
    PresetTarget("绝区零", "绝区零", "安东", "zzz-anton"),
    PresetTarget("绝区零", "绝区零", "「11号」", "zzz-soldier-11", ("11号",)),
    PresetTarget("绝区零", "绝区零", "「扳机」", "zzz-trigger", ("扳机",)),
    # Goddess of Victory: NIKKE
    PresetTarget("妮姬", "妮姬", "小红帽", "nikke-red-hood"),
    PresetTarget("妮姬", "妮姬", "红莲", "nikke-scarlet"),
    PresetTarget("妮姬", "妮姬", "神罚", "nikke-modernia"),
    PresetTarget("妮姬", "妮姬", "皇冠", "nikke-crown"),
    PresetTarget("妮姬", "妮姬", "丽塔", "nikke-liter"),
    PresetTarget("妮姬", "妮姬", "爱丽丝", "nikke-alice"),
    PresetTarget("妮姬", "妮姬", "桃乐丝", "nikke-dorothy"),
    PresetTarget("妮姬", "妮姬", "白雪公主", "nikke-snow-white"),
    PresetTarget("妮姬", "妮姬", "长发公主", "nikke-rapunzel"),
    PresetTarget("妮姬", "妮姬", "灰姑娘", "nikke-cinderella"),
    PresetTarget("妮姬", "妮姬", "拉毗", "nikke-rapi"),
    PresetTarget("妮姬", "妮姬", "阿妮斯", "nikke-anis"),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=r"D:\git\gitlab\ttsvoice")
    parser.add_argument("--output", default=r"D:\git\gitlab\VoiceLinkServer1\market_voice_presets")
    parser.add_argument("--ids", nargs="*", default=None)
    args = parser.parse_args()

    source_root = Path(args.source)
    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    selected_ids = set(args.ids or [])

    generated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for target in TARGETS:
        if selected_ids and target.preset_id not in selected_ids:
            continue
        result = build_package(source_root, output_root, target)
        if result["status"] == "packaged":
            generated.append(result)
            print(f"PACKAGED {result['id']} {result['name']} languages={','.join(result['languages'])}")
        else:
            skipped.append(result)
            print(f"SKIPPED {target.preset_id} {target.game_name} - {target.character}: {result['reason']}")

    index = {
        "outputDirectory": str(output_root),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "generatedPackages": generated,
        "skippedTargets": skipped,
    }
    (output_root / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "README.md").write_text(build_readme(index), encoding="utf-8")
    print(f"SUMMARY packaged={len(generated)} skipped={len(skipped)} output={output_root}")


def build_package(source_root: Path, output_root: Path, target: PresetTarget) -> dict[str, Any]:
    sources = find_language_sources(source_root, target)
    if not sources:
        return {
            "id": target.preset_id,
            "name": display_name(target),
            "status": "skipped",
            "reason": "no source zips found",
            "game": target.game_name,
            "character": target.character,
        }

    temp_dir = Path(tempfile.mkdtemp(prefix=f"{target.preset_id}-"))
    audio_dir = temp_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    profiles: list[dict[str, Any]] = []
    packaged_sources: list[dict[str, Any]] = []
    profile_sources: list[dict[str, Any]] = []
    try:
        for language_name, code, source_zip in sources:
            selected = select_reference_audio(source_zip)
            if selected is None:
                continue
            entry_name, reference_text = selected
            audio_path = unique_audio_path(audio_dir, code, entry_name)
            with zipfile.ZipFile(source_zip) as archive:
                with archive.open(entry_name) as source_file, (temp_dir / audio_path).open("wb") as output_file:
                    shutil.copyfileobj(source_file, output_file)
            profiles.append(
                {
                    "enabled": True,
                    "name": language_name,
                    "provider": "",
                    "targetLanguages": [code],
                    "referenceAudioPath": audio_path.as_posix(),
                    "referenceText": reference_text,
                    "cloneLanguage": "auto",
                }
            )
            source_info = {
                "language": code,
                "languageName": language_name,
                "sourceZip": str(source_zip),
                "sourceEntry": entry_name,
                "referenceAudioPath": audio_path.as_posix(),
                "referenceText": reference_text,
            }
            packaged_sources.append(source_info)
            profile_sources.append(source_info)

        if profiles:
            auto_source_index = next(
                (
                    index
                    for index, profile in enumerate(profiles)
                    if profile.get("targetLanguages") == ["zh"]
                ),
                0,
            )
            auto_profile = dict(profiles[auto_source_index])
            auto_profile["name"] = "自动语言"
            auto_profile["targetLanguages"] = ["*"]
            profiles.append(auto_profile)
            auto_source = dict(profile_sources[auto_source_index])
            auto_source["language"] = "auto"
            auto_source["languageName"] = "自动语言"
            auto_source["autoSourceLanguage"] = profile_sources[auto_source_index]["language"]
            packaged_sources.append(auto_source)
        else:
            auto_source_index = -1

        if not profiles:
            return {
                "id": target.preset_id,
                "name": display_name(target),
                "status": "skipped",
                "reason": "source zips have no supported audio entries",
                "game": target.game_name,
                "character": target.character,
            }

        auto_source_language = (
            profile_sources[auto_source_index]["language"]
            if auto_source_index >= 0
            else ""
        )
        auto_source_is_chinese = auto_source_language == "zh"

        manifest = {
            "id": target.preset_id,
            "name": display_name(target),
            "description": f"{target.game_name} 热门角色音色预设",
            "version": datetime.now().strftime("%Y.%m.%d"),
            "profiles": profiles,
        }
        (temp_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        package_path = output_root / f"{target.preset_id}.zip"
        if package_path.exists():
            package_path.unlink()
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
            package.write(temp_dir / "manifest.json", "manifest.json")
            for audio_file in sorted(audio_dir.iterdir()):
                package.write(audio_file, f"audio/{audio_file.name}")

        return {
            "id": target.preset_id,
            "name": display_name(target),
            "status": "packaged",
            "package": str(package_path),
            "size": package_path.stat().st_size,
            "game": target.game_name,
            "character": target.character,
            "languages": [profile["targetLanguages"][0] for profile in profiles],
            "autoSourceLanguage": auto_source_language,
            "autoSourceIsChinese": auto_source_is_chinese,
            "aliases": list(target.aliases),
            "sources": packaged_sources,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def find_language_sources(source_root: Path, target: PresetTarget) -> list[tuple[str, str, Path]]:
    result: list[tuple[str, str, Path]] = []
    game_root = source_root / target.game_dir
    if not game_root.exists():
        return result

    for language_dir_name, (code, language_name) in LANGUAGES.items():
        language_dir = game_root / language_dir_name
        if not language_dir.exists():
            continue
        source_zip = find_source_zip(language_dir, target)
        if source_zip is not None and source_zip.stat().st_size > 0:
            result.append((language_name, code, source_zip))
    return result


def find_source_zip(language_dir: Path, target: PresetTarget) -> Path | None:
    wanted = normalized_names((target.character, *target.aliases))
    candidates = sorted(language_dir.glob("*.zip"))
    exact_matches: list[Path] = []
    prefix_matches: list[Path] = []
    contains_matches: list[Path] = []
    for candidate in candidates:
        stem = candidate.stem
        normalized_stem = normalize_name(stem)
        stem_without_suffix = normalize_name(re.sub(r"_(ZH|JA|EN|KO|YUE)$", "", stem, flags=re.IGNORECASE))
        if stem_without_suffix in wanted or normalized_stem in wanted:
            exact_matches.append(candidate)
        elif any(stem_without_suffix.startswith(name) for name in wanted):
            prefix_matches.append(candidate)
        elif any(name in stem_without_suffix for name in wanted):
            contains_matches.append(candidate)
    for matches in (exact_matches, prefix_matches, contains_matches):
        if matches:
            return matches[0]
    return None


def normalized_names(values: tuple[str, ...]) -> set[str]:
    return {normalize_name(value) for value in values if value}


def normalize_name(value: str) -> str:
    return (
        value.strip()
        .replace("（", "(")
        .replace("）", ")")
        .replace("•", "")
        .replace("·", "")
        .replace("「", "")
        .replace("」", "")
        .lower()
    )


def select_reference_audio(source_zip: Path) -> tuple[str, str] | None:
    with zipfile.ZipFile(source_zip) as archive:
        audio_entries = [
            name
            for name in archive.namelist()
            if not name.endswith("/")
            and name.lower().endswith((".wav", ".mp3", ".ogg", ".opus"))
            and "__macosx/" not in name.lower()
        ]
    if not audio_entries:
        return None

    def key(name: str) -> tuple[int, int, str]:
        basename = Path(name).name
        emotion_rank = next((index for index, emotion in enumerate(EMOTION_PRIORITY) if emotion in basename), 99)
        return emotion_rank, len(basename), basename

    selected = sorted(audio_entries, key=key)[0]
    return selected, reference_text_from_entry(selected)


def reference_text_from_entry(entry_name: str) -> str:
    stem = Path(entry_name).stem
    stem = re.sub(r"^【[^】]+】", "", stem)
    stem = stem.replace("_", " ").strip()
    stem = re.sub(r"\s+", " ", stem)
    return stem.strip(" .。")


def unique_audio_path(audio_dir: Path, code: str, entry_name: str) -> Path:
    suffix = Path(entry_name).suffix.lower() or ".wav"
    base = safe_file_name(f"{code}-{Path(entry_name).stem}")[:140]
    candidate = Path("audio") / f"{base}{suffix}"
    counter = 2
    while (audio_dir.parent / candidate).exists():
        candidate = Path("audio") / f"{base}-{counter}{suffix}"
        counter += 1
    return candidate


def safe_file_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value)
    value = re.sub(r"\s+", "-", value).strip(" .-")
    return value or "reference"


def display_name(target: PresetTarget) -> str:
    character = target.aliases[0] if target.character == "开拓者(男)" else target.character
    character = target.aliases[0] if target.character == "开拓者(女)" else character
    return f"{target.game_name} - {character}"


def build_readme(index: dict[str, Any]) -> str:
    lines = [
        "# Market Voice Presets",
        "",
        "Generated voice clone preset packages for the server-side preset market.",
        "",
        "## Packaged",
        "",
    ]
    for item in index["generatedPackages"]:
        lines.append(f"- `{Path(item['package']).name}`: {item['name']} ({', '.join(item['languages'])})")
    if index["skippedTargets"]:
        lines.extend(["", "## Skipped", ""])
        for item in index["skippedTargets"]:
            lines.append(f"- `{item['id']}`: {item['name']} - {item['reason']}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
