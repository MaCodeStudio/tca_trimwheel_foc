# ==============================================================================
# Project: TCA A320 FOC Electric Trim Wheel - Control Center
# Author:  macode <yangbine@gmail.com>
# Copyright (c) 2026 MaCode Studio. All rights reserved.
#
# ------------------------------------------------------------------------------
# LICENSE & TERMS OF USE / 许可与使用条款:
#
# [EN]
# This software is open-sourced for personal, educational, and DIY hobbyist
# use ONLY. Commercial use, resale, or repackaging of this software (or its
# modified versions) for profit is STRICTLY PROHIBITED without the explicit
# written permission of the author.
#
# [CN]
# 本软件源代码仅供个人学习、教育和 DIY 爱好者非盈利性使用。
# 未经作者明确书面许可，严禁将本软件（或其任何修改版本）用于商业牟利、
# 换皮倒卖或打包出售。
#
# ------------------------------------------------------------------------------
# DISCLAIMER / 免责声明:
#
# [EN]
# This software is provided "as is", without warranty of any kind. The author
# assumes no responsibility for any hardware damage, data loss, or safety
# incidents caused by the use of this software.
#
# [CN]
# 本软件按“原样”提供，不包含任何形式的担保。作者对因使用本软件或擅自
# 更改接线导致的任何硬件烧毁、电脑损坏或安全意外不承担连带责任。
# ==============================================================================
import sys
import time
import threading
import queue
import serial
import serial.tools.list_ports
import json
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QGroupBox, QLabel, QLineEdit, QPushButton, QTextEdit,
                             QSystemTrayIcon, QMenu, QAction, QStyle, QMessageBox, QGridLayout, QCheckBox)
from PyQt5.QtCore import pyqtSignal, QObject, Qt, QEvent
from PyQt5.QtGui import QTextCursor

from SimConnect import *

# ================= 全局配置与状态 =================
BAUD_RATE = 115200
ser = None
exit_flag = threading.Event()
command_queue = queue.Queue()  # 游戏高频数据队列 (允许被覆盖)
priority_queue = queue.Queue()  # 系统指令高优先级队列 (绝对不丢弃)
msfs_action_queue = queue.Queue()

g_set_trim_value = 0.0
g_set_trim_flag = False
CONFIG_FILE = "tca_config.json"

# 默认映射参数
config_params = {
    "s2h_in_s": -0.118, "s2h_in_e": 0.4, "s2h_out_s": 0.0, "s2h_out_e": 28.0,
    "h2s_in_s": 0.0, "h2s_in_e": 37.699, "h2s_out_s": -1950.0, "h2s_out_e": 9000.0
}

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, 'r') as f:
            config_params.update(json.load(f))
    except Exception:
        pass


class GuiSignals(QObject):
    log = pyqtSignal(str)
    serial_status = pyqtSignal(bool, str)
    msfs_status = pyqtSignal(bool)
    hw_config = pyqtSignal(str)


ui_signals = GuiSignals()


def map_range(value, input_start, input_end, output_start, output_end):
    if input_start == input_end: return output_start
    if input_start < input_end:
        value = max(input_start, min(value, input_end))
    else:
        value = max(input_end, min(value, input_start))
    input_range = input_end - input_start
    output_range = output_end - output_start
    return output_start + output_range * ((value - input_start) / input_range)


