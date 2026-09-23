#!/usr/bin/env python3
"""覆盖 Theodore Director JSON 导出器的核心映射规则。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from export_director_plan import build_plan, default_output_path


def write_fixture(root: Path) -> None:
    """写入含三类素材、重名分镜和跨段长镜头的最小项目。"""
    (root / "素材映射.md").write_text(
        """# 《测试项目》素材映射

### 1. `hero`
- 类型：图片
- 本地路径：input/hero.png

### 2. `walk`
- 类型：视频
- 本地路径：
- 启用视频伴音：是

### 3. `music`
- 类型：音频
- 本地路径：input/music.wav
""",
        encoding="utf-8",
    )
    (root / "分镜设计.md").write_text(
        """### 雨巷初遇｜00:00—00:05
- 长镜头跨段：否

### 雨巷初遇｜00:05—00:10
- 长镜头跨段：否

### 屋檐避雨｜00:10—00:15
- 长镜头跨段：是（接续上一个分镜）
""",
        encoding="utf-8",
    )
    (root / "H3分镜提示词.md").write_text(
        """## 雨巷初遇01｜00:00—00:05
```text
integrated_multimodal_description: {{ref:hero}}
overall_soundscape: N/A
non_diegetic_music: N/A
```

## 雨巷初遇02｜00:05—00:10
```text
integrated_multimodal_description: {{ref:walk}}
overall_soundscape: N/A
non_diegetic_music: N/A
```

## 屋檐避雨｜00:10—00:15（接续上一个分镜）
```text
integrated_multimodal_description: {{ref:walk.audio}} {{ref:music}}
overall_soundscape: N/A
non_diegetic_music: N/A
```
""",
        encoding="utf-8",
    )


def run_validator(root: Path) -> subprocess.CompletedProcess[str]:
    """以UTF-8模式运行正式校验器并捕获输出。"""
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(Path(__file__).with_name("validate_project.py")), str(root)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def write_dialogue_fixture(root: Path) -> None:
    """写入登记台词DNA、且提示词触发全部台词告警的最小对白项目。"""
    (root / "素材映射.md").write_text(
        """# 《对白测试》素材映射

### 1. `hero`
- 类型：图片
- 本地路径：input/hero.png

## 台词DNA

### `噜噜`
- 说话人ID：S1
- 句式标签：宣言式短句
- 禁用语：宝贝

### `噜妹`
- 说话人ID：S2
- 句式标签：宣言式短句

### `阿黄`
- 说话人ID：S9
- 句式标签：反问加惊叹
""",
        encoding="utf-8",
    )
    (root / "分镜设计.md").write_text(
        """### 雨巷初遇｜00:00—00:10
- 长镜头跨段：否

### 屋檐避雨｜00:10—00:15
- 长镜头跨段：否
""",
        encoding="utf-8",
    )
    (root / "H3分镜提示词.md").write_text(
        """## 雨巷初遇｜00:00—00:10
```text
integrated_multimodal_description: {{ref:hero}} Voice rule: the ONLY speech is the line inside the <d>[Chinese]</d> tag. The round hippo (S1) declares: <d>[Chinese]综上所述宝贝就是这里了不会错的今天就把宝贝挖出来</d> The smaller hippo (S2) shouts: <d>[Chinese]不行不行这不是你的宝贝不能拿走还给我呀求求你了哥哥真是的</d> The old dog (S3) sighs: <d>[Chinese]唉</d>
overall_soundscape: N/A
non_diegetic_music: N/A
```

