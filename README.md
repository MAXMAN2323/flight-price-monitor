# Flight Price Monitor

公开 GitHub Actions 机票价格备用监测器。每 3 小时运行一次，监测到低于阈值的票价后，通过 PushPlus 推送到微信。

## 当前监测条件

- 北京到泰国哨兵组合：`PEK`、`PKX` 到 `BKK`、`DMK`，往返，低于 `3000 CNY` 提醒。
- 北京到西班牙哨兵组合：`PEK`、`PKX` 到 `MAD`、`BCN`，往返，低于 `6000 CNY` 提醒。
- 出发日期：`2026-09-25` 或 `2026-09-26`。
- 返回日期：`2026-10-06` 或 `2026-10-07`。
- 每轮查询组合：`32`。
- 每个查询之间等待：`5` 秒。

## 自动运行

`.github/workflows/flight-price-monitor.yml` 配置为每 3 小时运行一次，并支持手动触发。GitHub cron 按 UTC 触发；当前配置等价于北京时间 `01:17`、`04:17`、`07:17`、`10:17`、`13:17`、`16:17`、`19:17`、`22:17` 附近运行，但共享调度可能延迟或丢任务。

需要在仓库 Secrets 中配置：

- `PUSHPLUS_TOKEN`：PushPlus 用户 token 或消息 token。

如需发送给群组，在仓库 Variables 中配置：

- `PUSHPLUS_TOPIC`：PushPlus 一对多消息的群组编码。留空则只发送给自己。

可选环境变量已经在 workflow 中固定：

- `PUSHPLUS_CHANNEL=wechat`
- `PUSHPLUS_TEMPLATE=markdown`

## 推送规则

- 只在价格低于阈值时发送微信提醒。
- 首次跌破阈值会提醒。
- 持续低于同一价格不会重复刷屏。
- 出现更低价格会再次提醒。
- 价格升回阈值以上后，下一次再跌破会重新提醒。
- 如果 PushPlus 发送失败，本次 alert 标记会回滚，下一轮继续尝试，避免漏报。

## 数据源故障提醒

监测器也会监控数据源自身是否失效。完整一轮应查询 `32` 个组合；如果候选数低于 `5`，或错误率达到 `90%` 以上，记为一次数据源异常。

默认规则：

- 连续 `3` 次数据源异常后，通过 PushPlus 发送故障提醒。
- 如果异常持续，之后每连续异常 `6` 次再提醒一次，避免刷屏。
- 一旦候选数和错误率恢复到健康范围，连续异常计数会清零。
- 有限测试运行，例如 `--limit-queries 1`，不会计入健康告警。

这些阈值在 `config.json` 的 `health_alerts` 中配置。

## 历史记录

每轮正式运行会向 `history.jsonl` 追加一行摘要，包括运行时间、查询数、候选数、错误数、健康状态、各组最低价和推送结果。这个文件用于观察长期趋势，避免每次都从 git 历史里手动翻 `last_run.json`。

## 手动测试

安装依赖：

```bash
python -m pip install -r requirements.txt
```

只跑 1 个查询验证依赖和数据源：

```bash
python flight_monitor.py --limit-queries 1 --json --no-state-update
```

手动触发 GitHub Actions 并强制发送一次摘要：

```bash
gh workflow run flight-price-monitor.yml -f notify_every_run=true
```

设置群组编码：

```bash
gh variable set PUSHPLUS_TOPIC --repo MAXMAN2323/flight-price-monitor --body "your-topic-code"
```

## 风险边界

当前数据源是 `fast-flights` 对 Google Flights 的非官方查询，不是携程官方 API，也不是有 SLA 的付费接口。公共 GitHub runner 的云 IP 可能比个人电脑更容易遇到查询失败。若 `last_run.json` 中 `errors` 长期接近 `query_count`，应降低频率、增加 `sleep_seconds_between_queries`，或接入正式 API。

GitHub Actions 的 schedule 是共享调度，可能延迟或丢任务；公开仓库 60 天无活动时，定时 workflow 可能被自动禁用。
