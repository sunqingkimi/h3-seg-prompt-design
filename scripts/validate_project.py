#!/usr/bin/env python3
"""校验H3分段提示词项目的关键机械约束。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from export_director_plan import build_plan, default_output_path


REQUIRED_FILES = ("素材映射.md", "分镜设计.md", "H3分镜提示词.md")
FULL_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
BASE_FIELDS = (
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)

# 这些措辞会使本应单独提交的分镜提示词依赖其他分镜，需明确拦截。
CROSS_SEGMENT_PATTERNS = (
    r"(?:上一|上个|前一|之前|下一|下个|后续)分镜",
    r"(?:承接|延续|衔接|接续|回顾|预告)(?:上一|上个|前一|之前|下一|下个|后续)?(?:个)?分镜",
    r"(?:previous|prior|next|following)\s+(?:segment|storyboard|prompt)",
    r"(?:continues?|continuation)\s+(?:from|into)\s+(?:the\s+)?(?:previous|prior|next|following)\s+(?:segment|storyboard|prompt)",
    r"(?:recurr(?:ing|s)?|repeats?)\s+(?:across|in)\s+(?:multiple|other)\s+(?:segments?|prompts?|storyboards?)",
)

# 台词AI味黑名单：只收高频高置信的书面腔/AI腔模式，命中只告警；文艺含蓄仍靠人工朗读。
AI_FLAVOR_PATTERNS: tuple[tuple[str, str], ...] = (
    ("书面连接词", r"综上所述|总而言之|由此可见|毋庸置疑|与此同时|值得注意的是|众所周知"),
    ("AI腔负向排比", r"不是[^，。？！；\r\n]{1,15}，?而是|不仅[^，。？！；\r\n]{1,15}，?(?:更|还)是"),
    ("书面动词搭配", r"进行[\u4e00-\u9fff]{2}|予以[\u4e00-\u9fff]{1,3}"),
)

# 台词新信息高风险词：出现即要求在同段画面或前文有落点，否则观众误会（§11不凭空加词）。
MONEY_WORDS: tuple[str, ...] = ("钱", "买单", "付款", "付钱", "找零", "结账", "赊账")

# 无主指代开头：以这些词开句时，前一句或同段画面必须有明确落点（§11指代有主）。
PRONOUN_LEADS: tuple[str, ...] = ("他", "她", "它", "这", "那", "这样", "那样", "这里", "那里", "这个", "那个")

# 内容字停用表：求相邻两句交集时忽略这些高频虚字，只看实词是否衔接。
STOP_CHARS: frozenset[str] = frozenset("的了是在有和与你我他她它们这那那个这个什么怎么不也没都就才啊呀啦呢吗吧？?！!，、。…—· ")

# 卡通治愈轨（workflow-rules §12）触发词：命中任一即按卡通专轨加检，只告警不阻断。
CARTOON_KEYWORDS: tuple[str, ...] = (
    "卡通",
    "Q版",
    "治愈",
    "二次元",
    "吉祥物",
    "萌系",
    "玩偶",
    "卡通治愈轨",
    "场景圣经",
    "卡通打分",
    "形象卡",
    "风格锁句",
)

# 分镜设计卡通专轨必填字段（§12.2＋§9）：缺任一即警告，提示补齐后才允许写提示词。
CARTOON_STORYBOARD_FIELDS: tuple[str, ...] = (
    "开场钩子",
    "情绪线",
    "结尾钩子",
    "场景锚",
    "布局复述",
    "光色",
    "道具手位",
    "卡通打分",
)


def read_text(path: Path) -> str:
    """使用utf-8-sig兼容普通UTF-8和带BOM的Markdown文件。"""
    return path.read_text(encoding="utf-8-sig")


def parse_time(value: str) -> int:
    """把MM:SS转换为秒。"""
    minute, second = value.split(":")
    return int(minute) * 60 + int(second)


def parse_assets(mapping_text: str) -> dict[str, str]:
    """从素材三级标题与后续类型字段中提取别名和类型。"""
    assets: dict[str, str] = {}
    matches = list(re.finditer(r"(?m)^###\s+\d+\.\s+`([^`]+)`\s*$", mapping_text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(mapping_text)
        block = mapping_text[match.end() : end]
        type_match = re.search(r"(?m)^-\s*类型：\s*(图片|视频|音频)\s*$", block)
        if type_match:
            assets[match.group(1)] = type_match.group(1)
    return assets


def prompt_blocks(prompt_text: str) -> list[tuple[str, str]]:
    """提取每个分镜标题与紧随其后的text代码块。"""
    pattern = re.compile(
        r"(?ms)^##\s+(.+?｜[^\r\n]+)\s*\r?\n\s*```text\s*\r?\n(.*?)\r?\n```"
    )
    return [(match.group(1), match.group(2)) for match in pattern.finditer(prompt_text)]


def field_order(block: str) -> list[str]:
    """获取代码块中的顶级字段顺序。"""
    return re.findall(r"(?m)^([a-z_]+):", block)


def storyboard_segments(storyboard_text: str) -> list[tuple[str, bool, bool]]:
    """提取分镜名称及其是否为明确跨段长镜头，供提示词标题逐一核对。"""
    pattern = re.compile(r"(?ms)^###\s+(.+?)｜\d{2}:\d{2}—\d{2}:\d{2}[^\r\n]*\r?\n(.*?)(?=^###\s+|\Z)")
    segments: list[tuple[str, bool, bool]] = []
    for match in pattern.finditer(storyboard_text):
        label, content = match.groups()
        declaration = re.search(
            r"(?m)^-\s*长镜头跨段：\s*(是（接续上一个分镜）|否)\s*$", content
        )
        is_long_take_continuation = bool(
            declaration and declaration.group(1) == "是（接续上一个分镜）"
        )
        segments.append((label, is_long_take_continuation, declaration is not None))
    return segments


def dialogue_field(block_text: str, name: str) -> str:
    """读取台词DNA条目中“- 字段名：值”一行的值（空白仅限同行，禁止跨行吞下一行）。"""
    field_match = re.search(rf"(?m)^-[ \t]*{name}[：:][ \t]*(.*)$", block_text)
    return field_match.group(1).strip() if field_match else ""


def parse_dialogue_dna(mapping_text: str) -> dict[str, dict]:
    """解析素材映射“## 台词DNA”登记区，键为角色别名；缺区或无条目返回空。"""
    section_match = re.search(r"(?ms)^##\s+台词DNA[^\r\n]*\r?\n(.*?)(?=^##\s+|\Z)", mapping_text)
    if not section_match:
        return {}
    section = section_match.group(1)
    dna: dict[str, dict] = {}
    entries = list(re.finditer(r"(?m)^###\s+`([^`]+)`[^\r\n]*$", section))
    for index, entry_match in enumerate(entries):
        end = entries[index + 1].start() if index + 1 < len(entries) else len(section)
        block = section[entry_match.end() : end]
        ids_raw = dialogue_field(block, "说话人ID")
        speaker_ids = [
            re.sub(r"\s+", "", part).upper()
            for part in re.split(r"[,，、/]", ids_raw)
            if part.strip()
        ]
        banned = [
            item.strip()
            for item in re.split(r"[、，,;；/]", dialogue_field(block, "禁用语"))
            if item.strip()
        ]
        tail_raw = dialogue_field(block, "语气尾字")
        tails = [
            item.strip()
            for item in re.split(r"[、，,;；/]", tail_raw)
            if item.strip()
        ]
        dna[entry_match.group(1)] = {
            "speaker_ids": speaker_ids,
            "style": dialogue_field(block, "句式标签"),
            "banned": banned,
            "tails": tails,
        }
    return dna


