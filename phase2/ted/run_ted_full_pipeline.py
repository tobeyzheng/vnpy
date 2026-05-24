#!/usr/bin/env python3
"""
TED完整流水线入口脚本 - 方案C
按顺序执行Knot四维研究、候选准备和TED进攻型标池发现
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

from phase2.ted.result_store import resolve_ted_run_dir, update_run_manifest


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


def run_step(step_name, script_path, description, run_dir: str | None = None):
    """运行单个步骤"""
    logger = logging.getLogger("ted_pipeline")

    logger.info(f"开始步骤: {step_name}")
    print(f"\n🎯 {description}")

    if not os.path.exists(script_path):
        logger.error(f"脚本不存在: {script_path}")
        return False

    try:
        cmd = ["python3", script_path]
        if run_dir:
            cmd.extend(["--run-dir", run_dir])
        logger.info(f"执行命令: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            logger.info(f"步骤完成: {step_name}")
            if result.stdout:
                logger.info(result.stdout)
            print(f"✅ {step_name} 完成")
            return True

        logger.error(f"步骤失败: {step_name}, 错误: {result.stderr}")
        print(f"❌ {step_name} 失败")
        return False

    except Exception as e:
        logger.error(f"步骤异常: {step_name}, 错误: {e}")
        print(f"❌ {step_name} 异常: {e}")
        return False


def run_ted_discovery(run_dir: str | None = None):
    """运行TED进攻型标池发现"""
    logger = logging.getLogger("ted_pipeline")
    logger.info("开始TED进攻型标池发现...")

    try:
        from phase2.ted.run_ted_discovery import run_ted_discovery as run_ted_stage3

        success, report_file, pool_config_file, resolved_run_dir = run_ted_stage3(run_dir=run_dir)
        if not success:
            return False, None, None, None

        logger.info(f"TED发现完成，报告保存至: {report_file}")
        return True, report_file, pool_config_file, resolved_run_dir

    except Exception as e:
        logger.error(f"TED发现失败: {e}")
        return False, None, None, None


def build_parser():
    parser = argparse.ArgumentParser(description="TED full pipeline helper")
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Optional TED dated run directory. Defaults to state/runs/ted/YYYYMMDDTHHMMSS.",
    )
    return parser


def main():
    """主函数"""
    setup_logging()
    logger = logging.getLogger("ted_pipeline_main")
    args = build_parser().parse_args()
    resolved_run_dir = str(resolve_ted_run_dir(args.run_dir))

    logger.info("=== TED完整流水线启动 ===")
    print("\n🚀 TED完整流水线启动 (方案C)")
    print("=" * 50)
    print("📋 流程概览:")
    print("1. Knot四维研究召回")
    print("2. 候选准备召回")
    print("3. TED进攻型标池发现")
    print(f"📁 TED归档目录: {resolved_run_dir}")
    print("=" * 50)

    start_time = datetime.now()
    update_run_manifest(
        resolved_run_dir,
        pipeline={
            "status": "running",
            "started_at": start_time.isoformat(),
        },
    )

    try:
        step1_success = run_step(
            "Knot四维研究召回",
            "/projects/vnpy/phase2/ted/run_knot_4dim_research.py",
            "步骤1/3: Knot四维研究召回 - 发现强趋势候选标的",
            run_dir=resolved_run_dir,
        )

        if not step1_success:
            logger.error("步骤1失败，停止流水线")
            update_run_manifest(resolved_run_dir, pipeline={"status": "failed", "failed_stage": "stage1"})
            print("\n❌ 流水线因步骤1失败而停止")
            sys.exit(1)

        step2_success = run_step(
            "候选准备召回",
            "/projects/vnpy/phase2/ted/run_candidate_preparation.py",
            "步骤2/3: 候选准备召回 - 结合Knot种子和市场数据",
            run_dir=resolved_run_dir,
        )

        if not step2_success:
            logger.error("步骤2失败，停止流水线")
            update_run_manifest(resolved_run_dir, pipeline={"status": "failed", "failed_stage": "stage2"})
            print("\n❌ 流水线因步骤2失败而停止")
            sys.exit(1)

        print("\n🎯 步骤3/3: TED进攻型标池发现 - 生成进攻型标池")
        step3_success, report_file, pool_config_file, final_run_dir = run_ted_discovery(run_dir=resolved_run_dir)

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds() / 60

        if step3_success:
            logger.info("=== TED完整流水线完成 ===")
            update_run_manifest(
                final_run_dir or resolved_run_dir,
                pipeline={
                    "status": "ok",
                    "started_at": start_time.isoformat(),
                    "ended_at": end_time.isoformat(),
                    "duration_minutes": round(duration, 2),
                    "final_report_file": report_file,
                    "final_pool_config_file": pool_config_file,
                },
            )
            print("\n✅ TED完整流水线完成！")
            print(f"⏱️  总耗时: {duration:.1f} 分钟")
            print(f"📁 TED归档目录: {final_run_dir or resolved_run_dir}")
            print(f"📁 最终报告: {report_file}")
            print(f"📁 最终pool_config: {pool_config_file}")
            print("📋 日志文件: /projects/vnpy/log/ted_full_pipeline.log")
        else:
            logger.error("=== TED完整流水线失败 ===")
            update_run_manifest(resolved_run_dir, pipeline={"status": "failed", "failed_stage": "stage3"})
            print("\n❌ TED完整流水线失败，请检查日志")
            sys.exit(1)

    except Exception as e:
        logger.error(f"TED流水线异常: {e}")
        update_run_manifest(resolved_run_dir, pipeline={"status": "failed", "error": str(e)})
        print(f"\n❌ TED流水线异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()