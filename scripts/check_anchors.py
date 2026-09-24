#!/usr/bin/env python3
"""人物/道具锚点审计：检查每段提示词是否把座位绑定、区分物、道具总数锁写全。

定位：validate_project.py 只查机械一致性，不管语义位置；本脚本查“细节在提示词阶段
是否写死”，不依赖模型临场发挥。注意：本脚本查的是锁句的“有无”，不是语义“对错”，
最终仍需人工通读相邻镜头。

用法：
    python scripts/check_anchors.py "<视频项目文件夹>"

退出码：有 ERROR 返回 1，否则返回 0（WARNING 不阻断）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# 座位/位置锚定句特征：出现即认为本段写了位置绑定。
ANCHOR_PATTERNS = (
    r"驾驶|副驾|座位|driver|passenger|occupant|恒.+座|座.+恒|always <Subject",
)
# 道具总数锁特征（防翻倍：方向盘×2类问题）。
COUNT_PATTERNS = (
    r"全车仅|总数|exactly ONE|only one|共\d|×\d",
)
# 否定锁特征（副驾无轮/无复制件类声明）。
NEGATION_PATTERNS = (
    r"无方向盘|无转向柱|不翻倍|没有|素仪表盘|bare|no second|no wheel|no column|no duplicated",
)
# 卡通项目判定（沿用 validate 口径的关键词子集）：命中才启用分镜设计侧的严格检查。
CARTOON_KEYWORDS = (
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


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def prompt_blocks(prompt_text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"(?ms)^##\s+(.+?｜[^\r\n]+)\s*\r?\n\s*```text\s*\r?\n(.*?)\r?\n```"
    )
    return [(m.group(1), m.group(2)) for m in pattern.finditer(prompt_text)]


def body_of(block: str) -> str:
    """取正文区（detailed_description 或基础模式的 integrated_multimodal_description）。"""
    for field in ("detailed_description", "integrated_multimodal_description"):
        m = re.search(r"(?ms)^" + field + r":\s*\r?\n(.*?)(?=^\w+:|\Z)", block)
        if m:
            return m.group(1)
    return ""


def defined_subjects(block: str) -> set[str]:
    m = re.search(r"(?ms)^subject_definitions:\s*\r?\n(.*?)(?=^summary:|\Z)", block)
    section = m.group(1) if m else ""
    return set(re.findall(r"<Subject\s+(\d+)>", section))


def main() -> int:
    parser = argparse.ArgumentParser(description="审计H3分镜人物/道具锚点是否写全")
    parser.add_argument("project_dir", type=Path, help="包含三份Markdown交付物的视频项目目录")
    args = parser.parse_args()
    root = args.project_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []

    paths = {
        "素材映射.md": root / "素材映射.md",
        "分镜设计.md": root / "分镜设计.md",
        "H3分镜提示词.md": root / "H3分镜提示词.md",
    }
    for name, path in paths.items():
        if not path.is_file():
            errors.append(f"缺少必需文件：{name}")
    if errors:
        for item in errors:
            print(f"ERROR: {item}")
        return 1

    storyboard = read_text(paths["分镜设计.md"])
    prompts = read_text(paths["H3分镜提示词.md"])
    cartoon = any(k in storyboard for k in CARTOON_KEYWORDS)

    blocks = prompt_blocks(prompts)
    if not blocks:
        errors.append("未识别到分镜text代码块。")
    for title, block in blocks:
        label = title.split("｜")[0]
        body = body_of(block)
        if not body:
            errors.append(f"{label}缺少正文区（detailed_description/integrated_multimodal_description）。")
            continue
        if not any(re.search(p, body) for p in ANCHOR_PATTERNS):
            warnings.append(f"{label}正文缺少座位/位置锚定句（驾驶/副驾/driver/passenger类绑定），串位风险。")
        if not any(re.search(p, body) for p in COUNT_PATTERNS):
            warnings.append(f"{label}正文缺少道具总数锁（全车仅×/exactly ONE类），翻倍风险。")
        if not any(re.search(p, body) for p in NEGATION_PATTERNS):
            warnings.append(f"{label}正文缺少对侧否定锁（无×/bare/no…类），复制件风险。")
        defined = defined_subjects(block)
        used = set(re.findall(r"<Subject\s+(\d+)>", body))
        for sid in sorted(used - defined, key=int):
            errors.append(f"{label}使用了未在subject_definitions定义的<Subject {sid}>。")
        for sid in sorted(defined - used, key=int):
            warnings.append(f"{label}定义的<Subject {sid}>在正文中一次未出现，确认是否漏写。")
        shots = re.split(r"\[Shot \d+\]", body)[1:]
        if not shots:
            warnings.append(f"{label}未识别到[Shot]分镜，无法逐镜核对。")
        for i, shot in enumerate(shots, start=1):
            if not re.search(r"<Subject\s+\d+>|\{\{ref:", shot):
                warnings.append(f"{label}的[Shot {i}]未引用任何Subject/素材，人物位置无锚点。")

    if cartoon:
        segments = re.findall(
            r"(?ms)^###\s+(.+?｜\d{2}:\d{2}—\d{2}:\d{2})[^\r\n]*\r?\n(.*?)(?=^###\s+|\Z)",
            storyboard,
        )
        for seg_title, content in segments:
            seg_label = seg_title.split("｜")[0]
            if "布局复述" in content and not re.search(r"恒|绑定|锚|anchor|always", content):
                warnings.append(f"分镜设计“{seg_label}”有布局复述但无座位绑定（恒/绑定/锚），请补。")
            if "道具手位" in content and not re.search(r"仅|总数|无|不翻倍", content):
                warnings.append(f"分镜设计“{seg_label}”有道具手位但无总数/否定锁（仅/无/不翻倍），请补。")

    for item in warnings:
        print(f"WARNING: {item}")
    for item in errors:
        print(f"ERROR: {item}")
    if errors:
        print(f"锚点审计失败：{len(errors)}个错误，{len(warnings)}个警告。")
        return 1
    print(f"锚点审计通过：{len(blocks)}个分镜，{len(warnings)}个警告。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