def estimate_speech_seconds(language: str, content: str) -> float:
    """按工作流规则折算口播时长：中文4字/秒，英文2.5词/秒。"""
    if language.lower() == "chinese":
        return len(re.findall(r"[\u4e00-\u9fff]", content)) / 4
    return len(re.findall(r"[A-Za-z]+", content)) / 2.5


def first_banned_word(
    spoken_lines: list[tuple[list[str] | None, str, str]],
    dna_by_id: dict[str, str],
    dialogue_dna: dict[str, dict],
) -> tuple[str, str, str] | None:
    """按说话人归属查台词禁用语，返回第一条（说话人ID、角色别名、命中词），无则None。"""
    for speaker_ids, _, content in spoken_lines:
        for speaker_id in speaker_ids or []:
            owner = dna_by_id.get(speaker_id)
            for word in dialogue_dna.get(owner, {}).get("banned", []):
                if word and word in content:
                    return speaker_id, owner, word
    return None


def is_cartoon_project(mapping_text: str, storyboard_text: str, force: str) -> bool:
    """判定是否启用卡通治愈轨加检：显式开关优先，否则按关键词自动识别。"""
    if force == "yes":
        return True
    if force == "no":
        return False
    combined = f"{mapping_text}\n{storyboard_text}"
    return any(keyword in combined for keyword in CARTOON_KEYWORDS)


