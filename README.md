# Flight Price Monitor

公开 GitHub Actions 机票价格监测器。每小时运行一次，监测到低于阈值的票价后，通过 PushPlus 推送到微信。

## 当前监测条件

- 北京到泰国：`PEK`、`PKX` 到 `BKK`、`DMK`、`CNX`，往返，低于 `3000 CNY` 提醒。
- 北京到西班牙：`PEK`、`PKX` 到 `MAD`、`BCN`、`AGP`、`VLC`，往返，低于 `6000 CNY` 提醒。
- 出发日期：`2026-09-25` 或 `2026-09-26`。
- 返回日期：`2026-10-06` 或 `2026-10-07`。
- 每轮查询组合：`56`。
- 每个查询之间等待：`5` 秒。

## 自动运行

`.github/workflows/flight-price-monitor.yml` 配置为每小时第 17 分钟运行一次，并支持手动触发。

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