# ================= 后台守护线程 =================
def serial_manager():
    global ser
    ui_signals.log.emit("[系统] 串口守护线程已启动...")
    while not exit_flag.is_set():
        if ser is None or not ser.is_open:
            ui_signals.serial_status.emit(False, "正在搜索设备...")
            ports = serial.tools.list_ports.comports()
            target_port = None
            for p in ports:
                desc = p.description.lower()
                if any(k in desc for k in ["mega", "arduino", "ch340", "usb-serial"]):
                    target_port = p.device
                    break

            if target_port:
                try:
                    temp_ser = serial.Serial(target_port, BAUD_RATE, timeout=1)
                    ui_signals.log.emit(f"⏳ 找到设备 ({target_port})，等待 Arduino 启动...")
                    time.sleep(3)
                    temp_ser.reset_input_buffer()
                    temp_ser.reset_output_buffer()
                    ser = temp_ser
                    ui_signals.serial_status.emit(True, target_port)
                    ui_signals.log.emit(f"✅ 成功连接到 Arduino (端口: {target_port})")

                    time.sleep(0.5)
                    priority_queue.put("R")
                except Exception as e:
                    pass
        time.sleep(2)


def serial_receiver():
    global ser, g_set_trim_value, g_set_trim_flag
    last_cfg_time = 0

    while not exit_flag.is_set():
        if ser and ser.is_open:
            try:
                has_data = False
                while ser.in_waiting:
                    has_data = True
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if not line: continue

                    if line.startswith('A'):
                        try:
                            g_set_trim_value = float(line[1:])
                            g_set_trim_flag = True
                        except:
                            pass
                    elif line.startswith('CFG:'):
                        ui_signals.hw_config.emit(line)

                        current_time = time.time()
                        if current_time - last_cfg_time > 2.0:
                            ui_signals.log.emit("⚙️ 成功从主板获取底层硬件配置。")
                        last_cfg_time = current_time

                    elif line == "L0":
                        ui_signals.log.emit("🕹️ 硬件触发: 起落架拨动 (L0)")
                        msfs_action_queue.put("L0")
                    elif line == "L1":
                        ui_signals.log.emit("🕹️ 硬件触发: 起落架拨动 (L1)")
                        msfs_action_queue.put("L1")
                    elif "MANUAL" in line:
                        ui_signals.log.emit("⚠️ 切换到手动配平模式！")
                    elif "Automatic" in line:
                        ui_signals.log.emit("🤖 恢复自动配平模式！")
                    elif line.startswith(">>") or line.startswith("!!"):
                        ui_signals.log.emit(f"{line}")

                if not has_data: time.sleep(0.005)
            except Exception as e:
                if ser: ser.close(); ser = None
        else:
            time.sleep(0.1)


def serial_sender():
    global ser
    while not exit_flag.is_set():
        if ser and ser.is_open:
            cmd = None
            try:
                cmd = priority_queue.get_nowait()
            except queue.Empty:
                try:
                    cmd = command_queue.get(timeout=0.05)
                except queue.Empty:
                    pass

            if cmd is not None:
                try:
                    ser.write((cmd + '\n').encode('utf-8'))
                except Exception:
                    pass
        else:
            time.sleep(0.1)