def storyboard_blocks(storyboard_text: str) -> list[tuple[str, str]]:
    """按###标题切分分镜设计块，返回（标题，块内容），供卡通字段完整性检查。"""
    pattern = re.compile(r"(?ms)^###\s+(.+?)\s*\r?\n(.*?)(?=^###\s+|\Z)")
    return [(match.group(1), match.group(2)) for match in pattern.finditer(storyboard_text)]


def normalize_line(value: str) -> str:
    """去空白归一化台词，用于分镜设计与提示词两处对齐比对。"""
    return re.sub(r"\s+", "", value)


def content_chars(value: str) -> set[str]:
    """提取台词实词字集（去停用虚字），用于问答衔接判断。"""
    return {char for char in re.findall(r"[\u4e00-\u9fff]", value) if char not in STOP_CHARS}


def is_question_line(value: str) -> bool:
    """含问号即视为问句，需下句直接回收（§11问答闭环）。"""
    return "?" in value or "？" in value


def storyboard_dialogue_per_segment(storyboard_text: str) -> list[tuple[list[str], str]]:
    """按时间轴顺序提取每段“对白或旁白”引号内台词与段证据文本。

    返回（台词表，证据文本）：证据文本为段内容去掉对白行本身，
    供“凭空新词／指代落点”检查，避免台词自己给自己当证据。
    无对白段返回空表。
    """
    pattern = re.compile(r"(?ms)^###\s+.+?｜\d{2}:\d{2}—\d{2}:\d{2}[^\r\n]*\r?\n(.*?)(?=^###\s+|\Z)")
    segments: list[tuple[list[str], str]] = []
    for match in pattern.finditer(storyboard_text):
        content = match.group(1)
        field_match = re.search(r"(?m)^-\s*对白或旁白：\s*(.*)\s*$", content)
        field = field_match.group(1).strip() if field_match else ""
        evidence = re.sub(r"(?m)^-\s*对白或旁白：.*$", "", content)
        if not field or re.match(r"^(无|N/A|NA|没有|静默)", field):
            segments.append(([], evidence))
            continue
        quoted = re.findall(r"[“”\"]([^“”\"]+)[“”\"]", field)
        if not quoted:
            quoted = re.findall(r'"([^"]+)"', field)
        segments.append(([item.strip() for item in quoted if item.strip()], evidence))
    return segments


def short(value: str, width: int = 12) -> str:
    """截断台词用于告警展示，避免刷屏。"""
    return value if len(value) <= width else value[:width] + "…"


