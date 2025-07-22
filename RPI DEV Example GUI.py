# Sample program for RPI DEV used in Youtube video-- SECTOR 07
# Showcases buttons, rotary encoder, linear potentiometer slider
# light sensor, temp sensor, and pH sensor, in a GUI environment

from gpiozero import Button, RotaryEncoder
import adafruit_pcf8591.pcf8591 as PCF8591
import adafruit_bh1750
import adafruit_ds248x
import busio
import board
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import threading
import time
import numpy as np
from collections import deque

# GPIO setup 
BUTTON_PINS = [4, 5, 6, 16]  # Button 1 to 4
ENCODER_CLK = 17
ENCODER_DT = 18
ENCODER_SW = 25

# Set up buttons and encoder switch
buttons = [Button(pin, pull_up=True) for pin in BUTTON_PINS]
encoder_switch = Button(ENCODER_SW, pull_up=True)
encoder = RotaryEncoder(ENCODER_CLK, ENCODER_DT, max_steps=40)  
encoder.steps = 20  # Start at 50%

# I2C setup
i2c = busio.I2C(board.SCL, board.SDA)
pcf_pot = PCF8591.PCF8591(i2c, address=0x48)  # Potentiometer ADC
pcf_ph = PCF8591.PCF8591(i2c, address=0x49)  # pH sensor ADC
bh1750 = adafruit_bh1750.BH1750(i2c, address=0x23)  # Light sensor
try:
    ds248x = adafruit_ds248x.Adafruit_DS248x(i2c, address=0x18)  # 1-Wire adapter
except Exception as e:
    print(f"Failed to initialize DS248x: {e}")
    ds248x = None

# Initialize DS18B20
if ds248x:
    rom = bytearray(8)
    if not ds248x.onewire_search(rom):
        print("No DS18B20 found. Check wiring.")
        ds18b20_addr = None
    else:
        ds18b20_addr = rom
        print("Found device ROM:", " ".join(f"{byte:02X}" for byte in rom))
else:
    ds18b20_addr = None
    print("DS248x not initialized. Skipping DS18B20.")

# pH calibration (adjust after measuring with pH 4.0 and 7.0 buffers)
PH_4_0_ADC = 80  # Tuned value
PH_7_0_ADC = 99  # Tuned value
def adc_to_ph(adc_value):
    m = (7.0 - 4.0) / (PH_7_0_ADC - PH_4_0_ADC)
    b = 4.0 - m * PH_4_0_ADC
    return m * adc_value + b

# Slow poll interval for temperature, pH, and lux (seconds)
# Some sensors take a long time to read which makes the GUI seems slow
# So we don't update every cycle
SLOW_POLL_INTERVAL = 1

