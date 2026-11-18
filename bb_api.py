# server.py (Auf dem PC ausführen)
from flask import Flask, jsonify, request
import sqlite3

app = Flask(__name__)
DB_FILE = "beacons.db"

@app.route('/api/beacons', methods=['GET'])
def get_beacons():
    """Schickt alle registrierten Beacons und Räume an die Android App."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT uid, name, naechste_wartung, standort, letztes_gesehen, batterie FROM beacons")
    rows = cursor.fetchall()
    conn.close()
    
    data = []
    for r in rows:
        data.append({
            "uid": r[0], "name": r[1], "wartung": r[2], 
            "standort": r[3], "letztes_gesehen": r[4], "batterie": r[5]
        })
    return jsonify(data)

@app.route('/api/sync', methods=['POST'])
def sync_found_beacons():
    """Empfängt gefundene Beacons vom Smartphone und aktualisiert den PC."""
    found_list = request.json # List of {uid, letztes_gesehen}
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    for item in found_list:
        cursor.execute("UPDATE beacons SET letztes_gesehen = ? WHERE uid = ?", 
                       (item['letztes_gesehen'], item['uid']))
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "updated": len(found_list)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)