def main() -> int:
    parser = argparse.ArgumentParser(description="校验H3分段提示词项目")
    parser.add_argument("project_dir", type=Path, help="包含三份Markdown交付物的视频项目目录")
    parser.add_argument(
        "--cartoon",
        dest="cartoon",
        choices=("auto", "yes", "no"),
        default="auto",
        help="卡通治愈轨加检开关：auto按关键词识别，yes强制启用，no强制关闭",
    )
    args = parser.parse_args()
    root = args.project_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []

    paths = {name: root / name for name in REQUIRED_FILES}
    for name, path in paths.items():
        if not path.is_file():
            errors.append(f"缺少必需文件：{name}")
    if errors:
        for item in errors:
            print(f"ERROR: {item}")
        return 1

    mapping = read_text(paths["素材映射.md"])
    storyboard = read_text(paths["分镜设计.md"])
    prompts = read_text(paths["H3分镜提示词.md"])
    expected_director_plan: dict[str, object] | None = None
    try:
        expected_director_plan, _ = build_plan(root)
        director_path = default_output_path(root, expected_director_plan)
        if not director_path.is_file():
            errors.append(f"缺少 Theodore Director 导出文件：{director_path.name}")
        else:
            try:
                director_plan = json.loads(read_text(director_path))
            except json.JSONDecodeError as exc:
                errors.append(f"{director_path.name} 不是有效 JSON：{exc}")
            else:
                if director_plan.get("schemaVersion") != 5:
                    errors.append(f"{director_path.name} 的 schemaVersion 必须为 Theodore Director v5。")
                expected_assets = expected_director_plan["assets"]
                actual_assets = director_plan.get("assets")
                if actual_assets != expected_assets:
                    errors.append(f"{director_path.name} 的素材映射与素材映射.md 不一致。")
                expected_shots = expected_director_plan["shots"]
                actual_shots = director_plan.get("shots")
                if not isinstance(actual_shots, list) or len(actual_shots) != len(expected_shots):
                    errors.append(f"{director_path.name} 的分镜数量与 H3分镜提示词.md 不一致。")
                else:
                    for shot_index, (expected_shot, actual_shot) in enumerate(
                        zip(expected_shots, actual_shots), start=1
                    ):
                        fields_to_check = ("id", "title", "prompt", "durationSeconds", "latentRelay")
                        if any(actual_shot.get(field) != expected_shot[field] for field in fields_to_check):
                            errors.append(
                                f"{director_path.name} 的第{shot_index}个分镜与 Markdown 标题、提示词、时长或长镜头接力声明不一致。"
                            )
    except ValueError as exc:
        errors.append(f"无法生成 Theodore Director 导出计划：{exc}")
    assets = parse_assets(mapping)
    if not assets:
        warnings.append("未从素材映射的三级标题中识别到带类型的素材。")

    # 禁止把已生成分镜自动登记或引用为新素材。
    generated_ref = re.compile(r"\{\{ref:分镜\d+成片(?:\.audio)?\}\}")
    for filename, text in (
        ("素材映射.md", mapping),
        ("分镜设计.md", storyboard),
        ("H3分镜提示词.md", prompts),
    ):
        if generated_ref.search(text):
            errors.append(f"{filename} 包含禁止的自动分镜素材引用。")

    blocks = prompt_blocks(prompts)
    headings = re.findall(r"(?m)^##\s+.+?｜\d{2}:\d{2}—\d{2}:\d{2}", prompts)
    if len(blocks) != len(headings):
        errors.append(f"识别到{len(headings)}个分镜标题，但只有{len(blocks)}个完整text代码块。")

    design_segments = storyboard_segments(storyboard)
    design_labels = [label for label, _, _ in design_segments]
    label_counts = {label: design_labels.count(label) for label in design_labels}
    occurrence_counts: dict[str, int] = {}
    expected_labels: list[str] = []
    for label in design_labels:
        occurrence_counts[label] = occurrence_counts.get(label, 0) + 1
        expected_labels.append(
            f"{label}{occurrence_counts[label]:02d}" if label_counts[label] > 1 else label
        )
    if len(expected_labels) != len(blocks):
        errors.append("分镜设计与分镜提示词的分镜数量不一致，无法核对标题名称。")

    dialogue_dna = parse_dialogue_dna(mapping)
    dna_by_id: dict[str, str] = {}
    for dna_alias, dna_entry in dialogue_dna.items():
        for dna_id in dna_entry["speaker_ids"]:
            dna_by_id.setdefault(dna_id, dna_alias)
    used_speaker_ids: set[str] = set()
    dialogue_exists = False
    first_dialogue_checked = False
    prompt_dialogues: list[list[str]] = []

    previous_end: int | None = None
    for index, (title, block) in enumerate(blocks, start=1):
        title_match = re.match(r"(.+?)｜(\d{2}:\d{2})—(\d{2}:\d{2})", title)
        if not title_match:
            errors.append(f"无法解析分镜标题时间：{title}")
            continue
        label, start_text, end_text = title_match.groups()
        display_name = label
        if index <= len(expected_labels) and label != expected_labels[index - 1]:
            errors.append(f"分镜{index:02d}标题应为“{expected_labels[index - 1]}”，需与分镜设计名称一致；重名时在名称后追加组内序号。")
        start, end = parse_time(start_text), parse_time(end_text)
        duration = end - start
        if not 5 <= duration <= 15:
            errors.append(f"{display_name}时长为{duration}秒，不在5—15秒范围内。")
        if previous_end is not None and start != previous_end:
            warnings.append(f"{display_name}起点{start_text}与上一分镜终点不连续。")
        previous_end = end

        fields = field_order(block)
        expected = FULL_FIELDS if "subject_definitions" in fields else BASE_FIELDS
        if tuple(fields) != expected:
            errors.append(f"{display_name}字段顺序不符合基础三字段或完整参考六字段结构：{fields}")

        if re.search(r"<(?:Picture|Video|Audio)\s+\d+>", block, re.I):
            errors.append(f"{display_name}仍使用官方Picture/Video/Audio编号，没有改用自定义ref。")

        aliases = set(re.findall(r"\{\{ref:([^}]+)\}\}", block))
        base_aliases = {alias[:-6] if alias.endswith(".audio") else alias for alias in aliases}
        unknown = sorted(alias for alias in base_aliases if alias not in assets)
        if unknown:
            errors.append(f"{display_name}引用未登记素材：{', '.join(unknown)}")

        counts = {"图片": 0, "视频": 0, "音频": 0}
        for alias in aliases:
            if alias.endswith(".audio"):
                counts["音频"] += 1
            else:
                asset_type = assets.get(alias)
                if asset_type:
                    counts[asset_type] += 1
        if counts["图片"] > 9 or counts["视频"] > 3 or counts["音频"] > 3:
            errors.append(
                f"{display_name}超出素材上限：图片{counts['图片']}、"
                f"视频{counts['视频']}、音频{counts['音频']}。"
            )

        has_continuation_marker = title.endswith("（接续上一个分镜）")
        if "接续" in title and not has_continuation_marker:
            errors.append(f"{display_name}接续标记应写成（接续上一个分镜）。")
        if index <= len(design_segments):
            expected_continuation = design_segments[index - 1][1]
            has_declaration = design_segments[index - 1][2]
            if not has_declaration:
                errors.append(f"{display_name}在分镜设计中缺少“长镜头跨段：是（接续上一个分镜）”或“长镜头跨段：否”声明。")
            if expected_continuation != has_continuation_marker:
                errors.append(f"{display_name}的接续标记必须与分镜设计中的“长镜头跨段”声明一致。")

        # 标题允许接续标记，但提示词正文不得包含跨分镜生成依赖。
        for pattern in CROSS_SEGMENT_PATTERNS:
            if re.search(pattern, block, re.IGNORECASE):
                errors.append(f"{display_name}提示词包含跨分镜表述，代码块必须能独立生成。")
                break

        # 台词机检（§9/§11）：超长、重复、首句钩子、语速核算、说话人归属、禁用语、AI味。只告警。
        spoken_lines: list[tuple[list[str] | None, str, str]] = []
        for d_match in re.finditer(r"<d>\[([A-Za-z]+)\](.*?)</d>", block, re.S):
            if not d_match.group(2).strip():
                continue  # Voice rule 等处的 <d>[Language]</d> 元引用不是真实台词。
            dialogue_exists = True
            nearest_speaker = None
            for speaker_match in re.finditer(
                r"\((S\d+(?:\s*[,，、]\s*S\d+)*)\)", block[: d_match.start()]
            ):
                nearest_speaker = speaker_match
            if nearest_speaker is not None and d_match.start() - nearest_speaker.end() <= 200:
                speaker_ids = [
                    re.sub(r"\s+", "", part).upper()
                    for part in re.split(r"[,，、]", nearest_speaker.group(1))
                ]
            else:
                speaker_ids = None
            spoken_lines.append((speaker_ids, d_match.group(1), d_match.group(2)))

        for _, language, content in spoken_lines:
            if language.lower() == "chinese" and len(re.findall(r"[\u4e00-\u9fff]", content)) > 20:
                warnings.append(f"{display_name}有超过20字的中文台词，可能在镜内说不完。")
                break
        seen_lines: set[str] = set()
        for _, language, content in spoken_lines:
            if language.lower() != "chinese":
                continue
            normalized = re.sub(r"\s+", "", content)
            if normalized in seen_lines:
                warnings.append(f"{display_name}同一句台词出现两次，成片可能重复念出。")
                break
            seen_lines.add(normalized)

        if spoken_lines and any(ids is None for ids, _, _ in spoken_lines):
            warnings.append(f"{display_name}有台词未绑定(SN)式说话人ID，无法核对一句一主与台词DNA。")
        for speaker_ids, _, _ in spoken_lines:
            if speaker_ids:
                used_speaker_ids.update(speaker_ids)

        if not first_dialogue_checked and spoken_lines:
            first_dialogue_checked = True
            first_cjk = len(re.findall(r"[\u4e00-\u9fff]", spoken_lines[0][2]))
            if first_cjk > 10:
                warnings.append(f"全片第一句台词{first_cjk}字，超过钩子上限10字，需更短更有张力。")

        if spoken_lines and "<cutoff" not in block:
            estimated = sum(
                estimate_speech_seconds(language, content) for _, language, content in spoken_lines
            ) + 0.5 * (len(spoken_lines) - 1)
            if estimated > duration:
                warnings.append(
                    f"{display_name}台词按中文4字/秒（英文2.5词/秒）＋句间0.5秒停顿预估"
                    f"{estimated:.1f}秒，超过段时长{duration}秒，需删句或并句。"
                )

        banned_hit = first_banned_word(spoken_lines, dna_by_id, dialogue_dna)
        if banned_hit:
            speaker_id, owner, word = banned_hit
            warnings.append(f"{display_name}中{speaker_id}（{owner}）台词含禁用语“{word}”。")

        for flavor_label, flavor_pattern in AI_FLAVOR_PATTERNS:
            for _, _, content in spoken_lines:
                flavor_match = re.search(flavor_pattern, content)
                if flavor_match:
                    warnings.append(
                        f"{display_name}台词疑似{flavor_label}（“{flavor_match.group(0)}”），按趣味法则重写。"
                    )
                    break

        # 人设尾字逐句核对：登记了语气尾字的角色，其中文台词缺尾字即告警（只告警）。
        for speaker_ids, language, content in spoken_lines:
            if language.lower() != "chinese" or not speaker_ids:
                continue
            for speaker_id in speaker_ids:
                owner = dna_by_id.get(speaker_id)
                tails = dialogue_dna.get(owner, {}).get("tails", []) if owner else []
                if tails and not any(tail in content for tail in tails):
                    warnings.append(
                        f"{display_name}中{speaker_id}（{owner}）台词“{content}”"
                        f"未带尾字{'/'.join(tails)}，确认是否人设漂移（§12.3）。"
                    )
                    break

        prompt_dialogues.append(
            [content for _, language, content in spoken_lines if language.lower() == "chinese"]
        )

    # 对话逻辑五检（workflow-rules §11，全部只告警，终判靠人工朗读）。
    story_dialogues = storyboard_dialogue_per_segment(storyboard)
    if len(story_dialogues) == len(prompt_dialogues) and any(
        story_lines or prompt_lines
        for story_lines, prompt_lines in zip(story_dialogues, prompt_dialogues)
    ):
        total_story = sum(len(lines) for lines, _ in story_dialogues)
        total_prompt = sum(len(lines) for lines in prompt_dialogues)
        if total_story != total_prompt and (total_story > 0 and total_prompt > 0):
            warnings.append(
                f"分镜设计共{total_story}句对白，提示词共{total_prompt}句，数量不一致，"
                f"两处必须逐字对齐（§11两处对齐）。"
            )
        for seg_index, ((story_lines, _), prompt_lines) in enumerate(
            zip(story_dialogues, prompt_dialogues)
        ):
            seg_name = blocks[seg_index][0].split("｜")[0] if seg_index < len(blocks) else f"分镜{seg_index + 1:02d}"
            if len(story_lines) != len(prompt_lines) and (story_lines or prompt_lines):
                # 分镜设计缺“对白或旁白”字段时 story_lines 为空，此时只在有引号台词时才告警，避免极简夹具误报。
                if story_lines or total_story > 0:
                    warnings.append(
                        f"{seg_name}分镜设计{len(story_lines)}句、提示词{len(prompt_lines)}句，"
                        f"两处必须逐字对齐（§11两处对齐）。"
                    )
                continue
            for story_line, prompt_line in zip(story_lines, prompt_lines):
                if normalize_line(story_line) != normalize_line(prompt_line):
                    warnings.append(
                        f"{seg_name}两处台词不一致：设计“{short(story_line)}”vs"
                        f"提示词“{short(prompt_line)}”，必须一字对齐（§11两处对齐）。"
                    )
                    break

    flat_lines: list[tuple[int, str]] = [
        (seg_index, line)
        for seg_index, lines in enumerate(prompt_dialogues)
        for line in lines
    ]
    prior_text = ""
    for pos, (seg_index, line) in enumerate(flat_lines):
        seg_name = blocks[seg_index][0].split("｜")[0] if seg_index < len(blocks) else f"分镜{seg_index + 1:02d}"
        seg_content = story_dialogues[seg_index][1] if seg_index < len(story_dialogues) else ""
        for money_word in MONEY_WORDS:
            if money_word in line and money_word not in seg_content and money_word not in prior_text:
                warnings.append(
                    f"{seg_name}台词“{short(line)}”含“{money_word}”类新词，"
                    f"但本段画面与前文均未出现，观众会误会，请删词或补动作（§11不凭空加词）。"
                )
                break
        if line[:2] in PRONOUN_LEADS or line[:1] in PRONOUN_LEADS:
            prev_line = flat_lines[pos - 1][1] if pos > 0 else ""
            if not (set(line) & set(prev_line)) and not (set(line) & set(seg_content)):
                warnings.append(
                    f"{seg_name}台词“{short(line)}”以指代词开头且前文无落点，"
                    f"观众听不懂指谁，请点名或补落点（§11指代有主）。"
                )
        if is_question_line(line):
            followed = flat_lines[pos + 1][1] if pos + 1 < len(flat_lines) else ""
            if followed and not (content_chars(line) & content_chars(followed)):
                next_seg = blocks[flat_lines[pos + 1][0]][0].split("｜")[0]
                warnings.append(
                    f"{seg_name}问句“{short(line)}”与下句“{short(followed)}”"
                    f"（{next_seg}）零字重叠、答非所问，请改成直接回答或加反应动作（§11问答闭环）。"
                )
        prior_text += line

    # 台词DNA跨镜核对：缺登记、ID漂移、未登记说话人、句式标签撞车。
    if dialogue_exists and not dialogue_dna:
        warnings.append("提示词含对白，但素材映射缺少“## 台词DNA”登记，句式跨镜一致性只能人工核对。")
    if dialogue_dna and dialogue_exists:
        registered_ids = {
            speaker_id: alias
            for alias, entry in dialogue_dna.items()
            for speaker_id in entry["speaker_ids"]
        }
        for alias, entry in dialogue_dna.items():
            for speaker_id in entry["speaker_ids"]:
                if speaker_id not in used_speaker_ids:
                    warnings.append(f"台词DNA为{alias}登记的{speaker_id}未在提示词中出现，确认说话人ID是否漂移。")
        for speaker_id in sorted(used_speaker_ids - set(registered_ids)):
            warnings.append(f"提示词使用了未登记台词DNA的说话人{speaker_id}，请在素材映射补登。")
        style_owners: dict[str, list[str]] = {}
        for alias, entry in dialogue_dna.items():
            if entry["style"]:
                style_owners.setdefault(entry["style"], []).append(alias)
        for style, owners in style_owners.items():
            if len(owners) > 1:
                warnings.append(f"{'、'.join(owners)}句式标签相同（{style}），两人不许互换句式。")

    # 金句三现核对（§9/§12.3）：集名含丨分隔的金句时，终段中文台词核心字必须与之一致；只告警。
    title_match = re.search(r"(?m)^#\s+《(.+?)》素材映射\s*$", mapping)
    if title_match and "丨" in title_match.group(1):
        candidate = title_match.group(1).split("丨")[-1]
        if "—" in candidate:
            candidate = candidate.split("—")[0]
        expected_core = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", candidate)
        last_spoken = ""
        for _, prompt_block in blocks:
            for d_match in re.finditer(r"<d>\[([A-Za-z]+)\](.*?)</d>", prompt_block, re.S):
                if d_match.group(1).lower() == "chinese" and d_match.group(2).strip():
                    last_spoken = d_match.group(2)
        if expected_core and last_spoken:
            actual_core = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", last_spoken)
            if actual_core != expected_core:
                warnings.append(
                    f"终段台词“{last_spoken}”与集名金句“{candidate.strip()}”"
                    f"核心字不一致，金句三现要求标题＋片中＋结尾一字不换（§9/§12.3）。"
                )

    # 卡通治愈轨加检（workflow-rules §12）：场景设计＋台词设计＋100分制，只告警不阻断。
    cartoon_mode = is_cartoon_project(mapping, storyboard, args.cartoon)
    if cartoon_mode:
        if "卡通形象卡与场景圣经" not in mapping and "风格锁句" not in mapping:
            warnings.append("卡通治愈轨：素材映射缺少“## 卡通形象卡与场景圣经”或风格锁句，形象卡与场景圣经未锁定。")
        if "叙事轨" not in storyboard or "卡通治愈轨" not in storyboard:
            warnings.append("卡通治愈轨：分镜设计缺少“叙事轨：卡通治愈轨”声明，默认按通用轨核对，请补声明。")
        cartoon_blocks = storyboard_blocks(storyboard)
        missing_field_segments = 0
        for seg_title, seg_content in cartoon_blocks:
            missing = [field for field in CARTOON_STORYBOARD_FIELDS if field not in seg_content]
            if missing:
                missing_field_segments += 1
                warnings.append(
                    f"卡通治愈轨：分镜“{seg_title}”缺{ '、'.join(missing)}字段，补齐后才允许写提示词（§12.2/§12.4）。"
                )
        # 机检小分60分：六项各10分，命中一类即扣完该项（只扣一次，避免同类刷屏重复扣）。
        def _cartoon_machine_total() -> int:
            total = 60
            checks = (
                ("超过钩子上限10字", 10),
                ("超过20字的中文台词", 10),
                ("未绑定(SN)式说话人ID", 10),
                ("超过段时长", 10),
                ("含禁用语", 10),
                ("疑似", 10),
            )
            for mark, points in checks:
                if any(mark in item for item in warnings):
                    total -= points
            return max(total, 0)

        cartoon_machine_score = _cartoon_machine_total()
        warnings.append(
            f"卡通机检小分{cartoon_machine_score}/60（首句10＋单句10＋绑定10＋语速10＋禁用10＋AI味10，每类只扣一次）。"
            f"分镜字段缺失{missing_field_segments}段；萌甜、动作互赖双测、布局合理性、收束力必须人工手打分；"
            f"段尾按“卡通打分：XX/100（场景X＋台词X＋保真X）＋短板”补齐，≥80才写提示词（§12.4）。"
        )

    for item in warnings:
        print(f"WARNING: {item}")
    for item in errors:
        print(f"ERROR: {item}")
    if errors:
        print(f"校验失败：{len(errors)}个错误，{len(warnings)}个警告。")
        return 1
    print(f"校验通过：{len(blocks)}个分镜，{len(assets)}项已登记素材，{len(warnings)}个警告。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