# GUI class
class SensorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Sensor Dashboard")
        self.root.geometry("1000x600")
        self.style = ttk.Style(theme="darkly")

        # Notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=BOTH, expand=True, padx=10, pady=10)

        # Main tab
        self.main_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.main_tab, text="Dashboard")

        # Graph tab
        self.graph_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.graph_tab, text="Time-Series")

        # Main tab layout
        # Meters frame (left)
        self.meters_frame = ttk.LabelFrame(self.main_tab, text="Sensors", bootstyle=SECONDARY)
        self.meters_frame.grid(row=0, column=0, padx=10, pady=10, sticky="n")

        # First row: Temperature, pH, Lux
        ttk.Label(self.meters_frame, text="Temperature (°C)", bootstyle=INFO).grid(row=0, column=0, padx=5, pady=2)
        self.temp_meter = ttk.Meter(self.meters_frame, amounttotal=50, amountused=25, metersize=200, bootstyle=DANGER)
        self.temp_meter.grid(row=1, column=0, padx=5, pady=5)

        ttk.Label(self.meters_frame, text="pH", bootstyle=WARNING).grid(row=0, column=1, padx=5, pady=2)
        self.ph_meter = ttk.Meter(self.meters_frame, amounttotal=14, amountused=7, metersize=200, bootstyle=WARNING)
        self.ph_meter.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(self.meters_frame, text="Lux", bootstyle=SUCCESS).grid(row=0, column=2, padx=5, pady=2)
        self.lux_meter = ttk.Meter(self.meters_frame, amounttotal=5000, amountused=0, metersize=200, bootstyle=SUCCESS)
        self.lux_meter.grid(row=1, column=2, padx=5, pady=5)

        # Second row: Potentiometer, Encoder
        ttk.Label(self.meters_frame, text="Potentiometer (%)", bootstyle=PRIMARY).grid(row=2, column=0, padx=5, pady=2)
        self.pot_meter = ttk.Meter(self.meters_frame, amounttotal=100, amountused=0, metersize=200, bootstyle=INFO)
        self.pot_meter.grid(row=3, column=0, padx=5, pady=5)

        ttk.Label(self.meters_frame, text="Encoder (%)", bootstyle=PRIMARY).grid(row=2, column=1, padx=5, pady=2)
        self.encoder_meter = ttk.Meter(self.meters_frame, amounttotal=100, amountused=50, metersize=200, bootstyle=INFO)
        self.encoder_meter.grid(row=3, column=1, padx=5, pady=5)

        # Buttons frame (right)
        self.buttons_frame = ttk.LabelFrame(self.main_tab, text="Buttons", bootstyle=SECONDARY)
        self.buttons_frame.grid(row=0, column=1, padx=10, pady=10, sticky="n")

        self.button_labels = []
        for i in range(4):
            label = ttk.Label(self.buttons_frame, text=f"Button {i+1}: OFF", bootstyle=DANGER)
            label.grid(row=i, column=0, padx=5, pady=5, sticky="w")
            self.button_labels.append(label)

        self.switch_label = ttk.Label(self.buttons_frame, text="Encoder Switch: OFF", bootstyle=DANGER)
        self.switch_label.grid(row=4, column=0, padx=5, pady=5, sticky="w")

        # Graph tab setup (separate subplots)
        self.fig, (self.ax_temp, self.ax_ph, self.ax_lux) = plt.subplots(3, 1, figsize=(7, 6), sharex=True)
        self.times = deque(maxlen=6000)  # 10 min at 50ms
        self.temps = deque(maxlen=6000)
        self.phs = deque(maxlen=6000)
        self.luxes = deque(maxlen=6000)

        # Temperature plot
        self.temp_line, = self.ax_temp.plot([], [], label="Temp (°C)", color="cyan")
        self.ax_temp.set_ylabel("Temp (°C)")
        self.ax_temp.legend()
        self.ax_temp.grid(True)

        # pH plot
        self.ph_line, = self.ax_ph.plot([], [], label="pH", color="orange")
        self.ax_ph.set_ylabel("pH")
        self.ax_ph.legend()
        self.ax_ph.grid(True)

        # Lux plot
        self.lux_line, = self.ax_lux.plot([], [], label="Lux", color="green")
        self.ax_lux.set_xlabel("Time (s)")
        self.ax_lux.set_ylabel("Lux")
        self.ax_lux.legend()
        self.ax_lux.grid(True)

        self.fig.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_tab)
        self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)

        # Initialize states
        self.button_states = [False] * 4
        self.switch_state = False
        self.last_encoder_pos = 50
        self.last_pot_percent = -1
        self.last_light = -1
        self.last_temp = -1
        self.last_ph = -1
        self.start_time = time.time()
        self.running = True
        self.last_graph_update = self.start_time
        self.last_temp_update = self.start_time
        self.last_ph_update = self.start_time
        self.last_lux_update = self.start_time

        # Start update thread
        self.update_thread = threading.Thread(target=self.update, daemon=True)
        self.update_thread.start()

    def update(self):
        while self.running:
            loop_start = time.time()
            current_time = time.time()

            # Buttons
            start = time.time()
            for i, button in enumerate(buttons):
                current_state = button.is_pressed
                if current_state != self.button_states[i]:
                    state_text = "ON" if current_state else "OFF"
                    style = SUCCESS if current_state else DANGER
                    self.button_labels[i].configure(text=f"Button {i+1}: {state_text}", bootstyle=style)
                    print(f"Button {i+1}: {state_text}")
                    self.button_states[i] = current_state
            print(f"Buttons read time: {(time.time() - start) * 1000:.2f} ms")

            # Encoder switch
            start = time.time()
            current_switch = encoder_switch.is_pressed
            if current_switch != self.switch_state:
                switch_text = "ON" if current_switch else "OFF"
                style = SUCCESS if current_switch else DANGER
                self.switch_label.configure(text=f"Encoder Switch: {switch_text}", bootstyle=style)
                print(f"Encoder Switch: {switch_text}")
                self.switch_state = current_switch
            print(f"Encoder switch read time: {(time.time() - start) * 1000:.2f} ms")

            # Rotary encoder
            start = time.time()
            encoder_pos = (encoder.steps / 40) * 100  # ~5% increments
            if abs(encoder_pos - self.last_encoder_pos) >= 5:
                self.encoder_meter["amountused"] = encoder_pos
                print(f"Encoder Position: {int(encoder_pos)}%")
                self.last_encoder_pos = encoder_pos
            print(f"Encoder read time: {(time.time() - start) * 1000:.2f} ms")

            # Potentiometer
            start = time.time()
            pot_value = pcf_pot.read(1)  # AIN1
            pot_percent = (pot_value / 255) * 100
            if abs(pot_percent - self.last_pot_percent) > 2:
                self.pot_meter["amountused"] = pot_percent
                print(f"Potentiometer: {int(pot_percent)}%")
                self.last_pot_percent = pot_percent
            print(f"Potentiometer read time: {(time.time() - start) * 1000:.2f} ms")

            # BH1750 light sensor (poll every SLOW_POLL_INTERVAL)
            start = time.time()
            if current_time - self.last_lux_update >= SLOW_POLL_INTERVAL:
                try:
                    light_lux = bh1750.lux
                    if abs(light_lux - self.last_light) > 5:
                        self.lux_meter["amountused"] = min(light_lux, 5000)
                        print(f"Light Intensity: {int(light_lux)} lux")
                        self.last_light = light_lux
                    self.last_lux_update = current_time
                except Exception as e:
                    print(f"BH1750 error: {e}")
            print(f"Lux read time: {(time.time() - start) * 1000:.2f} ms")

            # DS18B20 temperature (poll every SLOW_POLL_INTERVAL)
            start = time.time()
            if current_time - self.last_temp_update >= SLOW_POLL_INTERVAL and ds248x and ds18b20_addr:
                try:
                    temperature = ds248x.ds18b20_temperature(ds18b20_addr)
                    if abs(temperature - self.last_temp) > 0.5:
                        self.temp_meter["amountused"] = min(temperature, 50)
                        print(f"Temperature: {temperature:.1f} °C")
                        self.last_temp = temperature
                    self.last_temp_update = current_time
                except Exception as e:
                    print(f"DS18B20 error: {e}")
            print(f"Temperature read time: {(time.time() - start) * 1000:.2f} ms")

            # pH sensor (poll every loop for calibration)
            start = time.time()
            try:
                ph_adc = pcf_ph.read(0)
                print(f"pH ADC: {ph_adc}")  # Debug print for calibration
                ph_value = adc_to_ph(ph_adc)
                if current_time - self.last_ph_update >= SLOW_POLL_INTERVAL:
                    if abs(ph_value - self.last_ph) > 0.1:
                        self.ph_meter["amountused"] = min(max(ph_value, 0), 14)
                        print(f"pH Value: {ph_value:.1f}")
                        self.last_ph = ph_value
                    self.last_ph_update = current_time
            except Exception as e:
                print(f"pH sensor error: {e}")
            print(f"pH read time: {(time.time() - start) * 1000:.2f} ms")

            # Update time-series
            self.times.append(current_time - self.start_time)
            self.temps.append(self.last_temp if self.last_temp != -1 else 0)
            self.phs.append(self.last_ph if self.last_ph != -1 else 0)
            self.luxes.append(self.last_light if self.last_light != -1 else 0)

            # Update graphs every 200ms
            if current_time - self.last_graph_update >= 0.2:
                self.temp_line.set_data(list(self.times), list(self.temps))
                self.ph_line.set_data(list(self.times), list(self.phs))
                self.lux_line.set_data(list(self.times), list(self.luxes))
                for ax in [self.ax_temp, self.ax_ph, self.ax_lux]:
                    ax.relim()
                    ax.autoscale_view()
                self.canvas.draw()
                self.last_graph_update = current_time

            print(f"Total loop time: {(time.time() - loop_start) * 1000:.2f} ms")
            time.sleep(0.05)

    def destroy(self):
        self.running = False
        self.update_thread.join()
        self.root.destroy()

# Main
if __name__ == "__main__":
    root = ttk.Window()
    app = SensorGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.destroy)
    root.mainloop()
