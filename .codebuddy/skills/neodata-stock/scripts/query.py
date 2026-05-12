#!/usr/bin/env python3
"""NeoData 股票/指数/板块数据查询客户端

Usage:
    python query.py --query "腾讯控股今日主力资金净流入"
    python query.py --query "沪深300最新行情"
    python query.py --query "创业板指近一个月走势" --sub-channel my_channel
"""

import argparse
import json
import os
import sys
import uuid

try:
    import requests
except ImportError:
    print("需要安装 requests: pip install requests", file=sys.stderr)
    sys.exit(1)

BASE_URL = "https://lily-pre.woa.com/neodata/stock"


def query_neodata_stock(
    query: str,
    sub_channel: str = "openclaw",
    request_id: str | None = None,
) -> dict:
    url = BASE_URL
    headers = {
        "Content-Type": "application/json",
    }
    payload = {
        "channel": "neodata",
        "sub_channel": sub_channel,
        "query": query,
        "request_id": request_id or uuid.uuid4().hex,
        "se_params": {},
        "extra_params": {},
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="NeoData 股票/指数/板块数据查询")
    parser.add_argument("--query", "-q", required=True, help="自然语言查询")
    parser.add_argument("--sub-channel", "-s", default=os.getenv("NEODATA_SUB_CHANNEL", "openclaw"), help="子渠道 (默认: openclaw)")
    parser.add_argument("--request-id", default=None, help="请求ID (默认自动生成)")

    args = parser.parse_args()

    try:
        result = query_neodata_stock(
            query=args.query,
            sub_channel=args.sub_channel,
            request_id=args.request_id,
        )
    except requests.RequestException as e:
        print(f"请求失败: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
