# Theodore Director Plan v5 导出协议

本 skill 的导出器适配 Theodore Director `main` 分支的 Plan v5。配置文件由导播台导入后，还需用户点击“保存到工作流”。

## skill 写入的字段

- `schemaVersion`：`5`。
- `project`：从《素材映射》的标题读取项目名，生成稳定 `td_<hash>` 内部 ID，并使用 `run_001`。
- `defaults` 与 `continuity`：24 FPS、默认种子 `123456790`、`h3_av_latent`、22/24 上下文帧、`final_output`。
- `assets`：别名、类型、本地路径和视频伴音开关。路径为空仍合法，可导入但不能执行。
- `shots`：`shot_001` 式 ID、最终提示词、标题、秒数、空负面提示词、启用状态及 `latentRelay`。高清处理方式固定为 `super_resolution_second_pass`。

## 映射约束

- `{{ref:别名}}` 必须在素材映射中存在。
- `{{ref:别名.audio}}` 只能引用视频素材，且其条目必须写 `启用视频伴音：是`。
- JSON 中的 `title` 不包含 `（接续上一个分镜）`；该信息仅映射为 `latentRelay`，避免把跨段依赖写入提示词或镜头名。
- 导出文件的文件名会清洗 Windows 禁用字符，但 JSON 内的 `project.name` 不会被改写。
