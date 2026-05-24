#!/usr/bin/env python3
"""
Knot四维研究召回入口脚本 - 方案C第一步
专门用于运行US四维Knot研究，发现强趋势候选标的
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from phase2.ted.result_store import resolve_ted_run_dir, stage_file_path, update_run_manifest


def setup_logging():
    """设置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('/projects/vnpy/log/knot_4dim_research.log')
        ]
    )


def _extract_dimensions(data):
    """兼容新旧两种Knot结果结构"""
    dimensions = data.get("dimensions")
    if isinstance(dimensions, dict):
        return dimensions
    return {
        "technical": data.get("technical", []),
        "fundamental": data.get("fundamental", []),
        "capital_flow": data.get("capital_flow", []),
        "event_driven": data.get("event_driven", []),
    }


def validate_knot_setup():
    """轻量校验Knot脚本是否可用，不触发远端调用"""
    logger = logging.getLogger("knot_4dim")
    knot_script = "/projects/vnpy/scripts/quant_workflow/run_knot_4dim_picks_us.py"

    if not os.path.exists(knot_script):
        logger.error(f"Knot四维研究脚本不存在: {knot_script}")
        return False

    cmd = ["python3", knot_script, "--help"]
    logger.info("开始Knot轻量校验（--help）")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"Knot轻量校验失败: {result.stderr}")
        return False

    logger.info("Knot轻量校验通过")
    logger.info(result.stdout.strip())
    return True


def run_knot_4dim_research(run_dir: str | None = None):
    """运行Knot四维研究召回"""
    logger = logging.getLogger("knot_4dim")
    logger.info("开始Knot四维研究召回...")

    try:
        knot_script = "/projects/vnpy/scripts/quant_workflow/run_knot_4dim_picks_us.py"

        if not os.path.exists(knot_script):
            logger.error(f"Knot四维研究脚本不存在: {knot_script}")
            return False, None

        resolved_run_dir = resolve_ted_run_dir(run_dir)
        result_file = stage_file_path(resolved_run_dir, "stage1_knot_4dim", "knot_4dim_us.json")

        cmd = ["python3", knot_script, "--output", str(result_file)]
        logger.info(f"执行命令: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            logger.info("Knot四维研究召回完成")
            if result.stdout:
                logger.info(f"输出: {result.stdout}")

            if os.path.exists(result_file):
                with open(result_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                dimensions = _extract_dimensions(data)
                logger.info(
                    "Knot研究结果: %s技术 + %s基本面 + %s资金流 + %s事件驱动",
                    len(dimensions.get("technical", [])),
                    len(dimensions.get("fundamental", [])),
                    len(dimensions.get("capital_flow", [])),
                    len(dimensions.get("event_driven", [])),
                )
                update_run_manifest(
                    resolved_run_dir,
                    stage1={
                        "status": "ok",
                        "result_file": str(result_file),
                        "dimensions": {
                            "technical": len(dimensions.get("technical", [])),
                            "fundamental": len(dimensions.get("fundamental", [])),
                            "capital_flow": len(dimensions.get("capital_flow", [])),
                            "event_driven": len(dimensions.get("event_driven", [])),
                        },
                    },
                )
            else:
                logger.warning(f"未在预期路径发现结果文件: {result_file}")

            return True, str(result_file)

        logger.error(f"Knot四维研究失败: {result.stderr}")
        return False, None

    except Exception as e:
        logger.error(f"Knot四维研究异常: {e}")
        return False, None


def build_parser():
    parser = argparse.ArgumentParser(description="TED Knot 4-dim research helper")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate Knot script availability and CLI without triggering remote research.",
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Optional TED dated run directory. Defaults to state/runs/ted/YYYYMMDDTHHMMSS.",
    )
    return parser


def main():
    """主函数"""
    setup_logging()
    logger = logging.getLogger("knot_4dim_main")
    args = build_parser().parse_args()

    logger.info("=== Knot四维研究召回启动 ===")

    if args.validate_only:
        print("\n🧪 开始Knot轻量校验")
        print("📋 校验内容: 脚本存在性 + CLI参数可用性")
        print("🚫 不会连接远端Knot，不会写研究结果")
        success = validate_knot_setup()
        if success:
            print("\n✅ Knot轻量校验通过！")
            print("📋 日志文件: /projects/vnpy/log/knot_4dim_research.log")
        else:
            print("\n❌ Knot轻量校验失败，请检查日志")
            sys.exit(1)
        return

    print("\n🎯 开始Knot四维研究召回 (方案C第一步)")
    print("📊 维度: 技术面、基本面、资金流、事件驱动")
    print("🎯 目标: 发现强趋势暴涨候选标的")

    try:
        success, result_file = run_knot_4dim_research(run_dir=args.run_dir)

        if success:
            logger.info("=== Knot四维研究召回完成 ===")
            print("\n✅ Knot四维研究召回完成！")
            print(f"📁 结果文件: {result_file}")
            print("📋 日志文件: /projects/vnpy/log/knot_4dim_research.log")
            print("\n➡️ 下一步: 运行候选准备召回")
        else:
            logger.error("=== Knot四维研究召回失败 ===")
            print("\n❌ Knot四维研究召回失败，请检查日志")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Knot四维研究流程异常: {e}")
        print(f"\n❌ Knot四维研究流程异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()