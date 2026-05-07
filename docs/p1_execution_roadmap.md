# P1 实施路线图

## 目标
1. 远程 knot 批量结果可写回主流程文件
2. Futu 模拟盘与本地账本对账闭环
3. 风控更严格接入执行层
4. raw_score 统一在主流程生产/消费

## 子任务
- T1: remote knot batch writeback runner
- T2: candidate dynamic writeback pipeline
- T3: futu position reconciliation auto-fix hooks
- T4: sell quantity clamp by futu can_sell_qty
- T5: risk guard before order submission
- T6: report/brief update with reconciliation and remote-ai sections
