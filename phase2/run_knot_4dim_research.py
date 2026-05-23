#!/usr/bin/env python3
"""
Knot四维研究召回入口脚本 - 方案C第一步
专门用于运行US四维Knot研究，发现强趋势候选标的
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
            logging.FileHandler('/projects/vnpy/log/knot_4dim_research.log')
        ]
    )


def run_knot_4dim_research():
    """运行Knot四维研究召回"""
    logger = logging.getLogger("knot_4dim")
    logger.info("开始Knot四维研究召回...")

    try:
        # 调用现有的Knot四维研究脚本
        knot_script = "/projects/vnpy/scripts/quant_workflow/run_knot_4dim_picks_us.py"

        if not os.path.exists(knot_script):
            logger.error(f"Knot四维研究脚本不存在: {knot_script}")
            return False

        # 创建日期目录
        date_str = datetime.now().strftime("%Y%m%d%H")
        log_dir = f"/projects/vnpy/log/{date_str}"
        os.makedirs(log_dir, exist_ok=True)

        # 运行Knot研究
        cmd = f"cd /projects/vnpy && python3 {knot_script}"
        logger.info(f"执行命令: {cmd}")

        # 执行命令
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            logger.info("Knot四维研究召回完成")
            logger.info(f"输出: {result.stdout}")

            # 检查结果文件
            result_file = os.path.join(log_dir, "knot_4dim_us.json")
            if os.path.exists(result_file):
                with open(result_file, 'r') as f:
                    data = json.load(f)
                logger.info(f"Knot研究结果: {len(data.get('technical', []))}技术 + {len(data.get('fundamental', []))}基本面 + {len(data.get('capital_flow', []))}资金流 + {len(data.get('event_driven', []))}事件驱动")

            return True
        else:
            logger.error(f"Knot四维研究失败: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"Knot四维研究异常: {e}")
        return False


def main():
    """主函数"""
    setup_logging()
    logger = logging.getLogger("knot_4dim_main")

    logger.info("=== Knot四维研究召回启动 ===")
    print("\n🎯 开始Knot四维研究召回 (方案C第一步)")
    print("📊 维度: 技术面、基本面、资金流、事件驱动")
    print("🎯 目标: 发现强趋势暴涨候选标的")

    try:
        success = run_knot_4dim_research()

        if success:
            logger.info("=== Knot四维研究召回完成 ===")
            print("\n✅ Knot四维研究召回完成！")
            print("📁 结果文件: /projects/vnpy/log/YYYYMMDDHH/knot_4dim_us.json")
            print("📋 日志文件: /projects/vnpy/log/knot_4dim_research.log")
            print("\n➡️ 下一步: 运行候选准备召回")
        else:
            logger.error("=== Knot四维研究召回失败 ===")
            print("\n❌ Knot四维研究召回失败，请检查日志")

    except Exception as e:
        logger.error(f"Knot四维研究流程异常: {e}")
        print(f"\n❌ Knot四维研究流程异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()