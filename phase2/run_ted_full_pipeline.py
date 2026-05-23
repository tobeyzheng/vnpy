#!/usr/bin/env python3
"""
TED完整流水线入口脚本 - 方案C
按顺序执行Knot四维研究、候选准备和TED进攻型标池发现
"""

import os
import sys
import json
import logging
import subprocess
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def setup_logging():
    """设置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('/projects/vnpy/log/ted_full_pipeline.log')
        ]
    )


def run_step(step_name, script_path, description):
    """运行单个步骤"""
    logger = logging.getLogger("ted_pipeline")

    logger.info(f"开始步骤: {step_name}")
    print(f"\n🎯 {description}")

    if not os.path.exists(script_path):
        logger.error(f"脚本不存在: {script_path}")
        return False

    try:
        cmd = f"cd /projects/vnpy && python3 {script_path}"
        logger.info(f"执行命令: {cmd}")

        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            logger.info(f"步骤完成: {step_name}")
            print(f"✅ {step_name} 完成")
            return True
        else:
            logger.error(f"步骤失败: {step_name}, 错误: {result.stderr}")
            print(f"❌ {step_name} 失败")
            return False

    except Exception as e:
        logger.error(f"步骤异常: {step_name}, 错误: {e}")
        print(f"❌ {step_name} 异常: {e}")
        return False


def run_ted_discovery():
    """运行TED进攻型标池发现"""
    logger = logging.getLogger("ted_pipeline")
    logger.info("开始TED进攻型标池发现...")

    try:
        # 导入TED模块
        from phase2.trend_explosion_discovery import TrendExplosionDiscovery

        ted = TrendExplosionDiscovery()
        report = ted.discover_aggressive_pool()

        # 保存报告
        report_dir = "/projects/vnpy/state/runs"
        os.makedirs(report_dir, exist_ok=True)

        report_file = os.path.join(report_dir, "ted_aggressive_pool_report.json")
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"TED发现完成，报告保存至: {report_file}")

        # 打印摘要
        print("\n" + "="*60)
        print("🎯 TED进攻型标池发现报告")
        print("="*60)
        print(f"📅 生成时间: {report['generated_at']}")
        print(f"🎯 策略: {report['strategy']}")
        print(f"📊 总候选数: {report['pool_summary']['total_candidates']}")
        print(f"🔥 核心进攻池: {report['pool_summary']['core_pool_count']}只")
        print(f"👀 观察补位池: {report['pool_summary']['watch_pool_count']}只")
        print(f"💼 主题备用池: {report['pool_summary']['reserve_pool_count']}只")

        # 核心池详情
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

        print("="*60)

        return True

    except Exception as e:
        logger.error(f"TED发现失败: {e}")
        return False


def main():
    """主函数"""
    setup_logging()
    logger = logging.getLogger("ted_pipeline_main")

    logger.info("=== TED完整流水线启动 ===")
    print("\n🚀 TED完整流水线启动 (方案C)")
    print("="*50)
    print("📋 流程概览:")
    print("1. Knot四维研究召回")
    print("2. 候选准备召回")
    print("3. TED进攻型标池发现")
    print("="*50)

    start_time = datetime.now()

    try:
        # 步骤1: Knot四维研究召回
        step1_success = run_step(
            "Knot四维研究召回",
            "/projects/vnpy/phase2/run_knot_4dim_research.py",
            "步骤1/3: Knot四维研究召回 - 发现强趋势候选标的"
        )

        if not step1_success:
            logger.error("步骤1失败，停止流水线")
            print("\n❌ 流水线因步骤1失败而停止")
            return

        # 步骤2: 候选准备召回
        step2_success = run_step(
            "候选准备召回",
            "/projects/vnpy/phase2/run_candidate_preparation.py",
            "步骤2/3: 候选准备召回 - 结合Knot种子和市场数据"
        )

        if not step2_success:
            logger.error("步骤2失败，停止流水线")
            print("\n❌ 流水线因步骤2失败而停止")
            return

        # 步骤3: TED进攻型标池发现
        print("\n🎯 步骤3/3: TED进攻型标池发现 - 生成进攻型标池")
        step3_success = run_ted_discovery()

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds() / 60

        if step3_success:
            logger.info("=== TED完整流水线完成 ===")
            print("\n✅ TED完整流水线完成！")
            print(f"⏱️  总耗时: {duration:.1f} 分钟")
            print("📁 最终报告: /projects/vnpy/state/runs/ted_aggressive_pool_report.json")
            print("📋 日志文件: /projects/vnpy/log/ted_full_pipeline.log")
        else:
            logger.error("=== TED完整流水线失败 ===")
            print("\n❌ TED完整流水线失败，请检查日志")

    except Exception as e:
        logger.error(f"TED流水线异常: {e}")
        print(f"\n❌ TED流水线异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()