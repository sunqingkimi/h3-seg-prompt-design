#!/usr/bin/env python3
"""把 H3 分镜 Markdown 交付物导出为 Theodore Director v5 项目计划。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 5
DEFAULT_BASE_SEED = 123456790
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


def read_text(path: Path) -> str:
    """使用 UTF-8-SIG 同时兼容普通 UTF-8 与带 BOM 的 Markdown 文件。"""
    return path.read_text(encoding="utf-8-sig")


def parse_time(value: str) -> int:
    """把 MM:SS 转换为秒数。"""
    minute, second = value.split(":")
    return int(minute) * 60 + int(second)


def project_name_from_mapping(mapping_text: str, fallback: str) -> str:
    """优先取素材映射标题中的项目名，避免导出名与项目交付物脱节。"""
    match = re.search(r"(?m)^#\s+《(.+?)》素材映射\s*$", mapping_text)
    return match.group(1).strip() if match and match.group(1).strip() else fallback


def safe_export_name(project_name: str) -> str:
    """仅清洗文件名；JSON 内 project.name 始终保留用户原始项目名。"""
    cleaned = INVALID_FILENAME_CHARS.sub("_", project_name).strip().rstrip(".")
    return cleaned or "Theodore Project"


def parse_assets(mapping_text: str) -> list[dict[str, Any]]:
    """读取素材映射中已锁定的素材，并保留空路径供导播台后续补充。"""
    heading_pattern = re.compile(r"(?m)^###\s+\d+\.\s+`([^`]+)`\s*$")
    matches = list(heading_pattern.finditer(mapping_text))
    assets: list[dict[str, Any]] = []
    type_map = {"图片": "image", "视频": "video", "音频": "audio"}
    for index, match in enumerate(matches, start=1):
        end = matches[index].start() if index < len(matches) else len(mapping_text)
        block = mapping_text[match.end() : end]
        type_match = re.search(r"(?m)^-\s*类型：\s*(图片|视频|音频)\s*$", block)
        if not type_match:
            raise ValueError(f"素材“{match.group(1)}”缺少有效的类型字段。")
        # 冒号后的空白只接受同一行的空格或制表符，不能吞掉下一行字段。
        path_match = re.search(r"(?m)^-\s*本地路径：[ \t]*(.*)$", block)
        audio_match = re.search(r"(?m)^-\s*启用视频伴音：\s*(是|否)\s*$", block)
        kind = type_map[type_match.group(1)]
        assets.append(
            {
                "id": f"asset_{index:03d}",
                "alias": match.group(1),
                "kind": kind,
                "path": path_match.group(1).strip() if path_match else "",
                "enabled": True,
                "fixed": False,
                "fixedOrder": 0,
                "shotIds": [],
                "includeVideoAudio": kind == "video" and bool(audio_match and audio_match.group(1) == "是"),
                "durationSeconds": None,
                "audioDurationSeconds": None,
                "fingerprint": "",
            }
        )
    aliases = [asset["alias"].casefold() for asset in assets]
    duplicates = [alias for alias, count in Counter(aliases).items() if count > 1]
    if duplicates:
        raise ValueError(f"素材别名重复：{', '.join(duplicates)}")
    return assets


def parse_prompt_blocks(prompt_text: str) -> list[tuple[str, int, int, str]]:
    """提取标题、时间范围和完整 text 代码块，代码块即 Director 的 shot.prompt。"""
    pattern = re.compile(
        r"(?ms)^##\s+(.+?)｜(\d{2}:\d{2})—(\d{2}:\d{2})(?:（接续上一个分镜）)?\s*\r?\n\s*```text\s*\r?\n(.*?)\r?\n```"
    )
    blocks: list[tuple[str, int, int, str]] = []
    for match in pattern.finditer(prompt_text):
        title, start_text, end_text, prompt = match.groups()
        blocks.append((title, parse_time(start_text), parse_time(end_text), prompt))
    return blocks


def parse_long_take_flags(storyboard_text: str) -> list[bool]:
    """按分镜设计顺序读取长镜头跨段声明，决定 Director 的 latentRelay。"""
    pattern = re.compile(r"(?ms)^###\s+.+?｜\d{2}:\d{2}—\d{2}:\d{2}[^\r\n]*\r?\n(.*?)(?=^###\s+|\Z)")
    flags: list[bool] = []
    for match in pattern.finditer(storyboard_text):
        declaration = re.search(
            r"(?m)^-\s*长镜头跨段：\s*(是（接续上一个分镜）|否)\s*$",
            match.group(1),
        )
        if declaration is None:
            raise ValueError("分镜设计缺少“长镜头跨段：是（接续上一个分镜）”或“长镜头跨段：否”声明。")
        flags.append(declaration.group(1) == "是（接续上一个分镜）")
    return flags


def referenced_aliases(prompt: str) -> set[str]:
    """提取提示词中的别名引用；.audio 视为视频素材的伴音开关。"""
    return set(re.findall(r"\{\{ref:([^}]+)\}\}", prompt))


def build_plan(project_dir: Path) -> tuple[dict[str, Any], list[str]]:
    """从三份 Markdown 交付物构建可导入的 Theodore Director v5 JSON。"""
    mapping_path = project_dir / "素材映射.md"
    storyboard_path = project_dir / "分镜设计.md"
    prompts_path = project_dir / "H3分镜提示词.md"
    for path in (mapping_path, storyboard_path, prompts_path):
        if not path.is_file():
            raise ValueError(f"缺少必需文件：{path.name}")

    mapping = read_text(mapping_path)
    storyboard = read_text(storyboard_path)
    prompt_text = read_text(prompts_path)
    project_name = project_name_from_mapping(mapping, project_dir.name)
    assets = parse_assets(mapping)
    asset_by_alias = {asset["alias"]: asset for asset in assets}
    blocks = parse_prompt_blocks(prompt_text)
    long_take_flags = parse_long_take_flags(storyboard)
    if not blocks:
        raise ValueError("未在 H3分镜提示词.md 中识别到完整的分镜 text 代码块。")
    if len(blocks) != len(long_take_flags):
        raise ValueError("分镜设计与分镜提示词的分镜数量不一致。")

    shots: list[dict[str, Any]] = []
    missing_path_aliases: list[str] = []
    for index, (title, start, end, prompt) in enumerate(blocks, start=1):
        if not 5 <= end - start <= 15:
            raise ValueError(f"{title}时长不在 5—15 秒范围内。")
        for reference in referenced_aliases(prompt):
            alias = reference.removesuffix(".audio")
            asset = asset_by_alias.get(alias)
            if asset is None:
                raise ValueError(f"{title}引用未登记素材：{alias}")
            if reference.endswith(".audio"):
                if asset["kind"] != "video":
                    raise ValueError(f"{title}的 {reference} 不是视频伴音引用。")
                if not asset["includeVideoAudio"]:
                    raise ValueError(f"{title}引用 {reference}，但素材映射未启用该视频伴音。")
        for asset in assets:
            if not asset["path"] and asset["alias"] not in missing_path_aliases:
                missing_path_aliases.append(asset["alias"])
        shots.append(
            {
                "id": f"shot_{index:03d}",
                "title": title,
                "prompt": prompt,
                "negativePrompt": "",
                "durationSeconds": end - start,
                "enabled": True,
                "latentRelay": long_take_flags[index - 1],
                "secondSamplingMode": "super_resolution_second_pass",
                "seed": None,
                "disabledAssetIds": [],
            }
        )

    project_id = f"td_{hashlib.sha256(project_name.encode('utf-8')).hexdigest()[:12]}"
    plan = {
        "schemaVersion": SCHEMA_VERSION,
        "project": {"id": project_id, "name": project_name, "runId": "run_001"},
        "defaults": {"fps": 24, "baseSeed": DEFAULT_BASE_SEED},
        "promptPrefix": "",
        "promptSuffix": "",
        "continuity": {
            "mode": "h3_av_latent",
            "videoContextFrames": 22,
            "audioContextFrames": 24,
            "durationMode": "final_output",
        },
        "assets": assets,
        "shots": shots,
    }
    return plan, missing_path_aliases


def default_output_path(project_dir: Path, plan: dict[str, Any]) -> Path:
    """导出文件位于项目目录，名称与 Director 项目名一致且可在 Windows 落盘。"""
    project_name = str(plan["project"]["name"])
    return project_dir / f"{safe_export_name(project_name)}.director.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 Theodore Director v5 项目计划 JSON")
    parser.add_argument("project_dir", type=Path, help="包含三份 Markdown 交付物的视频项目目录")
    parser.add_argument("--output", type=Path, help="可选的 .director.json 输出路径")
    args = parser.parse_args()
    try:
        project_dir = args.project_dir.resolve()
        plan, missing_paths = build_plan(project_dir)
        output_path = args.output.resolve() if args.output else default_output_path(project_dir, plan)
        output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"已导出 Theodore Director v5 配置：{output_path}")
    if missing_paths:
        print("WARNING: 以下素材路径为空；JSON 可导入，但需在导播台上传或补充路径后才能执行：" + ", ".join(missing_paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
