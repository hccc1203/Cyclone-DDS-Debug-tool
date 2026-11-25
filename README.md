# Cyclone-DDS-Debug-tool

桌面窗口支持扫描话题、查看实时样本，以及向自定义话题单次或循环发送 JSON。

## 安装并打开窗口

使用带 Tkinter 的 Python 3.12。在 Debian 或 Ubuntu 上，系统 Python 的 Tkinter 通常由 `python3-tk` 提供。

```sh
git clone https://github.com/hccc1203/Cyclone-DDS-Debug-tool.git
cd Cyclone-DDS-Debug-tool
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python dds_debug_gui.py
```

## 窗口操作

1. 设置 **Domain ID** 和 **Partition**。默认使用 Domain 0 和默认 Partition。
2. 点击 **扫描话题**，选中话题后点击 **开始订阅**。也可以直接输入话题名。类型默认为 `auto`，通过正在运行的发布端获取 XTypes 类型信息。对方未提供类型信息时，可在类型栏填写生成的 Cyclone DDS Python IDL 类型，例如 `telemetry:VehicleState`。
3. 在 **发布 JSON** 中输入话题名和 JSON，点击 **发送一次**，或填写正数频率后点击 **开始循环**。频率单位为 Hz，例如 `0.5` 表示每两秒发一次，`100` 表示每秒发 100 次。点击 **停止发送** 结束循环。实际速率取决于系统调度和 DDS 写入开销，赶不上目标速率时会跳过错过的发送时刻。
4. 测试本工具的收发时，可在订阅区输入同一个话题名，将类型设为 `json` 并先开始订阅。数据区逐行显示样本，保留最近 2000 行。

自定义 JSON 发布使用固定 DDS 类型 `DDSDebug::JsonMessage`，其 IDL 为 `module DDSDebug { struct JsonMessage { string payload; }; };`。其他程序可用相同 DDS 类型接收。订阅其他话题时使用发布端的 DDS 类型。

跨机器调试时，发布和订阅端应使用相同的 Domain 和匹配的 Partition。如需指定网卡或 Discovery Peers，可在运行前设置 `CYCLONEDDS_URI`。

## 命令行

```sh
python3 dds_debug.py --domain 0 list
python3 dds_debug.py --domain 0 sub DebugTopic --json
python3 dds_debug.py --domain 0 sub VehicleState
python3 dds_debug.py --domain 0 pub DebugTopic '{"value":42}'
python3 dds_debug.py --domain 0 pub DebugTopic '{"value":42}' --hz 2.5
```

单次发送会等待匹配到订阅端。循环发送启动后持续写入样本，按 Ctrl+C 停止。

## 许可证

本项目采用 [MIT License](LICENSE)。
