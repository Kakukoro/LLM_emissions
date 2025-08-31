"""
Carbon Footprint Tracker for AI Models

This script monitors and visualizes the environmental impact of running AI models,
specifically tracking CPU, GPU, and RAM usage along with their associated energy
consumption and CO2 emissions. It provides real-time graphs and measurements
through a GUI interface.

Key Features:
- Hardware detection for CPU, GPU, and RAM
- Real-time resource monitoring
- Energy consumption calculation
- CO2 emissions tracking
- Interactive GUI with live graphs
- Integration with Ollama LLM for testing

Dependencies:
- PySide6: GUI framework
- psutil: System monitoring
- pyqtgraph: Real-time plotting
- langchain_community: LLM integration
"""

import psutil
import platform
import subprocess
import re
from dataclasses import dataclass
import time
from typing import Optional
from langchain_ollama import OllamaLLM
from langchain_community.llms import Ollama
import os
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton, QLabel, QHBoxLayout, \
    QTextEdit, QLineEdit, QSizePolicy, QMessageBox, QDialog, QListWidget, QDialogButtonBox, QProgressDialog, \
    QListWidgetItem
from PySide6.QtCore import QTimer, QThread, Signal
import pyqtgraph as pg
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor, QActionGroup


@dataclass
class SystemSpecs:
    """
    Dataclass to store system hardware specifications.
    
    Attributes:
        cpu_model (str): CPU model name
        cpu_tdp (Optional[float]): CPU Thermal Design Power in watts
        gpu_model (str): GPU model name
        gpu_type (str): GPU type ('nvidia', 'amd', or 'intel_integrated')
        gpu_tdp (Optional[float]): GPU Thermal Design Power in watts
        ram_total_gb (float): Total RAM in gigabytes
    """
    cpu_model: str
    cpu_tdp: Optional[float]
    gpu_model: str
    gpu_type: str
    gpu_tdp: Optional[float]
    ram_total_gb: float