## 屋檐避雨｜00:10—00:15
```text
integrated_multimodal_description: {{ref:hero}} She says: <d>[Chinese]好呀</d>
overall_soundscape: N/A
non_diegetic_music: N/A
```
""",
        encoding="utf-8",
    )


def test_dialogue_warnings() -> None:
    """校验器对台词DNA、语速与AI味黑名单逐项告警且不阻断。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        write_dialogue_fixture(root)
        plan, _ = build_plan(root)
        output_path = default_output_path(root, plan)
        output_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        validation = run_validator(root)
        assert validation.returncode == 0, validation.stdout + validation.stderr
        for fragment in (
            "超过段时长10秒",
            "超过钩子上限10字",
            "疑似书面连接词",
            "含禁用语",
            "未登记台词DNA的说话人S3",
            "句式标签相同",
            "未在提示词中出现",
            "未绑定(SN)式说话人ID",
            "超过20字的中文台词",
        ):
            assert fragment in validation.stdout, fragment
        # Voice rule 的 <d>[Chinese]</d> 元引用不是台词：只允许夹具中真正未绑定的那一句告警一次。
        assert validation.stdout.count("未绑定(SN)式说话人ID") == 1, validation.stdout


def test_tail_and_gold_checks() -> None:
    """新增两项一致性检查：S1缺尾字逐句告警、终段台词与集名金句不一致告警；只告警不阻断。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        (root / "素材映射.md").write_text(
            """# 《测试剧 · 第二集丨一人一台，刚刚好呀—iPhone Duo》素材映射

### 1. `hero`
- 类型：图片
- 本地路径：input/hero.png

### 2. `voice1`
- 类型：音频
- 本地路径：input/v1.wav

## 台词DNA

### `噜噜`
- 说话人ID：S1
- 句式标签：宣言式短句
- 语气尾字：呀、啦
- 禁用语：
""",
            encoding="utf-8",
        )
        (root / "分镜设计.md").write_text(
            """### 开场｜00:00—00:10
- 长镜头跨段：否

### 收束｜00:10—00:20
- 长镜头跨段：否
""",
            encoding="utf-8",
        )
        (root / "H3分镜提示词.md").write_text(
            """## 开场｜00:00—00:10
```text
integrated_multimodal_description: {{ref:hero}} The boy (S1) says: <d>[Chinese] 卡住啦，救我呀！</d>
overall_soundscape: N/A
non_diegetic_music: N/A
```

## 收束｜00:10—00:20
```text
integrated_multimodal_description: {{ref:hero}} The boy (S1) says: <d>[Chinese] 手机两个人，刚刚好！</d>
overall_soundscape: N/A
non_diegetic_music: N/A
```
""",
            encoding="utf-8",
        )
        plan, _ = build_plan(root)
        output_path = default_output_path(root, plan)
        output_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        validation = run_validator(root)
        assert validation.returncode == 0, validation.stdout + validation.stderr
        # 收束句缺呀/啦尾且与集名金句核心字不一致，两项各告警一次。
        assert "未带尾字呀/啦" in validation.stdout, validation.stdout
        assert "与集名金句" in validation.stdout, validation.stdout
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        (root / "素材映射.md").write_text(
            """# 《测试剧 · 第二集丨一人一台，刚刚好呀—iPhone Duo》素材映射

### 1. `hero`
- 类型：图片
- 本地路径：input/hero.png

### 2. `voice1`
- 类型：音频
- 本地路径：input/v1.wav

## 台词DNA

### `噜噜`
- 说话人ID：S1
- 句式标签：宣言式短句
- 语气尾字：呀、啦
- 禁用语：
""",
            encoding="utf-8",
        )
        (root / "分镜设计.md").write_text(
            """### 开场｜00:00—00:10
- 长镜头跨段：否

### 收束｜00:10—00:20
- 长镜头跨段：否
""",
            encoding="utf-8",
        )
        (root / "H3分镜提示词.md").write_text(
            """## 开场｜00:00—00:10
```text
integrated_multimodal_description: {{ref:hero}} The boy (S1) says: <d>[Chinese] 卡住啦，救我呀！</d>
overall_soundscape: N/A
non_diegetic_music: N/A
```

