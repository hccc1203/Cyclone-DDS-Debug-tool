"""Publish and inspect samples using Eclipse Cyclone DDS."""
import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from importlib import import_module

POLL_INTERVAL = 0.05
JSON_TYPE = "DDSDebug::JsonMessage"


def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("时间必须是有限正数")
    return seconds


def positive_count(value):
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError("数量必须大于 0")
    return count


def positive_frequency(value):
    hz = positive_seconds(value)
    if not math.isfinite(1 / hz):
        raise argparse.ArgumentTypeError("频率过低")
    return hz


def parse_args():
    parser = argparse.ArgumentParser(description="Cyclone DDS 调试工具")
    parser.add_argument("--domain", type=int, default=0, help="DDS Domain ID，默认 0")
    cmd = parser.add_subparsers(dest="command", required=True)

    listing = cmd.add_parser("list", help="发现当前 Domain 中的发布端")
    listing.add_argument("--timeout", type=positive_seconds, default=3.0)

    pub = cmd.add_parser("pub", help="以 DDSDebug::JsonMessage 类型发布 JSON 数据")
    pub.add_argument("topic")
    pub.add_argument("data", help="JSON 文本")
    pub.add_argument("--timeout", type=positive_seconds, default=5.0, help="等待匹配和确认的时限（秒）")
    pub.add_argument("--partition", default="", help="DDS Partition")
    pub.add_argument("--hz", type=positive_frequency, help="每秒发送次数，省略时只发送一次")

    sub = cmd.add_parser("sub", help="订阅并逐行显示 DDS 数据")
    sub.add_argument("topic")
    type_options = sub.add_mutually_exclusive_group()
    type_options.add_argument("--type", metavar="MODULE:CLASS", help="手动指定生成的 Python IDL 类型")
    type_options.add_argument("--json", action="store_true", help="直接订阅工具的 JSON 类型")
    sub.add_argument("--timeout", type=positive_seconds, default=10.0, help="自动发现发布端及类型的时限（秒）")
    sub.add_argument("--count", type=positive_count, help="收到指定数量后退出，默认持续接收")
    sub.add_argument("--partition", default="", help="DDS Partition")
    args = parser.parse_args()

    if not 0 <= args.domain < 2**32 - 1:
        parser.error("Domain ID 必须在 0 到 4294967294 之间")
    if args.command != "list" and not args.topic.strip():
        parser.error("话题名不能为空")
    if args.command == "pub":
        try:
            args.data = json.dumps(json.loads(args.data), ensure_ascii=False, allow_nan=False)
            args.data.encode("utf-8")
        except ValueError as error:
            parser.error(f"无效的 JSON 数据：{error}")
    if args.command == "sub" and args.type:
        module, sep, name = args.type.partition(":")
        if not sep or not module or not name:
            parser.error("--type 格式为 MODULE:CLASS")
        args.type = (module, name)
    return args