class HardwareDetector:
    """
    Utility class for detecting and gathering system hardware information.
    Supports Windows, Linux, and macOS platforms.
    """

    @staticmethod
    def get_cpu_info() -> tuple[str, Optional[float]]:
        """
        Detect CPU model and TDP.
        
        Returns:
            tuple: (CPU model name, TDP in watts if available)
        """
        if platform.system() == "Windows":
            try:
                import wmi
                w = wmi.WMI()
                cpu = w.Win32_Processor()[0]
                return cpu.Name, None
            except:
                return platform.processor(), None

        elif platform.system() == "Linux":
            try:
                with open("/proc/cpuinfo", "r") as f:
                    info = f.read()
                model = re.search("model name.*: (.*)", info).group(1)
                return model, None
            except:
                return platform.processor(), None

        elif platform.system() == "Darwin":
            try:
                cmd = "sysctl -n machdep.cpu.brand_string"
                model = subprocess.check_output(cmd.split()).decode().strip()
                return model, None
            except:
                return platform.processor(), None

        return platform.processor(), None

    @staticmethod
    def get_gpu_info() -> tuple[str, str, Optional[float]]:
        """
        Detect GPU model, type, and TDP.
        
        Returns:
            tuple: (GPU model name, GPU type, TDP in watts if available)
        """
        # Check for NVIDIA GPU
        try:
            nvidia_smi = subprocess.check_output(["nvidia-smi", "-L"]).decode()
            model = re.search("GPU 0: (.*?) \(", nvidia_smi).group(1)
            power_info = subprocess.check_output([
                'nvidia-smi',
                '--query-gpu=power.default_limit',
                '--format=csv,noheader,nounits'
            ]).decode()
            tdp = float(power_info.strip())
            return model, "nvidia", tdp
        except:
            pass

        # Check for Intel integrated GPU
        if platform.system() == "Windows":
            try:
                import wmi
                w = wmi.WMI()
                gpu = w.Win32_VideoController()[0]
                if "Intel" in gpu.Name:
                    return gpu.Name, "intel_integrated", 15
            except:
                pass

        # Check for AMD GPU
        try:
            if platform.system() == "Linux":
                with open("/sys/class/drm/card0/device/vendor", "r") as f:
                    vendor_id = f.read().strip()
                    if vendor_id == "0x1002":  # AMD vendor ID
                        return "AMD GPU", "amd", None
        except:
            pass

        return "Integrated Graphics", "intel_integrated", 15

    @staticmethod
    def get_system_specs():
        cpu_model, cpu_tdp = HardwareDetector.get_cpu_info()
        gpu_model, gpu_type, gpu_tdp = HardwareDetector.get_gpu_info()
        ram_total_gb = psutil.virtual_memory().total / (1024 ** 3)

        # Estimate TDP based on CPU model if not detected
        if not cpu_tdp:
            if any(x in cpu_model.lower() for x in ['i9', 'ryzen 9']):
                cpu_tdp = 125
            elif any(x in cpu_model.lower() for x in ['i7', 'ryzen 7']):
                cpu_tdp = 95
            elif any(x in cpu_model.lower() for x in ['i5', 'ryzen 5']):
                cpu_tdp = 65
            else:
                cpu_tdp = 45  # Conservative estimate

        return SystemSpecs(
            cpu_model=cpu_model,
            cpu_tdp=cpu_tdp,
            gpu_model=gpu_model,
            gpu_type=gpu_type,
            gpu_tdp=gpu_tdp,
            ram_total_gb=ram_total_gb
        )

    @staticmethod
    def get_intel_gpu_usage():
        """Get Intel GPU utilization percentage."""
        try:
            if platform.system() == "Linux":
                # Using intel_gpu_top if available
                cmd = "intel_gpu_top -J"
                output = subprocess.check_output(cmd.split(), timeout=1).decode()
                # Parse JSON output for GPU utilization
                import json
                data = json.loads(output)
                return float(data['engines']['render']['busy'])
            elif platform.system() == "Windows":
                # Using Windows Performance Counters
                import wmi
                w = wmi.WMI(namespace="root\\Intel\\Power")
                gpu_data = w.Intel_GPU()[0]
                return float(gpu_data.GPUUtilization)
        except:
            # If we can't get actual GPU usage, estimate based on CPU usage
            # since integrated GPUs share resources with CPU
            return psutil.cpu_percent() * 0.3  # Rough estimate
        return 0
    @staticmethod
    def get_nvidia_gpu_usage():
        """Get NVIDIA GPU utilization percentage."""
        try:
            output = subprocess.check_output([
                'nvidia-smi',
                '--query-gpu=utilization.gpu',
                '--format=csv,noheader,nounits'
            ]).decode().strip()
            return float(output)
        except Exception as e:
            print(f"Error fetching NVIDIA GPU usage: {e}")
            return 0.0

