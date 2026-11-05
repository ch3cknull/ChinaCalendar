# ChinaCalendar · 数据驱动的订阅日历

每个年度维护一份 JSON，一条命令生成全部 10 类 ICS。

- [订阅页面](https://ch3cknull.github.io/ChinaCalendar/)
- [法定节假日时间段版](https://ch3cknull.github.io/ChinaCalendar/cal_holiday_1.ics)
- [2027 年放假数据](generator/data/holidays/2027.json)
- [节日规则及背景介绍](generator/data/festivals.json)

```sh
uv sync --frozen
uv run python -m generator
uv run python -m unittest generator.tests -v
```

修改年度 JSON 后，运行上方命令，提交并推送生成结果即可更新 GitHub Pages。
默认自动读取全部年度 JSON。既有订阅文件名保持不变。
ICS 统一保存在 `docs/`。首页 `docs/index.html` 是独立维护的静态页面，生成器不修改它。

农历、节气、干支使用 lunar-python；ICS 序列化使用 icalendar。
放假调休与补班采用年度 JSON，不从农历推算。
2027 年日期按用户提供的安排整理，未附可核验的国务院通知链接。

所有生成逻辑、数据和测试都在 `generator/`：`__main__.py` 是入口，`data/` 是数据，`tests.py` 是测试。
`docs/` 保存静态首页和 10 类订阅文件；依赖版本固定在 `uv.lock`。

新增年度时，在 `generator/data/holidays/` 添加 `YYYY.json`。脚本自动识别年份。
年度文件包含 `year`、`updated_at`、`source` 和 `holidays`。日期使用 `YYYY-MM-DD`；`start` 和 `end` 都包含当天，`workdays` 列出补班日。
每条假期使用稳定的 `id`，春节使用 `spring-festival`。修改安排时，更新同一文件中的 `updated_at`。
`source.status` 使用 `user-provided` 或 `official`；标为 `official` 时必须填写 HTTPS 原文链接。

运行 `uv run python -m generator --check` 可检查已提交的 ICS 是否与数据一致。
运行 `uv run python -m generator --year 2027 --output build` 可将指定年份输出到临时目录。

自动查找下一年放假安排在技术上可行，但本仓库尚未启用自动采集或 AI 更新。
公开仓库的标准 [GitHub Actions runner 免费](https://docs.github.com/en/actions/concepts/billing-and-usage)，可以定期检查国务院通知，校验后提交更新 PR。
[GitHub Models 已于 2026-07-30 下线](https://github.blog/changelog/2026-07-01-github-models-is-being-fully-retired-on-july-30-2026/)。
[Copilot CLI 可在 Actions 中运行](https://docs.github.com/en/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/copilot-cli-in-github-actions)，但个人仓库的调用会使用所有者的 Copilot AI 额度，不能把 Actions 免费等同于 AI 免费。
若接入自动采集，应保留官方原文链接，校验完整假期和补班日期，再生成日历；缺少通知时保持现有数据。
还需注意：公开仓库连续 60 天无活动时，[定时工作流会被自动停用](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)。

Fork 自 [YangH9/ChinaCalendar](https://github.com/YangH9/ChinaCalendar)，遵循原 MIT 许可证。
GitHub Pages 发布源仍为 master 分支的 docs 目录。
