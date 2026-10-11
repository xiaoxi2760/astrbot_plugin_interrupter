# 复读撤回打断机 (astrbot_plugin_interrupter)

AstrBot 插件：群复读检测打断 + 引用消息回复"撤回"即可撤回。

## 功能

### 复读检测打断（全平台可用）

群里连续出现相同消息达到触发条数（默认 2 条）时触发一次：

- 按概率（默认 0.3）发送「打断施法！」文本；
- 未命中概率时跟风复读一条相同消息（可关闭）；
- 可选同时**撤回复读的消息**（需机器人是群管理员，仅 OneBot v11 生效，默认关闭）；
- 支持文本 / 图片 / 表情等混合消息的重复判定；同一串复读只打断一次；
- `/复读 on|off` 分群开关，状态持久化（重启后保留）。

### 引用撤回（OneBot v11 / aiocqhttp）

引用任意消息回复「撤回」（触发词可配置），机器人自动撤回被引用的消息：

- 可配置仅允许撤回机器人自己发的消息；
- 默认阻断撤回后的 LLM 回复；
- 撤回失败（无权限、消息超时等）静默忽略，不报错。

## 指令

| 指令 | 说明 |
| --- | --- |
| `/复读 on` | 开启本群复读检测打断功能（别名 `fudu`、`repetition`） |
| `/复读 off` | 关闭本群复读检测打断功能 |
| `/复读` | 查看本群当前状态与配置摘要 |
| 引用消息回复「撤回」 | 撤回被引用的消息 |

## 配置

在 AstrBot 管理面板的插件配置中修改，保存后自动热重载：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| trigger_count | 2 | 复读触发条数（连续相同消息数） |
| break_spell_probability | 0.3 | 触发后发送打断文本的概率（0-1） |
| break_spell_text | 打断施法！ | 打断文本内容 |
| follow_repeat | true | 未发送打断文本时是否跟风复读 |
| enable_recall | false | 触发时是否撤回复读的消息（需管理员，仅 OneBot v11） |
| recall_max_count | 3 | 最多撤回最近多少条复读消息 |
| recall_trigger_text | 撤回 | 引用撤回的触发词（精确匹配） |
| recall_scope | all | 撤回范围：all / self_only |
| stop_llm | true | 引用撤回后是否阻断 LLM 回复 |

## 安装

将本目录放入 AstrBot 的 `data/plugins/` 下（或在面板"插件市场"从仓库安装），重启或重载插件即可。

## 备注

- 复读检测自动跳过：唤醒机器人的消息（@机器人 / 前缀指令）、机器人自己的消息、带引用的消息。
- 引用撤回使用 OneBot v11 的 `delete_msg` 接口；撤回他人的消息需要机器人具备群管理员权限。

## 参考项目

本插件的复读检测打断与引用撤回功能参考了以下项目：

- [astrbot_plugin_repetition](https://github.com/FengYing1314/astrbot_plugin_repetition) @ FengYing1314
- [astrbot_plugin_recall](https://github.com/iris1598/astrbot_plugin_recall) @ iris1598
