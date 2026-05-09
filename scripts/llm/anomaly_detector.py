#!/usr/bin/env python3
"""
异动检测工具 - 集成各种异动检测技能
"""

import argparse
import sys
import os
from typing import Dict, Any, List

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from scripts.skills.handle_capital_anomaly import handle_capital_anomaly
    from scripts.skills.handle_technical_anomaly import handle_technical_anomaly
    from scripts.skills.handle_derivatives_anomaly import handle_derivatives_anomaly
except ImportError:
    print("警告: 无法导入异动检测技能模块")
    handle_capital_anomaly = None
    handle_technical_anomaly = None
    handle_derivatives_anomaly = None


def detect_anomalies(symbol: str, anomaly_types: List[str] = None) -> Dict[str, Any]:
    """
    检测股票的各种异动

    Args:
        symbol: 股票代码
        anomaly_types: 异动类型列表，可选 ['technical', 'capital', 'derivatives']

    Returns:
        包含各种异动信息的字典
    """
    if anomaly_types is None:
        anomaly_types = ['technical', 'capital', 'derivatives']

    anomalies = {}

    # 技术面异动
    if 'technical' in anomaly_types and handle_technical_anomaly:
        try:
            anomalies['technical'] = handle_technical_anomaly(symbol)
        except Exception as e:
            anomalies['technical'] = {'error': str(e)}

    # 资金面异动
    if 'capital' in anomaly_types and handle_capital_anomaly:
        try:
            anomalies['capital'] = handle_capital_anomaly(symbol)
        except Exception as e:
            anomalies['capital'] = {'error': str(e)}

    # 衍生品异动
    if 'derivatives' in anomaly_types and handle_derivatives_anomaly:
        try:
            anomalies['derivatives'] = handle_derivatives_anomaly(symbol)
        except Exception as e:
            anomalies['derivatives'] = {'error': str(e)}

    return anomalies


def format_anomalies_for_prompt(anomalies: Dict[str, Any]) -> str:
    """将异动信息格式化为提示词可用的文本"""
    prompt_sections = []

    # 技术面异动
    if 'technical' in anomalies:
        tech = anomalies['technical']
        if not tech.get('error'):
            section = "技术面异动检测:\n"
            for indicator, signal in tech.items():
                if indicator != 'error':
                    section += f"- {indicator}: {signal}\n"
            prompt_sections.append(section)

    # 资金面异动
    if 'capital' in anomalies:
        capital = anomalies['capital']
        if not capital.get('error'):
            section = "资金面异动检测:\n"
            for metric, value in capital.items():
                if metric != 'error':
                    section += f"- {metric}: {value}\n"
            prompt_sections.append(section)

    # 衍生品异动
    if 'derivatives' in anomalies:
        deriv = anomalies['derivatives']
        if not deriv.get('error'):
            section = "衍生品异动检测:\n"
            for metric, value in deriv.items():
                if metric != 'error':
                    section += f"- {metric}: {value}\n"
            prompt_sections.append(section)

    if not prompt_sections:
        return "暂无显著异动信号"

    return "\n".join(prompt_sections)


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description='股票异动检测工具')
    parser.add_argument('symbol', help='股票代码 (格式: US.TSLA, HK.00700)')
    parser.add_argument('--technical', '-t', action='store_true', help='检测技术面异动')
    parser.add_argument('--capital', '-c', action='store_true', help='检测资金面异动')
    parser.add_argument('--derivatives', '-d', action='store_true', help='检测衍生品异动')
    parser.add_argument('--all', '-a', action='store_true', help='检测所有类型异动')
    parser.add_argument('--format', '-f', action='store_true', help='输出为提示词格式')

    args = parser.parse_args()

    # 确定要检测的异动类型
    anomaly_types = []
    if args.all:
        anomaly_types = ['technical', 'capital', 'derivatives']
    else:
        if args.technical:
            anomaly_types.append('technical')
        if args.capital:
            anomaly_types.append('capital')
        if args.derivatives:
            anomaly_types.append('derivatives')

    if not anomaly_types:
        anomaly_types = ['technical', 'capital', 'derivatives']

    print(f"检测 {args.symbol} 的异动信号...")

    anomalies = detect_anomalies(args.symbol, anomaly_types)

    if args.format:
        formatted = format_anomalies_for_prompt(anomalies)
        print("\n异动检测结果 (提示词格式):")
        print("="*50)
        print(formatted)
    else:
        print("\n异动检测结果:")
        print("="*50)
        for anomaly_type, result in anomalies.items():
            print(f"{anomaly_type.upper()} 异动:")
            if 'error' in result:
                print(f"  错误: {result['error']}")
            else:
                for key, value in result.items():
                    print(f"  {key}: {value}")
            print()


if __name__ == "__main__":
    main()