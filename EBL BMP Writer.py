import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk, ImageDraw
import PyPhenom as ppi
import math
import threading
import time
import logging
import random

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Determine the proper resampling method
try:
    resample_method = Image.Resampling.LANCZOS
except AttributeError:
    resample_method = Image.LANCZOS

# Global variables
loaded_image = None
image_path = None
current_phenom = None  # The SEM object during scanning
scanning = False       # Flag indicating if a scan is active
visited_points = []    # List of beam positions (ppi.Position) that have been visited
invert_flag = False    # False: normal (expose pixels >= threshold); True: inverted

CANVAS_SIZE = 400  # All canvases are 400x400 pixels

def load_image():
    global loaded_image, image_path
    image_path = filedialog.askopenfilename(
        filetypes=[("Image files", "*.png;*.jpg;*.jpeg;*.bmp"), ("All files", "*.*")]
    )
    if image_path:
        loaded_image = Image.open(image_path)
        update_main_preview()
        update_threshold_display()

def update_main_preview():
    if loaded_image is None:
        return
    preview = loaded_image.copy()
    preview.thumbnail((CANVAS_SIZE, CANVAS_SIZE), resample_method)
    tk_img = ImageTk.PhotoImage(preview)
    main_canvas.delete("all")
    main_canvas.image = tk_img
    main_canvas.create_image(CANVAS_SIZE//2, CANVAS_SIZE//2, image=tk_img)
    main_canvas.create_rectangle(0, 0, CANVAS_SIZE, CANVAS_SIZE, outline="red", width=2)

def update_threshold_display():
    if loaded_image is None:
        return
    thresh = int(threshold_slider.get())
    gray_img = loaded_image.convert("L")
    if invert_flag:
        bin_img = gray_img.point(lambda p: 255 if p < thresh else 0)
    else:
        bin_img = gray_img.point(lambda p: 255 if p >= thresh else 0)
    bin_img = bin_img.resize((CANVAS_SIZE, CANVAS_SIZE), resample_method)
    tk_thresh = ImageTk.PhotoImage(bin_img)
    threshold_canvas.delete("all")
    threshold_canvas.image = tk_thresh
    threshold_canvas.create_image(CANVAS_SIZE//2, CANVAS_SIZE//2, image=tk_thresh)
    threshold_canvas.create_rectangle(0, 0, CANVAS_SIZE, CANVAS_SIZE, outline="red", width=2)

def evenly_sample_points(points, skip_percentage):
    """
    Evenly subsample the list of points.
    If skip_percentage is 0, return points unmodified.
    Otherwise, keep about (100 - skip_percentage)% of the points evenly.
    """
    if skip_percentage <= 0 or not points:
        return points
    keep_ratio = (100 - skip_percentage) / 100.0
    n = len(points)
    num_keep = max(1, int(n * keep_ratio))
    if num_keep >= n:
        return points
    # Compute evenly spaced indices
    if num_keep == 1:
        indices = [n // 2]
    else:
        indices = [int(round(i * (n - 1) / (num_keep - 1))) for i in range(num_keep)]
    return [points[i] for i in indices]

def get_beam_points():
    """
    Generate beam points from the thresholded image.
    User-entered X and Y dimensions are in mm and converted to meters.
    Exposure time is entered in ns and converted to seconds.
    If a skip percentage is provided, the points are evenly sampled.
    If inversion is toggled, points are selected from pixels below the threshold.
    Returns a list of tuples: (ppi.Position, dwell_time).
    """
    try:
        x_dim_m = float(x_entry.get()) * 1e-3  # mm -> m
        y_dim_m = float(y_entry.get()) * 1e-3
        dwell_ns = float(dwell_entry.get())
        dwell_time = dwell_ns / 1e9           # ns -> s
    except ValueError:
        messagebox.showerror("Error", "Please enter valid dimensions and exposure time.")
        return []
    threshold_value = int(threshold_slider.get())
    analysis_img = loaded_image.convert("L").resize((100, 100), resample_method)
    pixels = analysis_img.load()
    width, height = analysis_img.size
    points = []
    for i in range(height):
        for j in range(width):
            if invert_flag:
                condition = (pixels[j, i] < threshold_value)
            else:
                condition = (pixels[j, i] >= threshold_value)
            if condition:
                x = -x_dim_m/2 + j * (x_dim_m / (width - 1))
                y = y_dim_m/2 - i * (y_dim_m / (height - 1))
                points.append((ppi.Position(x, y), dwell_time))
    try:
        skip_percentage = float(skip_entry.get())
    except ValueError:
        skip_percentage = 0
    if skip_percentage > 0:
        points = evenly_sample_points(points, skip_percentage)
    return points

def update_beam_path_preview():
    """
    Redraw the beam path preview canvas using visited_points.
    """
    if loaded_image is None:
        return
    beam_img = Image.new("RGB", (CANVAS_SIZE, CANVAS_SIZE), "white")
    draw = ImageDraw.Draw(beam_img)
    draw.rectangle([0, 0, CANVAS_SIZE, CANVAS_SIZE], outline="red", width=2)
    try:
        x_dim_m = float(x_entry.get()) * 1e-3
        y_dim_m = float(y_entry.get()) * 1e-3
    except ValueError:
        return
    center = (CANVAS_SIZE//2, CANVAS_SIZE//2)
    scale_x = CANVAS_SIZE / x_dim_m
    scale_y = CANVAS_SIZE / y_dim_m
    for pos in visited_points:
        px = center[0] + pos.x * scale_x
        py = center[1] - pos.y * scale_y
        r = 3
        draw.ellipse([px - r, py - r, px + r, py + r], fill="blue")
    tk_beam = ImageTk.PhotoImage(beam_img)
    beam_canvas.delete("all")
    beam_canvas.image = tk_beam
    beam_canvas.create_image(CANVAS_SIZE//2, CANVAS_SIZE//2, image=tk_beam)
    beam_canvas.create_rectangle(0, 0, CANVAS_SIZE, CANVAS_SIZE, outline="red", width=2)

def preview_beam_path():
    """
    Display a preview of the beam path on the beam canvas.
    This function generates the full list of beam points (without scanning) and draws them.
    """
    if loaded_image is None or not image_path:
        messagebox.showerror("Error", "Please load an image first.")
        return
    points = get_beam_points()
    if not points:
        messagebox.showinfo("Preview Beam Path", "No beam points generated from the image.")
        return
    beam_img = Image.new("RGB", (CANVAS_SIZE, CANVAS_SIZE), "white")
    draw = ImageDraw.Draw(beam_img)
    draw.rectangle([0, 0, CANVAS_SIZE, CANVAS_SIZE], outline="red", width=2)
    try:
        x_dim_m = float(x_entry.get()) * 1e-3
        y_dim_m = float(y_entry.get()) * 1e-3
    except ValueError:
        return
    center = (CANVAS_SIZE//2, CANVAS_SIZE//2)
    scale_x = CANVAS_SIZE / x_dim_m
    scale_y = CANVAS_SIZE / y_dim_m
    for (pos, dt) in points:
        px = center[0] + pos.x * scale_x
        py = center[1] - pos.y * scale_y
        r = 3
        draw.ellipse([px - r, py - r, px + r, py + r], fill="blue")
    tk_beam = ImageTk.PhotoImage(beam_img)
    beam_canvas.delete("all")
    beam_canvas.image = tk_beam
    beam_canvas.create_image(CANVAS_SIZE//2, CANVAS_SIZE//2, image=tk_beam)
    beam_canvas.create_rectangle(0, 0, CANVAS_SIZE, CANVAS_SIZE, outline="red", width=2)

def beam_scan_by_rows(beam_points):
    """
    Group beam points by row (using a tolerance on the y coordinate) and move the beam row by row.
    The beam is blanked (ppi.ScanMode.Blank) during transit and switched to active (ppi.ScanMode.Pattern)
    for exposure.
    """
    if not beam_points:
        return
    try:
        y_dim_m = float(y_entry.get()) * 1e-3
    except ValueError:
        return
    tolerance = y_dim_m * 0.01
    rows = []
    current_row = []
    last_y = None
    for pt in beam_points:
        pos = pt[0]
        if last_y is None or abs(pos.y - last_y) < tolerance:
            current_row.append(pt)
            last_y = pos.y
        else:
            rows.append(current_row)
            current_row = [pt]
            last_y = pos.y
    if current_row:
        rows.append(current_row)
    for row in rows:
        if not scanning:
            break
        row.sort(key=lambda pt: pt[0].x)
        try:
            vm = current_phenom.GetSemViewingMode()
            vm.scanMode = ppi.ScanMode.Blank
            current_phenom.SetSemViewingMode(vm)
            first_point = row[0][0]
            current_phenom.MoveTo(first_point)
            time.sleep(0.1)
        except Exception as e:
            logging.error(f"Error moving to row start: {e}")
        try:
            vm = current_phenom.GetSemViewingMode()
            vm.scanMode = ppi.ScanMode.Pattern
            current_phenom.SetSemViewingMode(vm)
        except Exception as e:
            logging.error(f"Error setting pattern mode: {e}")
        for pos, dwell in row:
            if not scanning:
                break
            try:
                current_phenom.MoveTo(pos)
                visited_points.append(pos)
                root.after(0, update_beam_path_preview)
            except Exception as e:
                logging.error(f"Error moving beam in row: {e}")
            time.sleep(dwell)
        try:
            vm = current_phenom.GetSemViewingMode()
            vm.scanMode = ppi.ScanMode.Blank
            current_phenom.SetSemViewingMode(vm)
            time.sleep(0.1)
        except Exception as e:
            logging.error(f"Error blanking beam at row end: {e}")
    stop_scan()

def beam_scan_loop(beam_points):
    beam_scan_by_rows(beam_points)

def start_scan():
    global current_phenom, scanning, visited_points
    if loaded_image is None or not image_path:
        messagebox.showerror("Error", "Please load an image first.")
        return
    beam_points = get_beam_points()
    if not beam_points:
        messagebox.showerror("Error", "No beam points generated from the image.")
        return
    if not messagebox.askyesno("Confirm Pattern",
           f"Start scan with {len(beam_points)} beam points?\nIntensity: {intensity_var.get()}\nBlanking during transit moves will be applied."):
        return
    visited_points = []
    current_phenom = ppi.Phenom("192.168.200.101", "MVE09533711190L", "KKTT820CBX6Q")
    vm = current_phenom.GetSemViewingMode()
    vm.scanMode = ppi.ScanMode.Blank
    current_phenom.SetSemViewingMode(vm)
    scanning = True
    start_btn.config(text="Stop Scan", command=stop_scan)
    threading.Thread(target=beam_scan_loop, args=(beam_points,), daemon=True).start()

def stop_scan():
    global scanning, current_phenom
    scanning = False
    if current_phenom:
        try:
            # Move beam to top left of stage (i.e. x = -X/2, y = +Y/2 in meters)
            x_dim_m = float(x_entry.get()) * 1e-3
            y_dim_m = float(y_entry.get()) * 1e-3
            top_left = ppi.Position(-x_dim_m/2, y_dim_m/2)
            current_phenom.MoveTo(top_left)
            # Set mode to Blank to avoid accidental exposure
            vm = current_phenom.GetSemViewingMode()
            vm.scanMode = ppi.ScanMode.Blank
            current_phenom.SetSemViewingMode(vm)
        except Exception as e:
            logging.error(f"Error moving to top left in stop_scan: {e}")
    start_btn.config(text="Start Scan", command=start_scan)
    messagebox.showinfo("Scan Stopped", "Patterning stopped. SEM is now blank and moved to top left.")

def toggle_invert():
    global invert_flag
    invert_flag = not invert_flag
    if invert_flag:
        invert_btn.config(text="Invert: ON")
    else:
        invert_btn.config(text="Invert: OFF")
    update_threshold_display()

# -------------------------
# Build the UI
# -------------------------
root = tk.Tk()
root.title("SEM Patterning UI")
root.geometry("1300x1000")

# Control Frame
control_frame = tk.Frame(root)
control_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

load_btn = tk.Button(control_frame, text="Load Image", command=load_image)
load_btn.grid(row=0, column=0, padx=5, pady=5)

tk.Label(control_frame, text="X (mm):").grid(row=0, column=1, padx=5, pady=5)
x_entry = tk.Entry(control_frame, width=10)
x_entry.grid(row=0, column=2, padx=5, pady=5)
x_entry.insert(0, "50")

tk.Label(control_frame, text="Y (mm):").grid(row=0, column=3, padx=5, pady=5)
y_entry = tk.Entry(control_frame, width=10)
y_entry.grid(row=0, column=4, padx=5, pady=5)
y_entry.insert(0, "50")

tk.Label(control_frame, text="Exposure Time (ns):").grid(row=0, column=5, padx=5, pady=5)
dwell_entry = tk.Entry(control_frame, width=10)
dwell_entry.grid(row=0, column=6, padx=5, pady=5)
dwell_entry.insert(0, "200")  # Default 200 ns

tk.Label(control_frame, text="Intensity:").grid(row=0, column=7, padx=5, pady=5)
intensity_var = tk.StringVar(value="Low")
intensity_menu = tk.OptionMenu(control_frame, intensity_var, "Low", "Medium", "High")
intensity_menu.grid(row=0, column=8, padx=5, pady=5)

tk.Label(control_frame, text="Threshold:").grid(row=0, column=9, padx=5, pady=5)
threshold_slider = tk.Scale(control_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                            command=lambda v: update_threshold_display())
threshold_slider.set(240)
threshold_slider.grid(row=0, column=10, padx=5, pady=5)

tk.Label(control_frame, text="Skip (%)").grid(row=0, column=11, padx=5, pady=5)
skip_entry = tk.Entry(control_frame, width=10)
skip_entry.grid(row=0, column=12, padx=5, pady=5)
skip_entry.insert(0, "0")

invert_btn = tk.Button(control_frame, text="Invert: OFF", command=toggle_invert)
invert_btn.grid(row=0, column=13, padx=5, pady=5)

preview_btn = tk.Button(control_frame, text="Preview Beam Path", command=preview_beam_path)
preview_btn.grid(row=0, column=14, padx=5, pady=5)

start_btn = tk.Button(control_frame, text="Start Scan", command=start_scan)
start_btn.grid(row=0, column=15, padx=5, pady=5)

# Display Frame for previews (side by side)
display_frame = tk.Frame(root)
display_frame.pack(side=tk.TOP, fill=tk.BOTH, padx=10, pady=10)

main_canvas = tk.Canvas(display_frame, width=CANVAS_SIZE, height=CANVAS_SIZE, bg="grey")
main_canvas.pack(side=tk.LEFT, padx=5, pady=5)

threshold_canvas = tk.Canvas(display_frame, width=CANVAS_SIZE, height=CANVAS_SIZE, bg="grey")
threshold_canvas.pack(side=tk.LEFT, padx=5, pady=5)

beam_canvas = tk.Canvas(display_frame, width=CANVAS_SIZE, height=CANVAS_SIZE, bg="grey")
beam_canvas.pack(side=tk.LEFT, padx=5, pady=5)

root.mainloop()