def msfs_manager():
    global g_set_trim_flag, g_set_trim_value
    old_maps_value = 0
    last_err_time = 0
    is_waiting_logged = False

    while not exit_flag.is_set():
        ui_signals.msfs_status.emit(False)
        sm = None
        try:
            # 【核心修复】：PyInstaller 路径劫持
            # 判断是否被打包成了 exe 运行
            if getattr(sys, 'frozen', False):
                # 获取 exe 文件所在的绝对路径目录
                exe_dir = os.path.dirname(sys.executable)
                dll_path = os.path.join(exe_dir, "SimConnect.dll")

                # 如果同级目录下找不到 DLL，直接抛出异常
                if not os.path.exists(dll_path):
                    raise FileNotFoundError(f"缺失环境文件: 请将 SimConnect.dll 放在 {exe_dir} 目录下！")

                # 强制传递 dll 路径给 SimConnect
                sm = SimConnect(library_path=dll_path)
            else:
                # 源码环境下正常运行
                sm = SimConnect()

            aq = AircraftRequests(sm, _time=0)
            ae = AircraftEvents(sm)

            gear_pct = aq.find("GEAR_CENTER_POSITION")
            gear_toggle = ae.find("GEAR_TOGGLE")
            trim_req = aq.find("ELEVATOR_TRIM_INDICATOR")
            trim_set = ae.find("ELEVATOR_TRIM_SET")

            if not gear_pct or not trim_req:
                raise Exception("无法绑定游戏变量 (飞机可能未载入)")

            ui_signals.msfs_status.emit(True)
            ui_signals.log.emit("✅ 已成功连接至 MSFS 核心引擎")
            is_waiting_logged = False

            while not exit_flag.is_set():
                try:
                    action = msfs_action_queue.get_nowait()
                    current_pct = gear_pct.get() * 100 if gear_pct.get() is not None else 0

                    if action == "L0" and current_pct > 5:
                        gear_toggle()
                        ui_signals.log.emit(">> ✈️ 执行: 收起起落架")
                    elif action == "L1" and current_pct < 95:
                        gear_toggle()
                        ui_signals.log.emit(">> ✈️ 执行: 放下起落架")
                except queue.Empty:
                    pass

                curr_trim = trim_req.get()
                if curr_trim is not None:
                    maps_val = map_range(curr_trim,
                                         config_params["s2h_in_s"], config_params["s2h_in_e"],
                                         config_params["s2h_out_s"], config_params["s2h_out_e"])

                    if command_queue.qsize() > 2:
                        try:
                            command_queue.get_nowait()
                        except:
                            pass
                    command_queue.put(f"T{maps_val:.3f}")

                    if g_set_trim_flag:
                        t_maps_val = int(map_range(g_set_trim_value,
                                                   config_params["h2s_in_s"], config_params["h2s_in_e"],
                                                   config_params["h2s_out_s"], config_params["h2s_out_e"]))
                        if abs(t_maps_val - old_maps_value) > 10:
                            trim_set(t_maps_val)
                            old_maps_value = t_maps_val
                        g_set_trim_flag = False
                time.sleep(0.05)

        except Exception as e:
            if sm:
                try:
                    sm.quit()
                except:
                    pass

            err_msg = str(e).strip()

            # 致命错误判定：如果包含 dll 缺失或相关关键词，持续高频预警
            if "simconnect.dll" in err_msg.lower() or "module not found" in err_msg.lower() or "126" in err_msg:
                curr_time = time.time()
                if curr_time - last_err_time > 10:
                    ui_signals.log.emit(f"⚠️ 致命错误: {err_msg}")
                    last_err_time = curr_time
            else:
                # 游戏未开等常规等待：附加上真实的失败原因，防止成无头苍蝇
                if not is_waiting_logged:
                    ui_signals.log.emit(f"⏳ 正在后台安静等待 MSFS 游戏启动... (状态: {err_msg})")
                    is_waiting_logged = True

            time.sleep(5)


