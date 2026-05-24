#!/usr/bin/env python3
"""
Trend Explosion Discovery (TED) 入口脚本
进攻型标池发现 - 专门寻找类似闪迪、美光等强趋势暴涨标的
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from phase2.ted.result_store import (
    DEFAULT_BASE_PATH,
    DEFAULT_COMPAT_POOL_CONFIG,
    DEFAULT_COMPAT_REPORT,
    render_pool_config_yaml,
    resolve_ted_run_dir,
    stage_file_path,
    update_run_manifest,
    write_text,
)
from phase2.ted.trend_explosion_discovery import TrendExplosionDiscovery


def setup_logging():
    """设置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('/projects/vnpy/log/ted_discovery.log')
        ]
    )


def run_knot_4dim_research(run_dir: str | None = None):
    """运行Knot四维研究召回"""
    logger = logging.getLogger("ted_knot")
    logger.info("开始Knot四维研究召回...")

    try:
        from phase2.ted.run_knot_4dim_research import run_knot_4dim_research as run_stage1

        success, result_file = run_stage1(run_dir=run_dir)
        if success:
            logger.info(f"Knot四维研究召回完成: {result_file}")
            return True
        logger.warning("Knot四维研究脚本执行失败")
        return False

    except Exception as e:
        logger.error(f"Knot四维研究失败: {e}")
        return False


def run_candidate_preparation(run_dir: str | None = None):
    """运行动态候选准备"""
    logger = logging.getLogger("ted_candidate")
    logger.info("开始动态候选准备...")

    try:
        from phase2.ted.run_candidate_preparation import run_candidate_preparation as run_stage2

        success, stage_summary = run_stage2(run_dir=run_dir)
        if success:
            logger.info(f"动态候选准备完成: {stage_summary.get('archived_dynamic', stage_summary.get('dynamic_file'))}")
            return True
        logger.warning("候选准备脚本执行失败")
        return False

    except Exception as e:
        logger.error(f"动态候选准备失败: {e}")
        return False


def _load_categorized_pool_from_report(report: dict) -> dict:
    categorized = {
        "core_pool": [],
        "watch_pool": [],
        "reserve_pool": [],
    }
    for pool_name in categorized.keys():
        for item in report.get(pool_name, []) or []:
            categorized[pool_name].append(
                {
                    "symbol": item.get("symbol", ""),
                    "name": item.get("name", ""),
                    "theme_bucket": item.get("theme_bucket", "general"),
                    "quote": {
                        "market_cap": item.get("market_cap", 0),
                    },
                }
            )
    return categorized


def run_ted_discovery(run_dir: str | None = None):
    """运行进攻型标池发现"""
    logger = logging.getLogger("ted_main")

    run_knot_4dim_research(run_dir=run_dir)
    run_candidate_preparation(run_dir=run_dir)

    logger.info("开始进攻型标池发现...")

    try:
        resolved_run_dir = resolve_ted_run_dir(run_dir)
        ted = TrendExplosionDiscovery()
        report = ted.discover_aggressive_pool()

        report_file = stage_file_path(resolved_run_dir, "stage3_ted_discovery", "ted_aggressive_pool_report.json")
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        categorized_pool = _load_categorized_pool_from_report(report)
        pool_config_content = render_pool_config_yaml(categorized_pool)
        pool_config_file = stage_file_path(resolved_run_dir, "stage3_ted_discovery", "pool_config.yaml")
        write_text(pool_config_file, pool_config_content)

        compat_report_file = os.path.join(DEFAULT_BASE_PATH, DEFAULT_COMPAT_REPORT)
        os.makedirs(os.path.dirname(compat_report_file), exist_ok=True)
        with open(compat_report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        compat_pool_config_file = os.path.join(DEFAULT_BASE_PATH, DEFAULT_COMPAT_POOL_CONFIG)
        write_text(compat_pool_config_file, pool_config_content)

        update_run_manifest(
            resolved_run_dir,
            stage3={
                "status": "ok",
                "report_file": str(report_file),
                "pool_config_file": str(pool_config_file),
                "compat_report_file": compat_report_file,
                "compat_pool_config_file": compat_pool_config_file,
                "pool_summary": report.get("pool_summary", {}),
            },
        )

        logger.info(f"进攻型标池发现完成，报告保存至: {report_file}")
        logger.info(f"TED pool_config 已保存至: {pool_config_file}")

        print("\n" + "=" * 60)
        print("🎯 进攻型标池发现报告")
        print("=" * 60)
        print(f"📅 生成时间: {report['generated_at']}")
        print(f"🎯 策略: {report['strategy']}")
        print(f"📊 总候选数: {report['pool_summary']['total_candidates']}")
        print(f"🔥 核心进攻池: {report['pool_summary']['core_pool_count']}只")
        print(f"👀 观察补位池: {report['pool_summary']['watch_pool_count']}只")
        print(f"💼 主题备用池: {report['pool_summary']['reserve_pool_count']}只")

        if report['core_pool']:
            print("\n🔥 核心进攻池详情:")
            print("-" * 40)
            for i, stock in enumerate(report['core_pool'], 1):
                print(f"{i}. {stock['symbol']} - {stock['name']}")
                print(f"   📈 AEOS评分: {stock['aeos_score']:.3f}")
                print(f"   💹 涨幅: {stock['change_pct']:.1f}%")
                print(f"   💰 市值: {stock['market_cap']/1e9:.1f}B")
                print(f"   📊 成交额: {stock['turnover']/1e6:.1f}M")
                print()

        if report['watch_pool']:
            print("👀 观察补位池:")
            watch_symbols = [stock['symbol'] for stock in report['watch_pool'][:5]]
            print(f"   {', '.join(watch_symbols)}")

        print(f"📁 TED归档目录: {resolved_run_dir}")
        print(f"📁 阶段3报告: {report_file}")
        print(f"📁 阶段3 pool_config: {pool_config_file}")
        print("=" * 60)
        return True, str(report_file), str(pool_config_file), str(resolved_run_dir)

    except Exception as e:
        logger.error(f"进攻型标池发现失败: {e}")
        return False, None, None, None


def build_parser():
    parser = argparse.ArgumentParser(description="TED aggressive pool discovery helper")
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Optional TED dated run directory. Defaults to state/runs/ted/YYYYMMDDTHHMMSS.",
    )
    return parser


def main():
    """主函数"""
    setup_logging()
    logger = logging.getLogger("ted_main")
    args = build_parser().parse_args()

    logger.info("=== Trend Explosion Discovery 启动 ===")

    try:
        success, report_file, pool_config_file, run_dir = run_ted_discovery(run_dir=args.run_dir)

        if success:
            logger.info("=== TED发现流程完成 ===")
            print("\n✅ 进攻型标池发现流程完成！")
            print(f"📁 TED归档目录: {run_dir}")
            print(f"📁 报告文件: {report_file}")
            print(f"📁 pool_config文件: {pool_config_file}")
            print(f"📁 兼容报告镜像: /projects/vnpy/{DEFAULT_COMPAT_REPORT}")
            print(f"📁 兼容pool_config镜像: /projects/vnpy/{DEFAULT_COMPAT_POOL_CONFIG}")
            print("📋 日志文件: /projects/vnpy/log/ted_discovery.log")
        else:
            logger.error("=== TED发现流程失败 ===")
            print("\n❌ 进攻型标池发现流程失败，请检查日志")
            sys.exit(1)

    except Exception as e:
        logger.error(f"TED流程异常: {e}")
        print(f"\n❌ TED流程异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()