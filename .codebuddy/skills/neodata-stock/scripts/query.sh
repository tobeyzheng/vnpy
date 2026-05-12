#!/usr/bin/env bash
# NeoData 股票/指数/板块数据查询 - curl 封装
#
# Usage:
#   bash query.sh "腾讯控股今日主力资金净流入"
#
# 环境变量:
#   NEODATA_SUB_CHANNEL - 子渠道名称 (默认: openclaw)

set -euo pipefail

BASE_URL="https://lily-pre.woa.com/neodata/stock"

QUERY="${1:?用法: bash query.sh <query>}"
SUB_CHANNEL="${NEODATA_SUB_CHANNEL:-openclaw}"

REQUEST_ID=$(python3 -c "import uuid; print(uuid.uuid4().hex)" 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null | tr -d '-' || echo "req-$$-$(date +%s)")

RESPONSE=$(curl --silent --show-error --location --max-time 30 --connect-timeout 10 \
    --write-out "\n%{http_code}" \
    "${BASE_URL}" \
    --header "Content-Type: application/json" \
    --data "$(cat <<EOF
{
    "channel": "neodata",
    "sub_channel": "${SUB_CHANNEL}",
    "query": "${QUERY}",
    "request_id": "${REQUEST_ID}",
    "se_params": {},
    "extra_params": {}
}
EOF
)")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [[ "$HTTP_CODE" -ne 200 ]]; then
    echo "请求失败: HTTP ${HTTP_CODE}" >&2
    [[ -n "$BODY" ]] && echo "$BODY" >&2
    exit 1
fi

echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY"