# ================= 可视化窗口 (GUI) =================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TCA 电动配平控制中心 - V1.0 Copyright ©2026 MaCode Studio")
        self.setFixedSize(920, 720)
        self.init_ui()
        self.init_tray()

        ui_signals.log.connect(self.append_log)
        ui_signals.serial_status.connect(self.update_serial_ui)
        ui_signals.msfs_status.connect(self.update_msfs_ui)
        ui_signals.hw_config.connect(self.update_hw_ui)

        threading.Thread(target=serial_manager, daemon=True).start()
        threading.Thread(target=serial_receiver, daemon=True).start()
        threading.Thread(target=serial_sender, daemon=True).start()
        threading.Thread(target=msfs_manager, daemon=True).start()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        left_layout = QVBoxLayout()

        gb1 = QGroupBox("1. 游戏数据 -> 硬件电机")
        gl1 = QGridLayout()
        self.e_s2h_is = QLineEdit(str(config_params["s2h_in_s"]));
        gl1.addWidget(QLabel("Sim始:"), 0, 0);
        gl1.addWidget(self.e_s2h_is, 0, 1)
        self.e_s2h_ie = QLineEdit(str(config_params["s2h_in_e"]));
        gl1.addWidget(QLabel("Sim终:"), 0, 2);
        gl1.addWidget(self.e_s2h_ie, 0, 3)
        self.e_s2h_os = QLineEdit(str(config_params["s2h_out_s"]));
        gl1.addWidget(QLabel("HW始:"), 1, 0);
        gl1.addWidget(self.e_s2h_os, 1, 1)
        self.e_s2h_oe = QLineEdit(str(config_params["s2h_out_e"]));
        gl1.addWidget(QLabel("HW终:"), 1, 2);
        gl1.addWidget(self.e_s2h_oe, 1, 3)
        gb1.setLayout(gl1);
        left_layout.addWidget(gb1)

        gb2 = QGroupBox("2. 硬件手轮 -> 游戏数值")
        gl2 = QGridLayout()
        self.e_h2s_is = QLineEdit(str(config_params["h2s_in_s"]));
        gl2.addWidget(QLabel("HW始:"), 0, 0);
        gl2.addWidget(self.e_h2s_is, 0, 1)
        self.e_h2s_ie = QLineEdit(str(config_params["h2s_in_e"]));
        gl2.addWidget(QLabel("HW终:"), 0, 2);
        gl2.addWidget(self.e_h2s_ie, 0, 3)
        self.e_h2s_os = QLineEdit(str(config_params["h2s_out_s"]));
        gl2.addWidget(QLabel("Sim始:"), 1, 0);
        gl2.addWidget(self.e_h2s_os, 1, 1)
        self.e_h2s_oe = QLineEdit(str(config_params["h2s_out_e"]));
        gl2.addWidget(QLabel("Sim终:"), 1, 2);
        gl2.addWidget(self.e_h2s_oe, 1, 3)
        gb2.setLayout(gl2);
        left_layout.addWidget(gb2)

        btn_apply_map = QPushButton("保存映射参数 (仅存本地)")
        btn_apply_map.setStyleSheet("background-color: #2D89EF; color: white;")
        btn_apply_map.clicked.connect(self.save_map_config)
        left_layout.addWidget(btn_apply_map)

        gb3 = QGroupBox("3. 硬件底层参数 (固化于主板EEPROM)")
        gb3.setStyleSheet(
            "QGroupBox { border: 1px solid #FF9900; margin-top: 1ex; } QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #FF9900; }")
        gl3 = QGridLayout()

        self.hw_pole = QLineEdit("7");
        gl3.addWidget(QLabel("电机极对数:"), 0, 0);
        gl3.addWidget(self.hw_pole, 0, 1)
        self.hw_pid_p = QLineEdit("0.17");
        gl3.addWidget(QLabel("速度环P参数:"), 0, 2);
        gl3.addWidget(self.hw_pid_p, 0, 3)

        self.hw_pid_i = QLineEdit("1.0");
        gl3.addWidget(QLabel("速度环I参数:"), 1, 0);
        gl3.addWidget(self.hw_pid_i, 1, 1)
        self.hw_vel = QLineEdit("4.0");
        gl3.addWidget(QLabel("最大速度限制:"), 1, 2);
        gl3.addWidget(self.hw_vel, 1, 3)

        self.hw_pin_zero = QLineEdit("27");
        gl3.addWidget(QLabel("归零引脚IO:"), 2, 0);
        gl3.addWidget(self.hw_pin_zero, 2, 1)
        self.hw_pin_gear = QLineEdit("26");
        gl3.addWidget(QLabel("起落架引脚IO:"), 2, 2);
        gl3.addWidget(self.hw_pin_gear, 2, 3)

        gl3_btns = QHBoxLayout()
        btn_hw_read = QPushButton("重新读取");
        btn_hw_read.clicked.connect(lambda: priority_queue.put("R"))
        btn_hw_save = QPushButton("写入主板并生效");
        btn_hw_save.clicked.connect(self.save_hw_config)
        btn_hw_save.setStyleSheet("background-color: #D83B01; color: white; font-weight: bold;")
        btn_hw_reset = QPushButton("恢复出厂");
        btn_hw_reset.clicked.connect(self.reset_hw_config)

        gl3_btns.addWidget(btn_hw_read);
        gl3_btns.addWidget(btn_hw_save);
        gl3_btns.addWidget(btn_hw_reset)
        gl3.addLayout(gl3_btns, 3, 0, 1, 4)
        gb3.setLayout(gl3)
        left_layout.addWidget(gb3)

        gb4 = QGroupBox("4. 配平轮硬件测试")
        gb4.setStyleSheet(
            "QGroupBox { border: 1px solid #107C10; margin-top: 1ex; } QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #107C10; }")
        gl4 = QHBoxLayout()

        btn_test_up = QPushButton("⬆️ NOSE UP (读取 HW终)")
        btn_test_up.setFixedHeight(35)
        btn_test_up.clicked.connect(self.test_nose_up)

        btn_test_down = QPushButton("⬇️ NOSE DOWN (读取 HW始)")
        btn_test_down.setFixedHeight(35)
        btn_test_down.clicked.connect(self.test_nose_down)

        gl4.addWidget(btn_test_up)
        gl4.addWidget(btn_test_down)
        gb4.setLayout(gl4)
        left_layout.addWidget(gb4)

        self.chk_top = QCheckBox("窗口置顶")
        self.chk_top.stateChanged.connect(self.toggle_top)
        left_layout.addWidget(self.chk_top)

        gb_status = QGroupBox("连接状态")
        sl = QVBoxLayout()
        self.lbl_ser = QLabel("🔴 串口状态: 等待初始化...")
        self.lbl_sim = QLabel("🔴 MSFS状态: 等待游戏启动...")
        sl.addWidget(self.lbl_ser)
        sl.addWidget(self.lbl_sim)
        gb_status.setLayout(sl)
        left_layout.addWidget(gb_status)
        left_layout.addStretch()

        right_layout = QVBoxLayout()
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("运行日志:"))
        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(lambda: self.log_box.clear())
        header_layout.addWidget(btn_clear, alignment=Qt.AlignRight)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet("background: #1E1E1E; color: #00FF00; font-family: Consolas;")

        right_layout.addLayout(header_layout)
        right_layout.addWidget(self.log_box)

        main_layout.addLayout(left_layout, 2)
        main_layout.addLayout(right_layout, 3)

    def init_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        menu = QMenu()
        act_show = QAction("显示主窗口", self)
        act_show.triggered.connect(self.showNormal)
        act_quit = QAction("彻底退出程序", self)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_show)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_icon_clicked)
        self.tray.show()

    def tray_icon_clicked(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal();
            self.activateWindow()

    def closeEvent(self, event):
        reply = QMessageBox.question(self, '退出确认',
                                     '您确定要彻底关闭 TCA 配平控制中心吗？\n\n(关闭后，配平轮和起落架将停止与游戏的自动同步)',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.quit_app()
            event.accept()
        else:
            event.ignore()

    def toggle_top(self, state):
        if state == Qt.Checked:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        self.show()

    def send_test_cmd(self, cmd, name):
        priority_queue.put(cmd)
        self.append_log(f"🛠️ [硬件测试] 执行: {name} (指令: {cmd})")

    def test_nose_up(self):
        try:
            val = float(self.e_s2h_oe.text())
            cmd = f"T{val:.3f}"
            self.send_test_cmd(cmd, "NOSE UP")
        except ValueError:
            QMessageBox.warning(self, "格式错误", "区块1中的 'HW终' 必须是有效数字！")

    def test_nose_down(self):
        try:
            val = float(self.e_s2h_os.text())
            cmd = f"T{val:.3f}"
            self.send_test_cmd(cmd, "NOSE DOWN")
        except ValueError:
            QMessageBox.warning(self, "格式错误", "区块1中的 'HW始' 必须是有效数字！")

    def save_map_config(self):
        try:
            config_params["s2h_in_s"] = float(self.e_s2h_is.text())
            config_params["s2h_in_e"] = float(self.e_s2h_ie.text())
            config_params["s2h_out_s"] = float(self.e_s2h_os.text())
            config_params["s2h_out_e"] = float(self.e_s2h_oe.text())
            config_params["h2s_in_s"] = float(self.e_h2s_is.text())
            config_params["h2s_in_e"] = float(self.e_h2s_ie.text())
            config_params["h2s_out_s"] = float(self.e_h2s_os.text())
            config_params["h2s_out_e"] = float(self.e_h2s_oe.text())

            with open(CONFIG_FILE, 'w') as f:
                json.dump(config_params, f)
            self.append_log("⚙️ 映射参数已更新并保存至本地文件。")
        except ValueError:
            QMessageBox.warning(self, "格式错误", "映射参数必须是有效数字！")

    def save_hw_config(self):
        reply = QMessageBox.question(self, '写入主板确认',
                                     '您确定要将当前参数固化到主板 EEPROM 吗？\n(写入后将自动重启硬件生效)',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                pole = int(self.hw_pole.text())
                p = float(self.hw_pid_p.text())
                i = float(self.hw_pid_i.text())
                v = float(self.hw_vel.text())
                zero = int(self.hw_pin_zero.text())
                gear = int(self.hw_pin_gear.text())

                cmd = f"C{pole},{p:.2f},{i:.2f},{v:.2f},{zero},{gear}"
                priority_queue.put(cmd)
                self.append_log("📤 正在向硬件烧写新配置，请等待硬件重启...")
            except ValueError:
                QMessageBox.warning(self, "格式错误", "底层参数格式有误（需区分整数和小数）")
        else:
            self.append_log("ℹ️ 已取消写入操作。")

    def reset_hw_config(self):
        reply = QMessageBox.warning(self, '恢复出厂确认',
                                    '警告：此操作将清除主板上的自定义配置，\n并强制恢复为出厂默认参数！\n是否继续？',
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            priority_queue.put("D")
            self.append_log("⚠️ 已发送恢复出厂指令，正在强制硬件重启...")
        else:
            self.append_log("ℹ️ 已取消恢复出厂操作。")

    def update_hw_ui(self, cfg_str):
        try:
            parts = cfg_str.replace("CFG:", "").split(",")
            if len(parts) >= 6:
                self.hw_pole.setText(parts[0])
                self.hw_pid_p.setText(parts[1])
                self.hw_pid_i.setText(parts[2])
                self.hw_vel.setText(parts[3])
                self.hw_pin_zero.setText(parts[4])
                self.hw_pin_gear.setText(parts[5])
        except Exception as e:
            pass

    def append_log(self, text):
        t = time.strftime("%H:%M:%S")
        self.log_box.append(f"[{t}] {text}")
        self.log_box.moveCursor(QTextCursor.End)

    def update_serial_ui(self, is_ok, msg):
        self.lbl_ser.setText(f"🟢 串口状态: 已连接 ({msg})" if is_ok else f"🔴 串口状态: {msg}")
        self.lbl_ser.setStyleSheet(f"color: {'green' if is_ok else 'red'}; font-weight: bold;")

    def update_msfs_ui(self, is_ok):
        self.lbl_sim.setText("🟢 MSFS状态: 游戏正在运行" if is_ok else "🔴 MSFS状态: 等待游戏启动...")
        self.lbl_sim.setStyleSheet(f"color: {'green' if is_ok else 'red'}; font-weight: bold;")

    def quit_app(self):
        exit_flag.set()
        if ser and ser.is_open: ser.close()
        QApplication.quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())