#!/usr/bin/env python3
"""
候选准备召回入口脚本 - 方案C第二步
专门用于运行动态候选准备，结合Knot种子和市场数据
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
            logging.FileHandler('/projects/vnpy/log/candidate_preparation.log')
        ]
    )


def run_candidate_preparation():
    """运行动态候选准备"""
    logger = logging.getLogger("candidate_prep")
    logger.info("开始动态候选准备...")

    try:
        # 调用现有的候选准备脚本
        prep_script = "/projects/vnpy/scripts/quant_workflow/run_prepare_candidate_inputs.py"

        if not os.path.exists(prep_script):
            logger.error(f"候选准备脚本不存在: {prep_script}")
            return False

        # 使用进攻型参数
        cmd = (
            f"cd /projects/vnpy && python3 {prep_script} "
            f"--market us "
            f"--strategy knot_first "
            f"--top-n 24 "
            f"--knot-target-count 36 "
            f"--universe-preset momentum_cta "
            f"--include-market-data"
        )
        logger.info(f"执行命令: {cmd}")

        # 执行命令
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            logger.info("动态候选准备完成")
            logger.info(f"输出: {result.stdout}")

            # 检查结果文件
            report_file = "/projects/vnpy/state/runs/candidate_inputs.prepare.report.us.json"
            dynamic_file = "/projects/vnpy/state/runs/candidate_inputs.dynamic.us.json"

            if os.path.exists(report_file):
                with open(report_file, 'r') as f:
                    report = json.load(f)
                logger.info(f"候选准备报告: {report.get('summary', 'N/A')}")

            if os.path.exists(dynamic_file):
                with open(dynamic_file, 'r') as f:
                    dynamic_data = json.load(f)
                logger.info(f"动态候选数: {len(dynamic_data.get('candidates', []))}")

            return True
        else:
            logger.error(f"动态候选准备失败: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"动态候选准备异常: {e}")
        return False


def check_knot_results():
    """检查Knot四维研究结果"""
    logger = logging.getLogger("candidate_prep")

    # 查找最新的Knot结果
    log_base = "/projects/vnpy/log"
    if not os.path.exists(log_base):
        logger.warning("Log目录不存在，跳过Knot结果检查")
        return None

    # 按时间倒序查找最新的Knot结果
    knot_dirs = []
    for dir_name in os.listdir(log_base):
        if len(dir_name) == 10 and dir_name.isdigit():  # YYYYMMDDHH格式
            knot_file = os.path.join(log_base, dir_name, "knot_4dim_us.json")
            if os.path.exists(knot_file):
                knot_dirs.append((dir_name, knot_file))

    if knot_dirs:
        # 按时间排序，取最新的
        knot_dirs.sort(reverse=True)
        latest_dir, latest_file = knot_dirs[0]

        try:
            with open(latest_file, 'r') as f:
                knot_data = json.load(f)

            total_candidates = (
                len(knot_data.get('technical', [])) +
                len(knot_data.get('fundamental', [])) +
                len(knot_data.get('capital_flow', [])) +
                len(knot_data.get('event_driven', []))
            )

            logger.info(f"发现Knot四维研究结果: {latest_dir}, 总候选数: {total_candidates}")
            return knot_data

        except Exception as e:
            logger.warning(f"读取Knot结果失败: {e}")

    return None


def main():
    """主函数"""
    setup_logging()
    logger = logging.getLogger("candidate_prep_main")

    logger.info("=== 候选准备召回启动 ===")
    print("\n🎯 开始候选准备召回 (方案C第二步)")
    print("📊 策略: knot_first + momentum_cta")
    print("🎯 目标: 结合Knot种子和市场数据生成动态候选")

    # 检查Knot结果
    knot_results = check_knot_results()
    if knot_results:
        print(f"📋 检测到Knot四维研究结果，将作为种子使用")
    else:
        print("⚠️  未检测到Knot四维研究结果，将使用默认候选")

    try:
        success = run_candidate_preparation()

        if success:
            logger.info("=== 候选准备召回完成 ===")
            print("\n✅ 候选准备召回完成！")
            print("📁 报告文件: /projects/vnpy/state/runs/candidate_inputs.prepare.report.us.json")
            print("📁 候选文件: /projects/vnpy/state/runs/candidate_inputs.dynamic.us.json")
            print("📋 日志文件: /projects/vnpy/log/candidate_preparation.log")
            print("\n➡️ 下一步: 运行TED进攻型标池发现")
        else:
            logger.error("=== 候选准备召回失败 ===")
            print("\n❌ 候选准备召回失败，请检查日志")

    except Exception as e:
        logger.error(f"候选准备流程异常: {e}")
        print(f"\n❌ 候选准备流程异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()