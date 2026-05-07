# Remote Knot Batch Integration

当前通道不支持 thread-bound 持久 subagent runtime，因此批量 runner 采用：

1. 主脚本生成每个 symbol 的 prompt payload
2. 由 OpenClaw 会话层对每个 symbol 发起一次性真实子会话
3. 收回 JSON-only 结果
4. 做 schema 校验
5. 落盘到：
   - state/runs/knot_agent_raw_output_hk.json
   - state/runs/knot_agent_intraday_decision_hk.json
6. 校验失败再 fallback

备注：仓库内 Python 代码不直接持有 sessions_spawn SDK，因此真实远程会话调用由 OpenClaw 编排层完成；仓库内保留 prompt 构建、schema 校验、raw/parsed 落盘格式。