## 收束｜00:10—00:20
```text
integrated_multimodal_description: {{ref:hero}} The boy (S1) says: <d>[Chinese] 一人一台，刚刚好呀！</d>
overall_soundscape: N/A
non_diegetic_music: N/A
```
""",
            encoding="utf-8",
        )
        plan, _ = build_plan(root)
        output_path = default_output_path(root, plan)
        output_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        validation = run_validator(root)
        assert validation.returncode == 0, validation.stdout + validation.stderr
        assert "WARNING" not in validation.stdout, validation.stdout


def test_dialogue_logic_warnings() -> None:
    """对话逻辑五检只告警不阻断：两处不一致、凭空钱词、问答脱节、无主指代逐项告警。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        (root / "素材映射.md").write_text(
            """# 《逻辑测试》素材映射

### 1. `hero`
- 类型：图片
- 本地路径：input/hero.png
""",
            encoding="utf-8",
        )
        (root / "分镜设计.md").write_text(
            """### 流星兔客｜00:00—00:10
- 长镜头跨段：否
- 画面与动作：噜噜擦窗，流星划过，玉兔倒袋滚出一颗糖
- 对白或旁白：噜噜 (S1)“别眨眼，有流星啦！”；噜妹 (S2)“那个……钱不够呀？”；噜噜 (S1)“这瓶我请啦！”

### 失重漂浮｜00:10—00:20
- 长镜头跨段：否
- 画面与动作：糖飘起，瓶子飘向天花板
- 对白或旁白：噜噜 (S1)“飘起别怕呀！”；噜妹 (S2)“瓶子长腿啦！”
""",
            encoding="utf-8",
        )
        (root / "H3分镜提示词.md").write_text(
            """## 流星兔客｜00:00—00:10
```text
integrated_multimodal_description: {{ref:hero}} The boy (S1) says: <d>[Chinese]别眨眼，有流星啦！</d> The girl (S2) says: <d>[Chinese]那个……钱不够呀？</d> The boy (S1) says: <d>[Chinese]这瓶我请啦！</d>
overall_soundscape: N/A
non_diegetic_music: N/A
```

## 失重漂浮｜00:10—00:20
```text
integrated_multimodal_description: {{ref:hero}} The boy (S1) says: <d>[Chinese]飘起别怕呀！</d> The girl (S2) says: <d>[Chinese]瓶子私奔啦！</d>
overall_soundscape: N/A
non_diegetic_music: N/A
```
""",
            encoding="utf-8",
        )
        plan, _ = build_plan(root)
        output_path = default_output_path(root, plan)
        output_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        validation = run_validator(root)
        assert validation.returncode == 0, validation.stdout + validation.stderr
        for fragment in (
            "两处台词不一致",
            "凭空加词",
            "问答闭环",
            "指代有主",
        ):
            assert fragment in validation.stdout, fragment


def main() -> None:
    """执行无外部依赖的导出断言。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        write_fixture(root)
        plan, missing_paths = build_plan(root)
        assert plan["schemaVersion"] == 5
        assert [shot["title"] for shot in plan["shots"]] == ["雨巷初遇01", "雨巷初遇02", "屋檐避雨"]
        assert [shot["latentRelay"] for shot in plan["shots"]] == [False, False, True]
        assert plan["assets"][1]["includeVideoAudio"] is True
        assert missing_paths == ["walk"]
        output_path = default_output_path(root, plan)
        output_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        assert json.loads(output_path.read_text(encoding="utf-8"))["shots"][2]["id"] == "shot_003"
        # 使用正式校验器确认 JSON 与三个 Markdown 交付物可彼此核对，且无对白项目零告警。
        validation = run_validator(root)
        assert validation.returncode == 0, validation.stdout + validation.stderr
        assert "WARNING" not in validation.stdout, validation.stdout
    test_dialogue_warnings()
    test_tail_and_gold_checks()
    test_dialogue_logic_warnings()
    print("Director export tests passed.")


if __name__ == "__main__":
    main()
