"""Desktop interface for the Cyclone DDS debug tool."""
from collections import deque
from pathlib import Path
from queue import Empty, SimpleQueue
import signal
import subprocess
import sys
from threading import Thread
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

CLI = Path(__file__).with_name("dds_debug.py")
LOG_LIMIT = 2000


class DebugWindow:
    def __init__(self, root):
        self.root = root
        self.jobs = {}
        self.events = SimpleQueue()
        self.samples = deque(maxlen=LOG_LIMIT)
        root.title("Cyclone DDS 调试工具")
        root.geometry("920x720")
        root.minsize(680, 580)
        root.protocol("WM_DELETE_WINDOW", self.close)

        body = ttk.Frame(root, padding=16)
        body.pack(fill="both", expand=True)
        connection = ttk.Frame(body)
        connection.pack(fill="x", pady=(0, 10))
        self.domain = self.field(connection, "Domain ID", "0", 8)
        self.partition = self.field(connection, "Partition", "", 18)
        self.scan_button = ttk.Button(connection, text="扫描话题", command=self.scan)
        self.scan_button.pack(side="left")
        self.scan_status = tk.StringVar()
        ttk.Label(connection, textvariable=self.scan_status).pack(side="left", padx=8)

        self.topics = ttk.Treeview(body, columns=("topic", "type"), show="headings", height=4)
        self.topics.heading("topic", text="话题名")
        self.topics.heading("type", text="DDS 类型")
        self.topics.pack(fill="x", pady=(0, 10))
        self.topics.bind("<<TreeviewSelect>>", self.select_topic)

        subscription = ttk.LabelFrame(body, text="订阅", padding=8)
        subscription.pack(fill="both", expand=True, pady=(0, 10))
        controls = ttk.Frame(subscription)
        controls.pack(fill="x")
        self.sub_topic = self.field(controls, "话题", "", 21)
        self.sub_type = self.field(controls, "类型", "", 18)
        self.sub_type.set("auto")
        self.sub_button = ttk.Button(controls, text="开始订阅", command=self.subscribe)
        self.sub_button.pack(side="left")
        ttk.Button(controls, text="清空", command=self.clear).pack(side="left", padx=4)
        self.sub_status = tk.StringVar(value="类型可填 auto、json 或 MODULE:CLASS")
        ttk.Label(subscription, textvariable=self.sub_status, wraplength=650).pack(anchor="w")
        self.output = ScrolledText(subscription, height=8, state="disabled", wrap="none")
        self.output.pack(fill="both", expand=True)

        publishing = ttk.LabelFrame(body, text="发布 JSON", padding=8)
        publishing.pack(fill="x")
        controls = ttk.Frame(publishing)
        controls.pack(fill="x")
        self.pub_topic = self.field(controls, "话题", "DebugTopic", 21)
        self.hz = self.field(controls, "频率 (Hz)", "10", 8)
        self.once_button = ttk.Button(controls, text="发送一次", command=self.send_once)
        self.once_button.pack(side="left", padx=(0, 4))
        self.pub_button = ttk.Button(controls, text="开始循环", command=self.publish)
        self.pub_button.pack(side="left")
        self.payload = ScrolledText(publishing, height=4, wrap="word")
        self.payload.insert("1.0", '{"message": "hello DDS", "value": 42}')
        self.payload.pack(fill="x", pady=6)
        self.pub_status = tk.StringVar()
        ttk.Label(publishing, textvariable=self.pub_status, wraplength=650).pack(anchor="w")
        self.root.after(50, self.drain)

    @staticmethod
    def field(parent, label, initial, width):
        ttk.Label(parent, text=label).pack(side="left", padx=(0, 4))
        variable = tk.StringVar(value=initial)
        ttk.Entry(parent, textvariable=variable, width=width).pack(side="left", padx=(0, 8))
        return variable

    def select_topic(self, _event):
        selected = self.topics.selection()
        if selected:
            topic, data_type = self.topics.item(selected[0], "values")
            self.sub_topic.set(topic)
            if data_type.replace(".", "::") == "DDSDebug::JsonMessage":
                self.sub_type.set("json")
            else:
                self.sub_type.set("auto")

    def scan(self):
        self.scan_status.set("正在扫描…")
        for item in self.topics.get_children():
            self.topics.delete(item)
        if self.start("list", []):
            self.scan_button.state(["disabled"])

    def subscribe(self):
        if "sub" in self.jobs:
            self.stop("sub")
            return
        arguments = ["--partition", self.partition.get()]
        type_option = self.sub_type.get().strip()
        if type_option == "json":
            arguments.append("--json")
        elif type_option not in ("", "auto"):
            arguments += ["--type", type_option]
        arguments.extend(["--", self.sub_topic.get().strip()])
        self.sub_status.set("正在启动…")
        if self.start("sub", arguments):
            self.sub_button.configure(text="停止订阅")

    def send_once(self):
        self.start_publishing([])

    def publish(self):
        if "pub" in self.jobs:
            self.stop("pub")
            return
        self.start_publishing(["--hz", self.hz.get().strip()])

    def start_publishing(self, options):
        arguments = [
            "--partition", self.partition.get(), *options, "--",
            self.pub_topic.get().strip(), self.payload.get("1.0", "end-1c"),
        ]
        self.pub_status.set("正在启动…")
        if self.start("pub", arguments):
            self.once_button.state(["disabled"])
            self.pub_button.configure(text="停止发送")

    def start(self, job, arguments):
        try:
            process = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-u", str(CLI), "--domain",
                 self.domain.get().strip(), job, *arguments],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                errors="replace",
            )
        except (OSError, ValueError) as error:
            self.status(job).set(str(error))
            return False
        self.jobs[job] = process
        Thread(target=self.collect, args=(job, process), daemon=True).start()
        return True

    def collect(self, job, process):
        def read_errors():
            with process.stderr:
                for message in process.stderr:
                    self.events.put((job, process, "status", message.rstrip()))

        errors = Thread(target=read_errors, daemon=True)
        errors.start()
        with process.stdout:
            for line in process.stdout:
                if job == "sub":
                    self.samples.append((process, line))
                else:
                    self.events.put((job, process, "topic", line.rstrip("\n")))
        code = process.wait()
        errors.join()
        self.events.put((job, process, "exit", code))

    def status(self, job):
        return {"list": self.scan_status, "sub": self.sub_status, "pub": self.pub_status}[job]

    def stop(self, job):
        self.status(job).set("正在停止…")
        self.jobs[job].terminate()

    def drain(self):
        samples = []
        for _ in range(LOG_LIMIT):
            try:
                process, line = self.samples.popleft()
            except IndexError:
                break
            if process is self.jobs.get("sub"):
                samples.append(line)
        if samples:
            self.output.configure(state="normal")
            follow = self.output.yview()[1] >= 0.99
            self.output.insert("end", "".join(samples))
            lines = int(self.output.index("end-1c").split(".")[0]) - 1
            if lines > LOG_LIMIT:
                self.output.delete("1.0", f"{lines - LOG_LIMIT + 1}.0")
            if follow:
                self.output.see("end")
            self.output.configure(state="disabled")

        for _ in range(300):
            try:
                job, process, event, value = self.events.get_nowait()
            except Empty:
                break
            if process is not self.jobs.get(job):
                continue
            if event == "status":
                self.status(job).set(value)
            elif event == "topic":
                self.topics.insert("", "end", values=value.split("\t", 1))
            else:
                self.finish(job, value)
        self.root.after(50, self.drain)

    def finish(self, job, code):
        del self.jobs[job]
        if code == -signal.SIGTERM or self.status(job).get() == "正在停止…":
            self.status(job).set("已停止")
        elif job == "list" and code == 0:
            self.scan_status.set(f"发现 {len(self.topics.get_children())} 个话题与类型组合")
        elif code and self.status(job).get() == "正在启动…":
            self.status(job).set(f"进程退出 ({code})")
        if job == "list":
            self.scan_button.state(["!disabled"])
        elif job == "sub":
            self.sub_button.configure(text="开始订阅")
        else:
            self.pub_button.configure(text="开始循环")
            self.once_button.state(["!disabled"])

    def clear(self):
        self.samples.clear()
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def close(self):
        for process in self.jobs.values():
            process.terminate()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    DebugWindow(root)
    root.mainloop()
