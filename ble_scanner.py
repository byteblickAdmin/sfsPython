import asyncio
import datetime
import sqlite3
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from bleak import BleakClient, BleakScanner

DB_FILE = "beacons.db"

# ByteBlick Farbpalette & Styling-Konstanten
BG_DARK = "#121820"        # Tiefes Anthrazit/Slate für den Haupthintergrund
BG_CARD = "#1A222D"        # Etwas helleres Grau für Karten/Container
TEXT_LIGHT = "#F3F4F6"      # Helles Grau/Weiß für gut lesbaren Text
TEXT_MUTED = "#9CA3AF"      # Gedimmtes Grau für Untertitel und Labels
ACCENT_BLUE = "#00D2FF"     # Technisches Cyan/Blau als Highlight-Farbe
ACCENT_HOVER = "#00A8CC"    # Dunkleres Cyan für Button-Hover-Effekte
BORDER_COLOR = "#2A3748"    # Dezente Rahmenlinien für Struktur
NEON_GREEN = "#00FF66"      # Kräftiges Grün für aktive Geräte in Reichweite

# GATT UUIDs gemäß Dokumentation
UUID_FF30_UNLOCK = "0000ff30-0000-1000-8000-00805f9b34fb"  # PIN Entsperrung
UUID_FF23_BATTERY = "0000ff23-0000-1000-8000-00805f9b34fb" # Verbundene Batterie

