# Scanner

Eine kleine Web-App, die Fotos von Papier in saubere, gerade ausgerichtete
Scans verwandelt — ähnlich wie iScanner oder Adobe Scan, aber selbst
gehostet und ohne App-Store.

## Funktionen

- Foto per Kamera aufnehmen oder als Datei hochladen (auch per Drag & Drop)
- Automatische Erkennung der Papierkanten und Perspektivkorrektur
  (schräg fotografiertes Blatt wird gerade "flachgelegt")
- Knicke/Wölbungen glätten anhand der Textzeilenkrümmung
- Finger/Hand am Rand automatisch erkennen und entfernen (siehe Hinweis unten)
- Drei Ausgabe-Stile: Schwarz-Weiß (klassischer Scanner-Look), Graustufen,
  Farbe (aufgehellt & geschärft)
- Mehrere Seiten nacheinander scannen und gemeinsam verwalten
- Alle Seiten als eine PDF exportieren, oder einzeln/gesammelt in die
  iPhone-Fotomediathek speichern (über den nativen Teilen-Dialog)

## Installation

Python 3.9+ wird benötigt.

```bash
cd scanner-app
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Starten

```bash
python app.py
```

Dann im Browser öffnen: **http://localhost:5000**

Auf dem iPhone im selben WLAN: die lokale IP des Rechners verwenden, z. B.
`http://192.168.1.23:5000` (die IP zeigt `ipconfig` unter Windows bzw.
`ifconfig` / `ip a` unter Mac/Linux). Achte darauf, dass die
Windows-Firewall Python auf privaten Netzwerken erlaubt.

## Auf Render.com hosten (dauerhaft erreichbar, kein lokaler Server nötig)

1. Erstelle ein neues GitHub-Repository (leer) und lade den Inhalt dieses
   Ordners hoch — entweder über die GitHub-Weboberfläche ("Upload files",
   den ganzen Ordnerinhalt reinziehen) oder per `git push`, falls du Git
   nutzt.
2. Auf render.com: **New +** → **Web Service** → das eben erstellte
   Repository auswählen.
3. Render erkennt Python meist automatisch. Falls nicht, manuell setzen:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --workers 2 --timeout 60`
   (Beides steht auch schon in `Procfile` bzw. `requirements.txt`.)
4. **Instance Type:** Free reicht zum Testen.
5. **Create Web Service** klicken — der erste Build dauert ein paar
   Minuten (OpenCV ist ein großes Paket).
6. Du bekommst eine feste URL wie `https://dein-scanner.onrender.com` —
   die funktioniert von überall, nicht nur im selben WLAN.

**Wichtig beim kostenlosen Plan:** Der Dienst "schläft" nach ca. 15
Minuten ohne Anfragen ein; der nächste Aufruf dauert dann ~30–50
Sekunden zum Aufwachen. Das ist normal und kein Fehler. Außerdem ist
der Dateispeicher auf Render *nicht dauerhaft* — hochgeladene/erzeugte
Scans überleben nur bis zum nächsten Neustart des Dienstes, was für den
eigentlichen Scan-und-Download-Ablauf aber unproblematisch ist.

### Als App-Icon auf dem iPhone

Nach dem Deploy: die Render-URL in Safari öffnen → Teilen-Symbol →
**"Zum Home-Bildschirm"**. Damit hast du ein eigenes App-Icon, das die
Seite ohne Adressleiste öffnet, wie eine echte App.

## In die Fotomediathek speichern (iPhone)

Web-Apps im Browser dürfen aus Sicherheitsgründen nicht automatisch und
ohne Zutun in die Fotos-App schreiben. "In Fotos speichern" öffnet
deshalb den nativen iOS-Teilen-Dialog (Web Share API); von dort tippst
du auf **"Sichern"** bzw. **"Bild sichern"**, und das Bild landet direkt
in der Fotomediathek — ganz ohne Zwischenschritt über die Dateien-App.
Bei "Alle in Fotos speichern" bekommst du alle gescannten Seiten auf
einmal in diesem Dialog angeboten (unterstützt in aktuellem iOS Safari).
Falls dein Browser das Teilen mehrerer Bilder nicht unterstützt, öffnet
die App stattdessen jede Seite einzeln zum Speichern.