def json_value(value):
    if isinstance(value, Enum):
        return value.name
    if is_dataclass(value):
        return {field.name: json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, bytes, bytearray)):
        return [json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def run(args):
    from cyclonedds.builtin import BuiltinDataReader, BuiltinTopicDcpsPublication
    from cyclonedds.core import DDSException
    from cyclonedds.domain import DomainParticipant
    from cyclonedds.dynamic import get_types_for_typeid
    from cyclonedds.idl import IdlStruct, IdlUnion
    from cyclonedds.pub import DataWriter, Publisher
    from cyclonedds.qos import Policy, Qos
    from cyclonedds.sub import DataReader, Subscriber
    from cyclonedds.topic import Topic
    from cyclonedds.util import duration

    @dataclass
    class JsonMessage(IdlStruct, typename=JSON_TYPE):
        payload: str

    participant = DomainParticipant(args.domain)
    deadline = time.monotonic() + args.timeout

    if args.command == "pub":
        partition_qos = Qos(Policy.Partition([args.partition]))
        publisher = Publisher(participant, qos=partition_qos)
        topic = Topic(participant, args.topic, JsonMessage)
        writer = DataWriter(
            publisher, topic,
            qos=Qos(Policy.Reliability.Reliable(max_blocking_time=duration(seconds=1))),
        )
        if args.hz is not None:
            period = 1 / args.hz
            sent = 0
            next_send = next_report = time.monotonic()
            message = JsonMessage(args.data)
            while True:
                remaining = next_send - time.monotonic()
                if remaining > 0:
                    time.sleep(min(remaining, 1))
                    continue
                writer.write(message)
                sent += 1
                now = time.monotonic()
                if now >= next_report:
                    print(f"已发送 {sent} 条", file=sys.stderr, flush=True)
                    next_report = now + 0.5
                next_send += period
                if next_send <= now:
                    next_send = now + period

        while not writer.get_matched_subscriptions():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"未匹配到订阅端（话题 {args.topic}），请检查 Domain、Partition 和类型")
            time.sleep(min(POLL_INTERVAL, remaining))
        writer.write(JsonMessage(args.data))
        try:
            acknowledged = writer.wait_for_acks(duration(seconds=max(0, deadline - time.monotonic())))
        except AttributeError as error:
            # Cyclone DDS Python 11.0.1 uses the wrong exception class on timeout.
            if error.name != "DDS_RETCODE_TIMEOUT":
                raise
            acknowledged = False
        if not acknowledged:
            raise TimeoutError("样本已写入，但等待可靠订阅端确认超时")
        print(f"已发布 {args.topic}", file=sys.stderr)
        return

    if args.command == "sub" and args.json:
        data_type = JsonMessage
    elif args.command == "sub" and args.type:
        module, name = args.type
        data_type = getattr(import_module(module), name)
        if not isinstance(data_type, type) or not issubclass(data_type, (IdlStruct, IdlUnion)):
            raise TypeError("--type 必须指向 Cyclone DDS IDL 结构体或联合体")
    else:
        discovery = BuiltinDataReader(participant, BuiltinTopicDcpsPublication)
        endpoints = {}
        while True:
            for endpoint in discovery.take(128):
                if endpoint.sample_info.valid_data:
                    endpoints[endpoint.key] = endpoint
                else:
                    endpoints.pop(endpoint.key, None)

            if args.command == "sub":
                candidates = [ep for ep in endpoints.values() if ep.topic_name == args.topic]
                if len({ep.type_name for ep in candidates}) > 1:
                    raise ValueError("同名话题有多种 DDS 类型，请用 --type 指定目标类型")
                endpoint = next((ep for ep in candidates if ep.type_id is not None), None)
                if endpoint is not None:
                    try:
                        data_type, _ = get_types_for_typeid(
                            participant, endpoint.type_id,
                            duration(seconds=max(0, deadline - time.monotonic())),
                        )
                    except (DDSException, NotImplementedError) as error:
                        raise RuntimeError(f"无法获取话题类型，请尝试 --type MODULE:CLASS（{error}）") from error
                    break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if args.command == "list":
                    for topic_name, type_name in sorted({
                        (ep.topic_name, ep.type_name) for ep in endpoints.values()
                    }):
                        print(f"{topic_name}\t{type_name}")
                    return
                if candidates:
                    raise TimeoutError("发现了发布端，但没有发现 XTypes 类型信息，请使用 --type MODULE:CLASS")
                raise TimeoutError(f"未发现话题 {args.topic} 的发布端，请检查 Domain 和网络")
            time.sleep(min(POLL_INTERVAL, remaining))

    partition_qos = Qos(Policy.Partition([args.partition]))
    subscriber = Subscriber(participant, qos=partition_qos)
    topic = Topic(participant, args.topic, data_type)
    is_json = getattr(data_type, "__idl_typename__", "").replace(".", "::") == JSON_TYPE
    reliability = Policy.Reliability.BestEffort
    if is_json:
        reliability = Policy.Reliability.Reliable(max_blocking_time=duration(seconds=1))
    reader = DataReader(
        subscriber, topic,
        qos=Qos(reliability, Policy.History.KeepLast(depth=128)),
    )
    print(f"正在订阅 {args.topic}，等待样本", file=sys.stderr)
    received = 0
    while True:
        samples = reader.take(128)
        for sample in samples:
            if not sample.sample_info.valid_data:
                continue
            if is_json:
                output = sample.payload
            elif isinstance(sample, IdlUnion):
                output = json.dumps(
                    {"discriminator": json_value(sample.discriminator), "value": json_value(sample.value)},
                    ensure_ascii=False,
                )
            else:
                output = json.dumps(json_value(sample), ensure_ascii=False)
            print(output, flush=True)
            received += 1
            if received == args.count:
                return
        if not samples:
            time.sleep(POLL_INTERVAL)


def main():
    args = parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 1
    except ModuleNotFoundError as error:
        if error.name == "cyclonedds":
            print("缺少 Cyclone DDS Python 依赖，请安装 requirements.txt。", file=sys.stderr)
        else:
            print(f"错误：{error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