def init_db():
    """Initialisiert die SQLite-Datenbank beim Start des Programms."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS beacons (
            uid TEXT PRIMARY KEY,
            name TEXT,
            naechste_wartung TEXT,
            standort TEXT,
            letztes_gesehen TEXT,
            batterie TEXT
        )
    ''')
    
    cursor.execute("PRAGMA table_info(beacons)")
    columns = [col[1] for col in cursor.fetchall()]
    if "letztes_gesehen" not in columns:
        cursor.execute("ALTER TABLE beacons ADD COLUMN letztes_gesehen TEXT")
    if "batterie" not in columns:
        cursor.execute("ALTER TABLE beacons ADD COLUMN batterie TEXT")
        
    conn.commit()
    conn.close()

class ByteBlickBeaconApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ByteBlick // Raum- & Beacon-Management")
        self.root.geometry("1300x680")
        self.root.minsize(950, 550)
        self.root.configure(bg=BG_DARK)
        
        self.live_rssi = {}       
        self.live_battery = {}    
        self.rssi_history = {}    
        self.last_seen_timestamp = {} 
        self.scanned_devices = {} 
        self.is_radar_running = False
        
        init_db()
        self.apply_styles()
        
        main_container = tk.Frame(root, bg=BG_DARK)
        main_container.pack(fill="both", expand=True, padx=15, pady=15)
        
        # --- HEADER-BERICH ---
        header_frame = tk.Frame(main_container, bg=BG_DARK)
        header_frame.pack(fill="x", pady=(0, 15))
        
        tk.Label(header_frame, text="BYTEBLICK // ROOM & BEACON TRACKING", 
                 font=("Segoe UI", 14, "bold"), fg=ACCENT_BLUE, bg=BG_DARK).pack(side="left")
        tk.Label(header_frame, text="Zonen-Überwachung & 7s-Timeout Radar", 
                 font=("Segoe UI", 9), fg=TEXT_MUTED, bg=BG_DARK).pack(side="right", anchor="s")

        # --- STEUERUNGS- UND FILTERLEISTE ---
        control_frame = tk.Frame(main_container, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1)
        control_frame.pack(fill="x", pady=(0, 15), ipady=8, ipadx=8)
        
        self.btn_scan_10s = tk.Button(control_frame, text="⚡ 10s Scan (In der Nähe)", 
                                    font=("Segoe UI", 9, "bold"), bg=ACCENT_BLUE, fg=BG_DARK, 
                                    activebackground=ACCENT_HOVER, activeforeground=BG_DARK,
                                    relief="flat", cursor="hand2", padx=8, pady=6, 
                                    command=self.start_10s_scan)
        self.btn_scan_10s.pack(side="left", padx=3)

        self.btn_radar = tk.Button(control_frame, text="📡 Live-Radar (Räume)", 
                                   font=("Segoe UI", 9, "bold"), bg="#253242", fg=TEXT_LIGHT, 
                                   activebackground="#334458", activeforeground=TEXT_LIGHT,
                                   relief="flat", cursor="hand2", padx=8, pady=6, 
                                   command=self.toggle_radar)
        self.btn_radar.pack(side="left", padx=3)
        
        self.btn_refresh = tk.Button(control_frame, text="🔄 Aktualisieren", 
                                     font=("Segoe UI", 9), bg="#253242", fg=TEXT_LIGHT, 
                                     activebackground="#334458", activeforeground=TEXT_LIGHT,
                                     relief="flat", cursor="hand2", padx=8, pady=6, 
                                     command=self.refresh_data)
        self.btn_refresh.pack(side="left", padx=3)
        
        tk.Label(control_frame, text="Raum:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_CARD).pack(side="left", padx=(15, 5))
        self.room_var = tk.StringVar(value="Alle Räume")
        self.room_combo = ttk.Combobox(control_frame, textvariable=self.room_var, state="readonly", width=18, font=("Segoe UI", 9))
        self.room_combo.pack(side="left", padx=3)
        self.room_combo.bind("<<ComboboxSelected>>", lambda e: self.load_radar_data())

        filter_container = tk.Frame(control_frame, bg=BG_CARD)
        filter_container.pack(side="right", padx=5)
        tk.Label(filter_container, text="Suche:", font=("Segoe UI", 9), fg=TEXT_MUTED, bg=BG_CARD).pack(side="left", padx=(0, 5))
        
        self.filter_var = tk.StringVar()
        # MODERNISIERT: trace_add statt trace für Python 3.14 / Tcl 9 Kompatibilität
        self.filter_var.trace_add("write", lambda *args: self.filter_data())
        
        self.filter_entry = tk.Entry(filter_container, textvariable=self.filter_var, 
                                   font=("Segoe UI", 9), bg="#121820", fg=TEXT_LIGHT, 
                                   insertbackground=TEXT_LIGHT, relief="flat", width=16)
        self.filter_entry.pack(side="left", ipady=4, ipadx=4)

        # --- TABELLEN-BEREICH ---
        table_frame = tk.Frame(main_container, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1)
        table_frame.pack(fill="both", expand=True, pady=(0, 15))
        
        columns = ("status", "uid", "name", "standort", "batterie", "letztes_gesehen", "rssi", "naechste_wartung")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse", style="Custom.Treeview")
        
        self.tree.heading("status", text="Reichweite")
        self.tree.heading("uid", text="UID / Adresse")
        self.tree.heading("name", text="Name")
        self.tree.heading("standort", text="Raum")
        self.tree.heading("batterie", text="Batterie 🔋")
        self.tree.heading("letztes_gesehen", text="Zuletzt gesehen")
        self.tree.heading("rssi", text="Signal / Nähe")
        self.tree.heading("naechste_wartung", text="Wartung")
        
        self.tree.column("status", width=110, anchor="center")
        self.tree.column("uid", width=150, anchor="w")
        self.tree.column("name", width=120, anchor="w")
        self.tree.column("standort", width=110, anchor="w")
        self.tree.column("batterie", width=110, anchor="center")
        self.tree.column("letztes_gesehen", width=130, anchor="center")
        self.tree.column("rssi", width=140, anchor="center")
        self.tree.column("naechste_wartung", width=100, anchor="center")
        
        self.tree.tag_configure("online", foreground=NEON_GREEN)
        self.tree.tag_configure("offline", foreground=TEXT_MUTED)
        
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        scrollbar.pack(side="right", fill="y")
        
        self.tree.bind("<<TreeviewSelect>>", self.on_select_beacon)
        
        # --- UNTERER BEREICH: BEARBEITUNGSFORMULAR & GATT-BUTTON ---
        edit_frame = tk.Frame(main_container, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1)
        edit_frame.pack(fill="x", ipady=10, ipadx=10)
        
        edit_title = tk.Label(edit_frame, text="BEACON-ZUORDNUNG & GATT-BATTERIEABFRAGE", font=("Segoe UI", 9, "bold"), fg=ACCENT_BLUE, bg=BG_CARD)
        edit_title.grid(row=0, column=0, columnspan=6, sticky="w", padx=10, pady=(5, 10))
        
        tk.Label(edit_frame, text="UID:", font=("Segoe UI", 9), fg=TEXT_MUTED, bg=BG_CARD).grid(row=1, column=0, sticky="w", padx=10, pady=5)
        self.lbl_uid_val = tk.Label(edit_frame, text="---", font=("Segoe UI", 10, "bold"), fg=ACCENT_BLUE, bg=BG_CARD)
        self.lbl_uid_val.grid(row=1, column=1, sticky="w", padx=10, pady=5)
        
        tk.Label(edit_frame, text="Name:", font=("Segoe UI", 9), fg=TEXT_MUTED, bg=BG_CARD).grid(row=1, column=2, sticky="w", padx=10, pady=5)
        self.ent_name = tk.Entry(edit_frame, font=("Segoe UI", 10), bg="#121820", fg=TEXT_LIGHT, insertbackground=TEXT_LIGHT, relief="flat", width=18)
        self.ent_name.grid(row=1, column=3, sticky="w", padx=10, pady=5, ipady=3)
        
        tk.Label(edit_frame, text="Raum:", font=("Segoe UI", 9), fg=TEXT_MUTED, bg=BG_CARD).grid(row=2, column=0, sticky="w", padx=10, pady=5)
        self.ent_standort = tk.Entry(edit_frame, font=("Segoe UI", 10), bg="#121820", fg=TEXT_LIGHT, insertbackground=TEXT_LIGHT, relief="flat", width=18)
        self.ent_standort.grid(row=2, column=1, sticky="w", padx=10, pady=5, ipady=3)
        
        tk.Label(edit_frame, text="Wartung:", font=("Segoe UI", 9), fg=TEXT_MUTED, bg=BG_CARD).grid(row=2, column=2, sticky="w", padx=10, pady=5)
        self.ent_wartung = tk.Entry(edit_frame, font=("Segoe UI", 10), bg="#121820", fg=TEXT_LIGHT, insertbackground=TEXT_LIGHT, relief="flat", width=18)
        self.ent_wartung.grid(row=2, column=3, sticky="w", padx=10, pady=5, ipady=3)
        
        self.btn_gatt_battery = tk.Button(edit_frame, text="🔋 Batterie lesen\n(GATT)", 
                                         font=("Segoe UI", 9, "bold"), bg="#253242", fg=ACCENT_BLUE, 
                                         activebackground=ACCENT_BLUE, activeforeground=BG_DARK,
                                         relief="flat", cursor="hand2", padx=10, pady=4, 
                                         command=self.read_battery_via_gatt_threaded)
        self.btn_gatt_battery.grid(row=1, column=4, rowspan=2, padx=10, sticky="nsew")

        self.btn_save = tk.Button(edit_frame, text="💾 Speichern", 
                                font=("Segoe UI", 10, "bold"), bg=ACCENT_BLUE, fg=BG_DARK, 
                                activebackground=ACCENT_HOVER, activeforeground=BG_DARK,
                                relief="flat", cursor="hand2", padx=15, pady=8, 
                                command=self.save_beacon_changes)
        self.btn_save.grid(row=1, column=5, rowspan=2, padx=10, sticky="nsew")

        self.update_room_dropdown()
        self.load_radar_data()

    def apply_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Custom.Treeview", background=BG_CARD, foreground=TEXT_LIGHT, fieldbackground=BG_CARD, rowheight=26, borderwidth=0)
        style.configure("Custom.Treeview.Heading", background="#253242", foreground=TEXT_LIGHT, font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Custom.Treeview", background=[('selected', ACCENT_BLUE)], foreground=[('selected', BG_DARK)])

    def update_room_dropdown(self):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT standort FROM beacons WHERE standort IS NOT NULL AND standort != ''")
        rooms = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        current_selection = self.room_var.get()
        room_list = ["Alle Räume"] + sorted(rooms)
        self.room_combo["values"] = room_list
        
        if current_selection in room_list:
            self.room_var.set(current_selection)
        else:
            self.room_var.set("Alle Räume")

    def load_10s_scan_data(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        self.all_rows = []
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        for uid, data in self.scanned_devices.items():
            name = data["name"]
            rssi = data["rssi"]
            bat = data["battery"]
            
            cursor.execute("SELECT naechste_wartung, standort, letztes_gesehen, batterie FROM beacons WHERE uid = ?", (uid,))
            db_row = cursor.fetchone()
            
            wartung = db_row[0] if db_row and db_row[0] else "Ausstehend"
            standort = db_row[1] if db_row and db_row[1] else "Unbekannt"
            letztes_gesehen = db_row[2] if db_row and db_row[2] else "Gerade eben"
            if db_row and db_row[3] and bat == "N/V":
                bat = db_row[3]
            
            bars = self.get_signal_meter(rssi)
            rssi_text = f"{rssi} dBm {bars}"
            status = "🟢 In Reichweite"
            
            self.all_rows.append((status, uid, name, standort, bat, letztes_gesehen, rssi_text, wartung, "online"))
            
        conn.close()
        self.filter_data()

    def load_radar_data(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        selected_room = self.room_var.get()
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        if selected_room == "Alle Räume" or not selected_room:
            cursor.execute("SELECT uid, name, naechste_wartung, standort, letztes_gesehen, batterie FROM beacons")
        else:
            cursor.execute("SELECT uid, name, naechste_wartung, standort, letztes_gesehen, batterie FROM beacons WHERE standort = ?", (selected_room,))
            
        db_rows = cursor.fetchall()
        conn.close()
        
        current_time = time.time()
        self.all_rows = []
        
        for row in db_rows:
            uid, name, wartung, standort, letztes_gesehen, batterie = row
            standort_str = standort if standort else "Unbekannt"
            letztes_str = letztes_gesehen if letztes_gesehen else "Noch nie"
            beacon_name = name if name else "Unbekannter Beacon"
            bat_str = batterie if batterie else "N/V"
            
            # --- 7 SEKUNDEN TIMEOUT-TOLERANZ ---
            is_online = False
            if uid in self.last_seen_timestamp:
                if current_time - self.last_seen_timestamp[uid] < 7.0:
                    is_online = True
            
            if is_online and uid in self.live_rssi:
                status = "🟢 In Reichweite"
                rssi_val = self.live_rssi[uid]
                bars = self.get_signal_meter(rssi_val)
                rssi_text = f"{rssi_val} dBm {bars}"
                if uid in self.live_battery:
                    bat_str = self.live_battery[uid]
                tag = "online"
            else:
                status = "⚪ Außer Reichweite"
                rssi_text = "---"
                tag = "offline"
            
            self.all_rows.append((status, uid, beacon_name, standort_str, bat_str, letztes_str, rssi_text, wartung, tag))
        
        self.filter_data()

    def refresh_data(self):
        self.live_rssi.clear()
        self.live_battery.clear()
        self.rssi_history.clear()
        self.last_seen_timestamp.clear()
        self.scanned_devices.clear()
        self.update_room_dropdown()
        self.load_radar_data()

    def get_signal_meter(self, rssi):
        if rssi > -55:
            return "📶 [████] Extrem nah"
        elif rssi > -70:
            return "📶 [███░] Nah"
        elif rssi > -85:
            return "📶 [██░░] Mittel"
        else:
            return "📶 [█░░░] Weit weg"

    def filter_data(self):
        query = self.filter_var.get().lower()
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        for row in self.all_rows:
            status, uid, name, standort, batterie, letztes_gesehen, rssi, wartung, tag = row
            if any(query in str(field).lower() for field in [status, uid, name, standort, batterie, letztes_gesehen, rssi, wartung]):
                self.tree.insert("", "end", values=(status, uid, name, standort, batterie, letztes_gesehen, rssi, wartung), tags=(tag,))

    def on_select_beacon(self, event):
        selected_item = self.tree.selection()
        if not selected_item:
            return
        values = self.tree.item(selected_item, "values")
        if values:
            _, uid, name, standort, _, _, _, wartung = values
            self.lbl_uid_val.config(text=uid)
            self.ent_name.delete(0, tk.END); self.ent_name.insert(0, name)
            self.ent_standort.delete(0, tk.END); self.ent_standort.insert(0, standort)
            self.ent_wartung.delete(0, tk.END); self.ent_wartung.insert(0, wartung)

    def save_beacon_changes(self):
        uid = self.lbl_uid_val.cget("text")
        if uid == "---" or not uid:
            messagebox.showwarning("Hinweis", "Bitte wählen Sie zuerst einen Beacon aus.")
            return
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE beacons SET name = ?, naechste_wartung = ?, standort = ? WHERE uid = ?", 
                       (self.ent_name.get(), self.ent_wartung.get(), self.ent_standort.get(), uid))
        conn.commit()
        conn.close()
        
        messagebox.showinfo("Erfolg", "Beacon-Konfiguration aktualisiert.")
        self.update_room_dropdown()
        self.load_radar_data()

    def read_battery_via_gatt_threaded(self):
        uid = self.lbl_uid_val.cget("text")
        if uid == "---" or not uid:
            messagebox.showwarning("Hinweis", "Bitte wählen Sie zuerst einen Beacon aus der Tabelle aus.")
            return
        
        self.btn_gatt_battery.config(state="disabled", text="⏳ Verbinde...")
        threading.Thread(target=lambda: asyncio.run(self.async_read_gatt_battery(uid)), daemon=True).start()

    async def async_read_gatt_battery(self, address):
        try:
            async with BleakClient(address) as client:
                await client.write_gatt_char(UUID_FF30_UNLOCK, b"686868", response=True)
                st = await client.read_gatt_char(UUID_FF30_UNLOCK)
                
                if not st or st[0] != 0x01:
                    self.root.after(0, lambda: self.gatt_battery_result(address, None, "Entsperren fehlgeschlagen (PIN falsch?)"))
                    return

                data = await client.read_gatt_char(UUID_FF23_BATTERY)
                if len(data) >= 5:
                    mv = (data[2] << 8) | data[3]
                    percent = data[4]
                    bat_str = f"{mv}mV · {percent}%"
                    self.root.after(0, lambda: self.gatt_battery_result(address, bat_str, None))
                else:
                    self.root.after(0, lambda: self.gatt_battery_result(address, None, "Ungültiges Datenformat von 0xFF23"))
        except Exception as e:
            self.root.after(0, lambda: self.gatt_battery_result(address, None, str(e)))

    def gatt_battery_result(self, address, bat_str, error_msg):
        self.btn_gatt_battery.config(state="normal", text="🔋 Batterie lesen\n(GATT)")
        if error_msg:
            messagebox.showerror("GATT Fehler", f"Konnte Batterie nicht auslesen:\n{error_msg}")
        else:
            self.live_battery[address] = bat_str
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute("UPDATE beacons SET batterie = ? WHERE uid = ?", (bat_str, address))
            conn.commit()
            conn.close()
            
            self.load_radar_data()
            messagebox.showinfo("Erfolg", f"Batteriestand erfolgreich ausgelesen: {bat_str}")

    def update_beacon_found_in_db(self, uid, scanned_name):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        beacon_name = scanned_name if scanned_name else "Unbekannter Beacon"
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT uid, name FROM beacons WHERE uid = ?", (uid,))
        exists = cursor.fetchone()
        
        if not exists:
            cursor.execute('''
                INSERT INTO beacons (uid, name, naechste_wartung, standort, letztes_gesehen, batterie)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (uid, beacon_name, "Ausstehend", "Unbekannt", timestamp, "N/V"))
        else:
            if exists[1] == "Unbekannter Beacon" and scanned_name:
                cursor.execute("UPDATE beacons SET name = ?, letztes_gesehen = ? WHERE uid = ?", (beacon_name, timestamp, uid))
            else:
                cursor.execute("UPDATE beacons SET letztes_gesehen = ? WHERE uid = ?", (timestamp, uid))
        conn.commit()
        conn.close()

    def start_10s_scan(self):
        self.scanned_devices.clear()
        self.btn_scan_10s.config(state="disabled", text="⚡ Scan läuft...")
        threading.Thread(target=self.run_10s_scan_thread, daemon=True).start()

    def run_10s_scan_thread(self):
        async def scan():
            def detection_callback(device, advertisement_data):
                uid = device.address
                name = device.name or advertisement_data.local_name
                rssi = advertisement_data.rssi
                
                self.scanned_devices[uid] = {"name": name or "Unbekannter Beacon", "rssi": rssi, "battery": "N/V"}
                self.update_beacon_found_in_db(uid, name)

            scanner = BleakScanner(detection_callback=detection_callback)
            await scanner.start()
            await asyncio.sleep(10.0)
            await scanner.stop()

        asyncio.run(scan())
        self.root.after(0, self.finish_10s_scan)

    def finish_10s_scan(self):
        self.btn_scan_10s.config(state="normal", text="⚡ 10s Scan (In der Nähe)")
        self.update_room_dropdown()
        self.load_10s_scan_data()
        messagebox.showinfo("Scan beendet", f"{len(self.scanned_devices)} Gerät(e) in der Nähe gefunden.")

    def toggle_radar(self):
        if not self.is_radar_running:
            self.is_radar_running = True
            self.btn_radar.config(text="🛑 Radar stoppen", bg="#FF4444", fg=TEXT_LIGHT)
            threading.Thread(target=self.run_continuous_radar, daemon=True).start()
        else:
            self.is_radar_running = False
            self.btn_radar.config(text="📡 Live-Radar (Räume)", bg="#253242", fg=TEXT_LIGHT)

    def run_continuous_radar(self):
        async def radar_loop():
            def detection_callback(device, advertisement_data):
                uid = device.address
                raw_rssi = advertisement_data.rssi
                name = device.name or advertisement_data.local_name
                
                # Live-Zeitstempel bei jedem eintreffenden Funksignal aktualisieren
                self.last_seen_timestamp[uid] = time.time()

                if uid not in self.rssi_history:
                    self.rssi_history[uid] = []
                history = self.rssi_history[uid]
                history.append(raw_rssi)
                if len(history) > 3:
                    history.pop(0)
                
                smoothed_rssi = int(sum(history) / len(history))
                self.live_rssi[uid] = smoothed_rssi

                self.update_beacon_found_in_db(uid, name)

            scanner = BleakScanner(detection_callback=detection_callback)
            await scanner.start()
            
            while self.is_radar_running:
                await asyncio.sleep(0.5)
                self.root.after(0, self.load_radar_data)
                
            await scanner.stop()

        asyncio.run(radar_loop())

if __name__ == "__main__":
    root = tk.Tk()
    app = ByteBlickBeaconApp(root)
    root.mainloop()