## Mehrere Seiten & PDF-Export

Nach jedem Scan wird die Seite unten in der Leiste "Gescannte Seiten"
gesammelt. Mit "Nächstes Foto scannen" fügst du weitere Seiten hinzu,
ohne die bisherigen zu verlieren. "Alle als PDF exportieren" fügt alle
gesammelten Seiten in der angezeigten Reihenfolge zu einer einzigen PDF
zusammen. Einzelne Seiten lassen sich über das ×-Symbol auf der
Miniaturansicht wieder entfernen.

## Wie es funktioniert

1. Das Bild wird zur Kantenerkennung verkleinert; die App sucht die
   größte helle Fläche (das Papier) gegenüber dem Hintergrund und
   ersatzweise über Kantenerkennung, falls der Hintergrund zu hell oder
   unruhig ist.
2. Aus der gefundenen Fläche wird ein sauberes, symmetrisches Rechteck
   berechnet (statt eines wackeligen 4-Punkt-Polygons) und
   perspektivisch entzerrt.
3. Randstreifen von sichtbarem Hintergrund werden inhaltsbasiert
   weggeschnitten bzw. wegretuschiert.
4. Anhand der Krümmung der Textzeilen wird eine leichte Wellung/Knickung
   im Papier erkannt und flachgezogen.
5. Hauttonfarbene Bereiche am Bildrand (typischerweise ein haltender
   Finger) werden erkannt und mittels Inpainting rekonstruiert.
6. Je nach gewähltem Stil wird Kontrast/Helligkeit angepasst
   (adaptive Schwellenwertbildung für Schwarz-Weiß, CLAHE für Graustufen
   und Farbe).
7. Das Ergebnis wird als JPG gespeichert; auf Wunsch werden mehrere
   Seiten zu einer PDF zusammengeführt.

Wird keine Papierkante erkannt (z. B. bei sehr unruhigem Hintergrund),
wird das ganze Bild aufbereitet statt zugeschnitten — die App bricht nie
einfach ab.

**Ehrlicher Hinweis zur Knick-Glättung:** Das ist standardmäßig
ausgeschaltet, weil sie bei bereits recht flach fotografierten Seiten
gelegentlich mehr kaputt macht als sie hilft (leichte künstliche Neigung
im Text). Schalte sie gezielt ein, wenn das Papier auf dem Foto sichtbar
gewölbt oder geknickt ist — die App prüft dann zusätzlich, ob mehrere
Textzeilen unabhängig voneinander eine übereinstimmende Krümmung zeigen,
bevor sie etwas verändert, und bricht sonst folgenlos ab.

**Ehrlicher Hinweis zur Fingererkennung:** Das ist eine
Farb-/Heuristik-basierte Erkennung, kein trainiertes KI-Modell wie bei
Adobe Scan. Sie funktioniert zuverlässig, wenn der Finger auf Papier
oder Hintergrund liegt, kann aber keinen Text wiederherstellen, den der
Finger tatsächlich verdeckt hat — der wurde beim Fotografieren schlicht
nie erfasst. Am besten: Finger möglichst weit an den äußersten Rand
halten oder das Blatt an einer Ecke statt in der Mitte fassen.

## Projektstruktur

```
scanner-app/
├── app.py              Flask-Server + Bildverarbeitung (OpenCV) + PDF-Export
├── requirements.txt
├── templates/
│   └── index.html      Seitenstruktur
├── static/
│   ├── style.css        Design
│   └── script.js        Kamera/Upload, Mehrseiten-Session, Teilen/Speichern, API-Aufrufe
└── processed/           Zwischengespeicherte Scans & PDFs (wird automatisch erstellt)
```

## Hinweis zu Datenschutz

Die Verarbeitung läuft komplett lokal auf dem Server, auf dem die App
läuft — es werden keine Bilder an Dritte gesendet. Für den produktiven
Einsatz mit mehreren Nutzern sollte der `processed/`-Ordner regelmäßig
bereinigt werden, da hochgeladene Scans dort als Dateien liegen bleiben.