class CarbonTracker:
    def __init__(self, carbon_intensity: float = 0.385):
        self.specs = HardwareDetector.get_system_specs()
        self.process = psutil.Process()
        self.carbon_intensity = carbon_intensity
        self.cooling_overhead = 1.1  # 10% cooling overhead
        self.ram_power_per_gb = 0.3  # Watts per GB
        # Set the correct GPU usage function based on GPU type
        if self.specs.gpu_type == "nvidia":
            self.get_gpu_usage = HardwareDetector.get_nvidia_gpu_usage
        elif self.specs.gpu_type == "intel_integrated":
            self.get_gpu_usage = HardwareDetector.get_intel_gpu_usage
        else:
            self.get_gpu_usage = lambda: 0.0

        print("\n=== System Specifications ===")
        print(f"CPU: {self.specs.cpu_model} (TDP: {self.specs.cpu_tdp}W)")
        print(f"GPU: {self.specs.gpu_model} (Type: {self.specs.gpu_type}, TDP: {self.specs.gpu_tdp}W)")
        print(f"RAM: {self.specs.ram_total_gb:.1f} GB")
        print("===========================\n")

    def measure(self, func):
        def wrapper(*args, **kwargs):
            # Start measurements
            start_time = time.time()
            start_cpu = psutil.cpu_percent(interval=None)
            start_ram = self.process.memory_info().rss / (1024 ** 3)
            start_gpu = self.get_gpu_usage()

            result = func(*args, **kwargs)

            # End measurements
            end_time = time.time()
            end_cpu = psutil.cpu_percent(interval=None)
            end_ram = self.process.memory_info().rss / (1024 ** 3)
            end_gpu = self.get_gpu_usage()

            # Calculate metrics
            duration = end_time - start_time
            avg_cpu_percent = (end_cpu + start_cpu) / 2
            avg_ram_gb = (end_ram + start_ram) / 2
            avg_gpu_percent = (end_gpu + start_gpu) / 2

            # Energy calculations (Wh)
            cpu_energy = (self.specs.cpu_tdp * (avg_cpu_percent / 100) * duration) / 3600
            ram_energy = (self.ram_power_per_gb * avg_ram_gb * duration) / 3600

            # GPU energy calculation based on type
            if self.specs.gpu_type == "intel_integrated":
                # Intel integrated GPU typically uses 10-15W under load
                gpu_energy = (15 * (avg_gpu_percent / 100) * duration) / 3600
            elif self.specs.gpu_type == "nvidia" and self.specs.gpu_tdp:
                gpu_energy = (self.specs.gpu_tdp * (avg_gpu_percent / 100) * duration) / 3600
            else:
                gpu_energy = 0

            # Total energy including cooling
            total_energy_kwh = (cpu_energy + ram_energy + gpu_energy) * self.cooling_overhead / 1000

            # Calculate CO2
            co2_emissions = total_energy_kwh * self.carbon_intensity

            print("\n=== Environmental Impact ===")
            print(f"Duration: {duration:.2f} seconds")
            print(f"Average CPU: {avg_cpu_percent:.1f}%")
            print(f"Average GPU: {avg_gpu_percent:.1f}%")
            print(f"RAM Usage: {avg_ram_gb:.2f} GB")

            print("\n=== Energy & Emissions ===")
            print(f"CPU Energy: {cpu_energy * 1000:.2f} Wh")
            print(f"GPU Energy: {gpu_energy * 1000:.2f} Wh")
            print(f"RAM Energy: {ram_energy * 1000:.2f} Wh")
            print(f"Total Energy: {total_energy_kwh * 1000:.2f} Wh")
            print(f"CO2 Emissions: {co2_emissions * 1000:.2f} gCO2")
            print("=========================\n")

            return result

        return wrapper


class OllamaThread(QThread):
    """Thread for handling Ollama requests"""
    response_ready = Signal(str)  # Signal to emit when response is ready
    error_occurred = Signal(str)  # Signal for errors

    def __init__(self, llm, prompt):
        super().__init__()
        self.llm = llm
        self.prompt = prompt

    def run(self):
        try:
            response = self.llm.invoke(
                self.prompt,
                temperature=0.7,
                max_tokens=500
            )
            self.response_ready.emit(response)
        except Exception as e:
            self.error_occurred.emit(str(e))


class ModelSelectorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Model")
        self.setModal(True)

        layout = QVBoxLayout(self)

        # Create list widget for models
        self.model_list = QListWidget()
        layout.addWidget(self.model_list)

        # Add OK and Cancel buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Add some default models that we know exist
        default_models = ["llama3.2:1b", "gpt-oss", "mistral-small3.1", "gemma3"]

        # Try to get installed models
        try:
            import requests
            response = requests.get("http://localhost:11434/api/tags")
            if response.status_code == 200:
                installed_models = [model["name"] for model in response.json()["models"]]

                # Add models to list with installation status
                for model in default_models:
                    item = QListWidgetItem(model)
                    if model not in installed_models:
                        item.setText(f"{model} (not installed)")
                        item.setForeground(Qt.gray)
                    self.model_list.addItem(item)
            else:
                raise Exception("Failed to fetch models")
        except Exception as e:
            # Fallback: just add the models without status
            for model in default_models:
                self.model_list.addItem(model)

    def get_selected_model(self):
        if self.model_list.currentItem():
            # Strip the "(not installed)" suffix if present
            model_name = self.model_list.currentItem().text().split(" (")[0]

            # Check if model needs to be installed
            if "(not installed)" in self.model_list.currentItem().text():
                reply = QMessageBox.question(
                    self,
                    "Install Model",
                    f"The model '{model_name}' is not installed. Would you like to install it now?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )

                if reply == QMessageBox.Yes:
                    try:
                        # Show installation progress
                        progress_msg = QMessageBox(self)
                        progress_msg.setIcon(QMessageBox.Information)
                        progress_msg.setText(f"Installing {model_name}...\nThis might take a few minutes.")
                        progress_msg.setStandardButtons(QMessageBox.NoButton)
                        progress_msg.show()
                        QApplication.processEvents()

                        # Run ollama pull command
                        import subprocess
                        subprocess.run(["ollama", "pull", model_name], check=True)

                        progress_msg.close()
                        QMessageBox.information(self, "Success", f"Model {model_name} installed successfully!")

                    except Exception as e:
                        QMessageBox.critical(
                            self,
                            "Installation Error",
                            f"Failed to install model {model_name}. Error: {str(e)}"
                        )
                        return None
                else:
                    return None

            return model_name
        return None


class CarbonTrackerGUI(QMainWindow):
    def __init__(self, tracker):
        super().__init__()
        self.setWindowTitle("AI Carbon Tracker")
        self.setMinimumSize(800, 600)

        self.tracker = tracker
        self.llm = Ollama(model="llama3.2:1b")

        # Add this model_info dictionary
        self.model_info = {
            "llama3.2:1b": {
                "name": "Llama 2 7B",
                "parameters": "7 billion",
                "context_length": "4096 tokens"
            },
            "gpt-oss": {
                "name": "Llama 2 1B",
                "parameters": "1.1 billion",
                "context_length": "4096 tokens"
            },
            "mistral-small3.1": {
                "name": "Mistral 7B",
                "parameters": "7.3 billion",
                "context_length": "8192 tokens"
            },
            "gemma3": {
                "name": "Code Llama 7B",
                "parameters": "7 billion",
                "context_length": "4096 tokens"
            }
        }

        self.current_model = "llama3.2:1b"  # Default model

        self.create_menu_bar()

        # Setup data storage
        self.timestamps = []
        self.cpu_data = []
        self.gpu_data = []
        self.ram_data = []
        self.co2_data = []
        # Add energy data lists
        self.cpu_energy_data = []
        self.gpu_energy_data = []
        self.ram_energy_data = []

        self.setup_ui()

        # Update timer
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_graphs)
        self.timer.start(1000)  # Update every second

        self.start_time = time.time()

    def create_menu_bar(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")
        exit_action = file_menu.addAction("Exit")
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)

        # View menu
        view_menu = menubar.addMenu("View")

        # Reset Graphs action
        reset_graphs = view_menu.addAction("Reset Graphs")
        reset_graphs.setShortcut("Ctrl+R")
        reset_graphs.triggered.connect(self.reset_graphs)

        # Toggle Dark Mode
        dark_mode = view_menu.addAction("Dark Mode")
        dark_mode.setCheckable(True)  # Makes it toggleable
        dark_mode.triggered.connect(self.toggle_dark_mode)

        # Add separator
        view_menu.addSeparator()

        # Show/Hide different plots submenu
        plots_menu = view_menu.addMenu("Show/Hide Plots")

        self.show_usage_plot = plots_menu.addAction("Resource Usage")
        self.show_usage_plot.setCheckable(True)
        self.show_usage_plot.setChecked(True)
        self.show_usage_plot.triggered.connect(self.toggle_usage_plot)

        self.show_energy_plot = plots_menu.addAction("Energy Consumption")
        self.show_energy_plot.setCheckable(True)
        self.show_energy_plot.setChecked(True)
        self.show_energy_plot.triggered.connect(self.toggle_energy_plot)

        self.show_co2_plot = plots_menu.addAction("CO2 Emissions")
        self.show_co2_plot.setCheckable(True)
        self.show_co2_plot.setChecked(True)
        self.show_co2_plot.triggered.connect(self.toggle_co2_plot)

        # Models menu
        models_menu = menubar.addMenu("Models")

        # Select Model action
        select_model = models_menu.addAction("Select Model")
        select_model.setShortcut("Ctrl+M")
        select_model.triggered.connect(self.show_model_selector)

        # Add separator
        models_menu.addSeparator()

        # Quick switch submenu
        quick_switch = models_menu.addMenu("Quick Switch")

        # Add common models
        models = {
            "llama3.2:1b": "llama3.2:1b",
            "gpt-oss": "gpt-oss",
            "mistral-small3.1": "mistral-small3.1",
            "gemma3": "gemma3"
        }

        # Create action group for radio-button behavior
        model_group = QActionGroup(self)
        model_group.setExclusive(True)

        for display_name, model_id in models.items():
            action = quick_switch.addAction(display_name)
            action.setCheckable(True)
            action.setData(model_id)
            model_group.addAction(action)
            if model_id == "llama3.2:1b":  # Set default model
                action.setChecked(True)
            action.triggered.connect(lambda checked, m=model_id: self.switch_model(m))

        # Add separator
        models_menu.addSeparator()

        # Refresh Models action
        refresh_models = models_menu.addAction("Refresh Available Models")
        refresh_models.triggered.connect(self.refresh_models)

    def reset_graphs(self):
        """Reset all graph data"""
        self.timestamps.clear()
        self.cpu_data.clear()
        self.gpu_data.clear()
        self.ram_data.clear()
        self.co2_data.clear()
        self.cpu_energy_data.clear()
        self.gpu_energy_data.clear()
        self.ram_energy_data.clear()
        self.start_time = time.time()

    def toggle_dark_mode(self, checked):
        """Toggle dark/light mode"""
        if checked:
            # Dark mode colors
            self.setStyleSheet("""
                QMainWindow, QWidget {
                    background-color: #2b2b2b;
                    color: #ffffff;
                }
                QTextEdit, QLineEdit {
                    background-color: #3b3b3b;
                    color: #ffffff;
                    border: 1px solid #555555;
                }
                QPushButton {
                    background-color: #444444;
                    color: #ffffff;
                    border: 1px solid #555555;
                }
            """)
            # Update plot colors
            self.usage_plot.setBackground('#2b2b2b')
            self.energy_plot.setBackground('#2b2b2b')
            self.co2_plot.setBackground('#2b2b2b')
        else:
            # Light mode colors
            self.setStyleSheet("")
            # Reset plot backgrounds
            self.usage_plot.setBackground('white')
            self.energy_plot.setBackground('white')
            self.co2_plot.setBackground('white')

    def toggle_usage_plot(self, checked):
        """Show/hide resource usage plot"""
        self.usage_plot.setVisible(checked)
        if checked:
            self.usage_plot.setMinimumHeight(200)
            self.usage_plot.enableAutoRange()
        else:
            self.usage_plot.setMinimumHeight(0)

    def toggle_energy_plot(self, checked):
        """Show/hide energy consumption plot"""
        self.energy_plot.setVisible(checked)
        if checked:
            self.energy_plot.setMinimumHeight(200)
            self.energy_plot.enableAutoRange()
        else:
            self.energy_plot.setMinimumHeight(0)

    def toggle_co2_plot(self, checked):
        """Show/hide CO2 emissions plot"""
        self.co2_plot.setVisible(checked)
        if checked:
            self.co2_plot.setMinimumHeight(200)
            self.co2_plot.enableAutoRange()
        else:
            self.co2_plot.setMinimumHeight(0)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Create horizontal layout for top info
        top_layout = QHBoxLayout()
        layout.addLayout(top_layout)

        # Legends (left side)
        legend_label = QLabel(
            "=== Graph Legend ===\n<br>"
            "Resource Usage & Energy:\n<br>"
            "<span style='color: red'>■</span> Red = CPU\n<br>"
            "<span style='color: green'>■</span> Green = GPU\n<br>"
            "<span style='color: blue'>■</span> Blue = RAM\n\n<br>"
            "CO2 Emissions:\n<br>"
            "<span style='color: yellow'>■</span> Yellow = Total CO2"
        )
        legend_label.setTextFormat(Qt.RichText)  # Enable HTML formatting
        top_layout.addWidget(legend_label)

        # System info (right side)
        specs = self.tracker.specs
        self.info_label = QLabel()  # Create as class attribute
        self.info_label.setContentsMargins(0, 0, 0, 0)  # Remove padding
        top_layout.addWidget(self.info_label)
        self.update_system_info()  # Initial update of system info

        # Create a horizontal layout for the main content
        main_layout = QHBoxLayout()
        layout.addLayout(main_layout)

        # Left side: Graphs
        graphs_layout = QVBoxLayout()
        graphs_layout.setSpacing(20)  # Add 20 pixels spacing between items

        self.usage_plot = pg.PlotWidget(title="Resource Usage")
        self.usage_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.usage_plot.setMinimumHeight(200)  # Set a fixed minimum height
        self.usage_plot.setLabel('left', 'Usage (%)')
        self.usage_plot.setLabel('bottom', 'Time (s)')
        self.cpu_curve = self.usage_plot.plot(pen='r')
        self.gpu_curve = self.usage_plot.plot(pen='g')
        self.ram_curve = self.usage_plot.plot(pen='b')
        graphs_layout.addWidget(self.usage_plot)

        self.energy_plot = pg.PlotWidget(title="Energy Consumption")
        self.energy_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.energy_plot.setMinimumHeight(200)
        self.energy_plot.setLabel('left', 'Energy (Wh)')
        self.energy_plot.setLabel('bottom', 'Time (s)')
        self.cpu_energy_curve = self.energy_plot.plot(pen='r')
        self.gpu_energy_curve = self.energy_plot.plot(pen='g')
        self.ram_energy_curve = self.energy_plot.plot(pen='b')
        graphs_layout.addWidget(self.energy_plot)

        self.co2_plot = pg.PlotWidget(title="Cumulative CO2 Emissions")
        self.co2_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.co2_plot.setMinimumHeight(200)
        self.co2_plot.setLabel('left', 'CO2 (gCO2)')
        self.co2_plot.setLabel('bottom', 'Time (s)')
        self.co2_curve = self.co2_plot.plot(pen='y')
        graphs_layout.addWidget(self.co2_plot)

        main_layout.addLayout(graphs_layout)

        # Right side: Chat interface
        chat_layout = QVBoxLayout()

        # Chat history with updated styling
        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setStyleSheet("""
            QTextEdit {
                background-color: white;
                border: 1px solid gray;
                border-radius: 5px;
                padding: 5px;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 12pt;
                line-height: 1.4;
            }
        """)
        chat_layout.addWidget(self.chat_history)

        # Input area
        input_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Type your message here...")
        self.chat_input.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.chat_input)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.send_message)
        input_layout.addWidget(self.send_button)

        chat_layout.addLayout(input_layout)

        # Current values display
        self.current_values = QLabel("Current Values:\nCPU: ---%\nGPU: ---%\nRAM: ---%\nEnergy: --- Wh\nCO2: --- gCO2")
        chat_layout.addWidget(self.current_values)

        main_layout.addLayout(chat_layout)

        # Set the ratio between graphs and chat (1:1)
        main_layout.setStretch(0, 1)
        main_layout.setStretch(1, 1)

    def update_graphs(self):
        current_time = time.time() - self.start_time

        # Get current values
        cpu_percent = psutil.cpu_percent()
        gpu_percent = self.tracker.get_gpu_usage()
        ram_percent = psutil.virtual_memory().percent

        # Calculate energy for the last second
        duration = 1.0
        cpu_energy = (self.tracker.specs.cpu_tdp * (cpu_percent / 100) * duration) / 3600
        ram_energy = (self.tracker.ram_power_per_gb * self.tracker.specs.ram_total_gb * (
                    ram_percent / 100) * duration) / 3600

        # GPU energy calculation based on type
        if self.tracker.specs.gpu_type == "intel_integrated":
            gpu_energy = (15 * (gpu_percent / 100) * duration) / 3600
        elif self.tracker.specs.gpu_type == "nvidia" and self.tracker.specs.gpu_tdp:
            gpu_energy = (self.tracker.specs.gpu_tdp * (gpu_percent / 100) * duration) / 3600
        else:
            gpu_energy = 0

        # Total energy including cooling
        total_energy = (cpu_energy + ram_energy + gpu_energy) * self.tracker.cooling_overhead
        co2 = total_energy * self.tracker.carbon_intensity

        # Update data lists
        self.timestamps.append(current_time)
        self.cpu_data.append(cpu_percent)
        self.gpu_data.append(gpu_percent)
        self.ram_data.append(ram_percent)

        # Update energy data lists
        self.cpu_energy_data.append(cpu_energy * 1000)  # Convert to Wh
        self.gpu_energy_data.append(gpu_energy * 1000)
        self.ram_energy_data.append(ram_energy * 1000)

        self.co2_data.append(sum(self.co2_data[-1:] or [0]) + co2 * 1000)  # Cumulative CO2

        # Update graphs
        self.cpu_curve.setData(self.timestamps, self.cpu_data)
        self.gpu_curve.setData(self.timestamps, self.gpu_data)
        self.ram_curve.setData(self.timestamps, self.ram_data)

        # Update energy curves with full history
        self.cpu_energy_curve.setData(self.timestamps, self.cpu_energy_data)
        self.gpu_energy_curve.setData(self.timestamps, self.gpu_energy_data)
        self.ram_energy_curve.setData(self.timestamps, self.ram_energy_data)

        self.co2_curve.setData(self.timestamps, self.co2_data)

        # Update current values label
        self.current_values.setText(
            f"Current Values:\n"
            f"CPU: {cpu_percent:.1f}%\n"
            f"GPU: {gpu_percent:.1f}%\n"
            f"RAM: {ram_percent:.1f}%\n"
            f"Energy: {total_energy * 1000:.2f} Wh\n"
            f"Total CO2: {self.co2_data[-1]:.2f} gCO2"
        )

    def run_llm_test(self):
        """
        Runs a test query to the Ollama LLM and measures its environmental impact.
        Includes error handling and connection verification.
        """

        @self.tracker.measure
        def run_test():
            try:
                print("\n=== Starting LLM Test ===")
                print("Checking Ollama connection...")

                # Test if Ollama server is running
                import requests
                try:
                    requests.get("http://localhost:11434")
                    print("✓ Ollama server is running")
                except requests.exceptions.ConnectionError:
                    print(" Error: Ollama server is not running!")
                    print("Please start Ollama server first.")
                    print("Run 'ollama serve' in a terminal")
                    return None

                # Verify model is available
                print(f"Checking if model 'llama3.2' is available...")
                try:
                    response = self.llm.invoke(
                        "Hello",
                        temperature=0,
                        max_tokens=10
                    )
                    print("✓ Model check successful")
                except Exception as e:
                    print("✗ Error: Model not available!")
                    print("Try running: 'ollama pull llama2'")
                    print(f"Error details: {str(e)}")
                    return None

                # Send actual query
                print("\nSending request to Ollama...")
                response = self.llm.invoke(
                    "What is the environmental impact of AI? (Please keep the answer short)",
                    temperature=0.7,
                    max_tokens=100
                )
                print("\nLLM Response:", response)
                return response

            except Exception as e:
                print(f"\n✗ Error running LLM test: {str(e)}")
                print("\nTroubleshooting steps:")
                print("1. Make sure Ollama is installed")
                print("2. Run 'ollama serve' in a terminal")
                print("3. Run 'ollama pull llama2'")
                print("4. Check if other Ollama commands work")
                return None

        run_test()

    def send_message(self):
        message = self.chat_input.text().strip()
        if not message:
            return

        self.chat_input.clear()

        # Add user message with reduced bottom margin
        self.chat_history.append(f"<div style='margin-bottom: 5px;'><b>You:</b> {message}</div>")

        # Add "AI is typing..." message
        self.chat_history.append(
            "<div id='typing' style='color: #666; margin-bottom: 5px;'>"
            "AI is thinking..."
            "</div>"
        )

        # Create and start thread for Ollama request
        self.ollama_thread = OllamaThread(self.llm, message)
        self.ollama_thread.response_ready.connect(self.handle_response)
        self.ollama_thread.error_occurred.connect(self.handle_error)
        self.ollama_thread.start()

    def handle_response(self, response):
        # Remove the "typing" message and its div
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.deletePreviousChar()  # Remove the extra newline

        # Add AI response with consistent spacing
        self.chat_history.append(
            f"<div style='margin-bottom: 10px; background-color: #f5f5f5; padding: 10px; border-radius: 5px;'>"
            f"<b>AI:</b> {response}"
            f"</div>"
        )

        # Scroll to bottom
        self.chat_history.verticalScrollBar().setValue(
            self.chat_history.verticalScrollBar().maximum()
        )

    def handle_error(self, error_message):
        # Remove the "typing" message
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()

        # Add error message
        self.chat_history.append(
            f"<div style='margin-bottom: 20px; color: #ff0000;'>"
            f"<b>Error:</b> {error_message}"
            f"</div>"
        )

        # Scroll to bottom
        self.chat_history.verticalScrollBar().setValue(
            self.chat_history.verticalScrollBar().maximum()
        )

    def show_model_selector(self):
        dialog = ModelSelectorDialog(self)
        if dialog.exec():
            selected_model = dialog.get_selected_model()
            if selected_model:
                self.switch_model(selected_model)

    def update_system_info(self):
        specs = self.tracker.specs
        model_info = self.model_info.get(self.current_model, {
            "name": self.current_model,
            "parameters": "Unknown",
            "context_length": "Unknown"
        })

        self.info_label.setText(
            f"=== System Specifications ===\n"
            f"CPU: {specs.cpu_model} (TDP: {specs.cpu_tdp}W)\n"
            f"GPU: {specs.gpu_model} (Type: {specs.gpu_type}, TDP: {specs.gpu_tdp}W)\n"
            f"RAM: {specs.ram_total_gb:.1f} GB\n\n"
            f"=== Model Information ===\n"
            f"LLM: {model_info['name']}\n"
            f"Parameters: {model_info['parameters']}\n"
            f"Context Length: {model_info['context_length']}"
        )

    def download_model(self, model_id):
        progress = QProgressDialog(f"Downloading model {model_id}...", "Cancel", 0, 0, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setWindowTitle("Downloading Model")
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        progress.show()

        def update_progress(output):
            if 'downloading' in output.lower():
                progress.setLabelText(output.strip())
            elif 'verifying' in output.lower():
                progress.setLabelText(output.strip())
            QApplication.processEvents()

        try:
            process = subprocess.Popen(
                ['ollama', 'pull', model_id],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )

            for line in process.stdout:
                update_progress(line)
                if progress.wasCanceled():
                    process.terminate()
                    return False

            process.wait()
            if process.returncode == 0:
                return True
            else:
                raise Exception(f"Failed to download model {model_id}")
        except Exception as e:
            QMessageBox.critical(self, "Download Error", str(e))
            return False
        finally:
            progress.close()

    def switch_model(self, model_id):
        try:
            # First verify the model exists by checking with Ollama API
            import requests
            response = requests.get("http://localhost:11434/api/tags")
            if response.status_code != 200:
                raise Exception("Failed to connect to Ollama server")

            installed_models = [model["name"] for model in response.json()["models"]]
            if model_id not in installed_models:
                reply = QMessageBox.question(
                    self,
                    "Model Not Installed",
                    f"The model '{model_id}' is not installed. Would you like to download it?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    if self.download_model(model_id):
                        # Model downloaded successfully, continue with switch
                        pass
                    else:
                        return False
                else:
                    return False

            # If we get here, the model exists and we can switch to it
            self.llm = Ollama(model=model_id)
            self.current_model = model_id
            self.update_system_info()

            self.chat_history.append(f"Successfully switched to model: {model_id}")
            return True

        except Exception as e:
            QMessageBox.warning(
                self,
                "Model Switch Error",
                f"Failed to switch to model {model_id}. Error: {str(e)}\n\n"
                f"Make sure the model name is correct (e.g., 'llama2:7b' or 'llama2:1b')."
            )
            return False

    def refresh_models(self):
        try:
            import requests
            response = requests.get("http://localhost:11434/api/tags")
            if response.status_code == 200:
                models = response.json()["models"]
                model_list = [model["name"] for model in models]
                QMessageBox.information(
                    self,
                    "Available Models",
                    "Installed models:\n" + "\n".join(model_list)
                )
            else:
                raise Exception("Failed to fetch models")
        except Exception as e:
            QMessageBox.warning(
                self,
                "Refresh Error",
                f"Failed to refresh models. Error: {str(e)}\n\n"
                "Make sure Ollama is running."
            )


def main():
    app = QApplication(sys.argv)

    tracker = CarbonTracker()
    window = CarbonTrackerGUI(tracker)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
