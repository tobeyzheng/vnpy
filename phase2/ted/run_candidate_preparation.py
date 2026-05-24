#!/usr/bin/env python3
"""
候选准备召回入口脚本 - 方案C第二步
专门用于运行动态候选准备，结合Knot种子和市场数据
"""

import argparse
import json
import logging
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from phase2.ted.result_store import copy_artifact, resolve_ted_run_dir, stage_file_path, update_run_manifest


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


def run_candidate_preparation(run_dir: str | None = None):
    """运行动态候选准备"""
    logger = logging.getLogger("candidate_prep")
    logger.info("开始动态候选准备...")

    try:
        prep_script = "/projects/vnpy/scripts/quant_workflow/run_prepare_candidate_inputs.py"

        if not os.path.exists(prep_script):
            logger.error(f"候选准备脚本不存在: {prep_script}")
            return False, {}

        cmd = [
            "python3",
            prep_script,
            "--market", "us",
            "--strategy", "knot_first",
            "--top-n", "24",
            "--knot-target-count", "36",
            "--universe-preset", "momentum_cta",
            "--include-market-data",
        ]
        logger.info(f"执行命令: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            logger.info("动态候选准备完成")
            if result.stdout:
                logger.info(f"输出: {result.stdout}")

            report_file = "/projects/vnpy/state/runs/candidate_inputs.prepare.report.us.json"
            dynamic_file = "/projects/vnpy/state/runs/candidate_inputs.dynamic.us.json"
            stage_summary = {
                "report_file": report_file,
                "dynamic_file": dynamic_file,
            }

            resolved_run_dir = resolve_ted_run_dir(run_dir)
            archived_report = None
            archived_dynamic = None

            if os.path.exists(report_file):
                with open(report_file, 'r', encoding='utf-8') as f:
                    report = json.load(f)
                logger.info(f"候选准备报告状态: {report.get('status', 'N/A')}")
                archived_report = copy_artifact(
                    report_file,
                    stage_file_path(resolved_run_dir, "stage2_candidate_preparation", "candidate_inputs.prepare.report.us.json"),
                )
                stage_summary["report"] = report

            if os.path.exists(dynamic_file):
                with open(dynamic_file, 'r', encoding='utf-8') as f:
                    dynamic_data = json.load(f)
                logger.info(f"动态候选数: {len(dynamic_data.get('items', []))}")
                archived_dynamic = copy_artifact(
                    dynamic_file,
                    stage_file_path(resolved_run_dir, "stage2_candidate_preparation", "candidate_inputs.dynamic.us.json"),
                )
                stage_summary["dynamic_item_count"] = len(dynamic_data.get("items", []))

            stage_summary["archived_report"] = str(archived_report) if archived_report else None
            stage_summary["archived_dynamic"] = str(archived_dynamic) if archived_dynamic else None
            update_run_manifest(
                resolved_run_dir,
                stage2={
                    "status": "ok",
                    "report_file": str(archived_report) if archived_report else report_file,
                    "dynamic_file": str(archived_dynamic) if archived_dynamic else dynamic_file,
                    "dynamic_item_count": stage_summary.get("dynamic_item_count", 0),
                },
            )

            return True, stage_summary

        logger.error(f"动态候选准备失败: {result.stderr}")
        return False, {}

    except Exception as e:
        logger.error(f"动态候选准备异常: {e}")
        return False, {}


def check_knot_results(run_dir: str | None = None):
    """检查Knot四维研究结果"""
    logger = logging.getLogger("candidate_prep")
    resolved_run_dir = resolve_ted_run_dir(run_dir, create=False)
    ted_knot_file = stage_file_path(resolved_run_dir, "stage1_knot_4dim", "knot_4dim_us.json", create_parent=False)
    candidate_files = []

    if ted_knot_file.exists():
        candidate_files.append((ted_knot_file.parent.parent.name, str(ted_knot_file)))

    log_base = "/projects/vnpy/log"
    if os.path.exists(log_base):
        for dir_name in os.listdir(log_base):
            if len(dir_name) == 10 and dir_name.isdigit():
                knot_file = os.path.join(log_base, dir_name, "knot_4dim_us.json")
                if os.path.exists(knot_file):
                    candidate_files.append((dir_name, knot_file))

    if candidate_files:
        candidate_files.sort(reverse=True)
        latest_dir, latest_file = candidate_files[0]

        try:
            with open(latest_file, 'r', encoding='utf-8') as f:
                knot_data = json.load(f)

            dimensions = _extract_dimensions(knot_data)
            total_candidates = (
                len(dimensions.get("technical", [])) +
                len(dimensions.get("fundamental", [])) +
                len(dimensions.get("capital_flow", [])) +
                len(dimensions.get("event_driven", []))
            )

            logger.info(f"发现Knot四维研究结果: {latest_dir}, 总候选数: {total_candidates}")
            return knot_data

        except Exception as e:
            logger.warning(f"读取Knot结果失败: {e}")

    return None


def build_parser():
    parser = argparse.ArgumentParser(description="TED candidate preparation helper")
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Optional TED dated run directory. Defaults to state/runs/ted/YYYYMMDDTHHMMSS.",
    )
    return parser


def main():
    """主函数"""
    setup_logging()
    logger = logging.getLogger("candidate_prep_main")
    args = build_parser().parse_args()

    logger.info("=== 候选准备召回启动 ===")
    print("\n🎯 开始候选准备召回 (方案C第二步)")
    print("📊 策略: knot_first + momentum_cta")
    print("🎯 目标: 结合Knot种子和市场数据生成动态候选")

    knot_results = check_knot_results(run_dir=args.run_dir)
    if knot_results:
        print("📋 检测到Knot四维研究结果，将作为同批次TED研究参考")
    else:
        print("⚠️  未检测到Knot四维研究结果，将使用默认候选")

    try:
        success, stage_summary = run_candidate_preparation(run_dir=args.run_dir)

        if success:
            logger.info("=== 候选准备召回完成 ===")
            print("\n✅ 候选准备召回完成！")
            print(f"📁 归档报告: {stage_summary.get('archived_report', stage_summary.get('report_file'))}")
            print(f"📁 归档候选: {stage_summary.get('archived_dynamic', stage_summary.get('dynamic_file'))}")
            print("📋 日志文件: /projects/vnpy/log/candidate_preparation.log")
            print("\n➡️ 下一步: 运行TED进攻型标池发现")
        else:
            logger.error("=== 候选准备召回失败 ===")
            print("\n❌ 候选准备召回失败，请检查日志")
            sys.exit(1)

    except Exception as e:
        logger.error(f"候选准备流程异常: {e}")
        print(f"\n❌ 候选准备流程异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()