import os
import re
import threading
import random
import time
import json
from datetime import datetime
import pygame
import yt_dlp
import requests
from PIL import Image, ImageTk, ImageFilter, ImageDraw, ImageFont, ImageOps

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, simpledialog

# Importation de mutagen pour récupérer la durée d'un MP3 et les tags
try:
    from mutagen.mp3 import MP3
except ImportError:
    MP3 = None

# --- Gestion des données de l'application ---

APP_DATA_FILE = "app_data.json"
DEFAULT_DATA = {
    "playlists": {"Favoris": []},
    "history": [],
    "settings": {
        "theme": "Dark",
        "window_size": "950x900",   # Taille par défaut de la fenêtre
        "window_state": "zoomed"     # État par défaut de la fenêtre
    },
    "tags": {}  # Stockage des tags par fichier
}

def load_app_data():
    """Charge les données de l'application depuis un fichier JSON."""
    try:
        with open(APP_DATA_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_DATA.copy()

def save_app_data(data):
    """Enregistre les données de l'application dans un fichier JSON."""
    with open(APP_DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

app_data = load_app_data()

# --- Initialisation de pygame ---

os.environ["SDL_VIDEODRIVER"] = "dummy"
pygame.mixer.init()
pygame.display.init()

# --- Configuration de CustomTkinter ---
ctk.set_appearance_mode(app_data["settings"].get("theme", "Dark"))
ctk.set_default_color_theme("green")

# --- Variables Globales ---

audio_duration = 0  
playback_start_time = None
total_paused_duration = 0
last_pause_time = None
is_paused = False
cover_img_global = None
loop_enabled = False

# Gestion de la playlist active
current_playlist = None            # Nom de la playlist en cours
current_playlist_index = 0         # Index du morceau actuellement joué
current_playlist_selected = None   # Playlist affichée dans l'onglet Playlists

# Dictionnaires et listes de gestion
playlists = {}                   # Autres playlists que "Favoris"
library_items = {}               # {filename: frame_widget}
library_title_widgets = {}       # Pour la mise en évidence dans la bibliothèque

# --- Fonctions utilitaires ---

def round_rectangle(canvas, x1, y1, x2, y2, radius=6, **kwargs):
    """Dessine un rectangle aux coins arrondis sur le canvas."""
    if x2 - x1 < 2 * radius:
        radius = (x2 - x1) / 2
    if y2 - y1 < 2 * radius:
        radius = (y2 - y1) / 2
    points = [
        x1+radius, y1,
        x2-radius, y1,
        x2, y1,
        x2, y1+radius,
        x2, y2-radius,
        x2, y2,
        x2-radius, y2,
        x1+radius, y2,
        x1, y2,
        x1, y2-radius,
        x1, y1+radius,
        x1, y1
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)

def add_rounded_corners(im, radius):
    """
    Ajoute des coins arrondis à une image.
    
    :param im: Image PIL
    :param radius: Rayon des coins
    :return: Image modifiée avec transparence aux coins
    """
    im = im.convert("RGBA")
    circle = Image.new('L', (radius * 2, radius * 2), 0)
    draw = ImageDraw.Draw(circle)
    draw.ellipse((0, 0, radius * 2, radius * 2), fill=255)
    alpha = Image.new('L', im.size, 255)
    w, h = im.size
    alpha.paste(circle.crop((0, 0, radius, radius)), (0, 0))
    alpha.paste(circle.crop((radius, 0, radius * 2, radius)), (w - radius, 0))
    alpha.paste(circle.crop((0, radius, radius, radius * 2)), (0, h - radius))
    alpha.paste(circle.crop((radius, radius, radius * 2, radius * 2)), (w - radius, h - radius))
    im.putalpha(alpha)
    return im

def create_default_cover(size=(300, 300)):
    """
    Crée une pochette par défaut en cas d'absence d'image.
    
    :param size: Taille de l'image
    :return: Image PIL générée
    """
    img = Image.new("RGB", size, color="#333333")
    draw = ImageDraw.Draw(img)
    font_size = int(size[0] // 10)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    text = "No Cover"
    text_width, text_height = draw.textsize(text, font=font)
    pos = ((size[0] - text_width) // 2, (size[1] - text_height) // 2)
    draw.text(pos, text, fill="#FFFFFF", font=font)
    return img

def notify(message):
    """Affiche une notification discrète en haut à gauche."""
    notif = ctk.CTkLabel(root, text=message, fg_color="#444444", text_color="white")
    notif.place(relx=0.01, rely=0.01, anchor="nw")
    root.after(3000, notif.destroy)

# --- Fonctions de téléchargement et lecture ---

def paste_url():
    """Colle l'URL du presse-papiers dans le champ dédié."""
    try:
        clipboard = root.clipboard_get()
        youtube_url_entry.delete(0, tk.END)
        youtube_url_entry.insert(0, clipboard)
    except Exception:
        messagebox.showerror("Erreur", "Impossible de récupérer le presse-papiers.")

def download_youtube():
    """Télécharge l'audio depuis une URL YouTube."""
    url = youtube_url_entry.get()
    if not url:
        messagebox.showerror("Erreur", "Veuillez entrer une URL YouTube valide.")
        return
    youtube_url_entry.delete(0, tk.END)
    download_youtube_button.configure(state=ctk.DISABLED)
    status_label.configure(text="Téléchargement en cours...")
    progress_bar.pack(fill=ctk.X, pady=5)
    download_percentage_label.pack(pady=5)
    progress_bar.set(0)
    download_percentage_label.configure(text="0%")

    def download_thread():
        try:
            os.makedirs("audio", exist_ok=True)
            ffmpeg_path = find_ffmpeg()
            if not ffmpeg_path:
                messagebox.showerror("Erreur", "FFmpeg introuvable.")
                return
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join("audio", '%(title)s.%(ext)s'),
                'noplaylist': True,
                'ffmpeg_location': ffmpeg_path,
                'progress_hooks': [update_progress],
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                downloaded_file = ydl.prepare_filename(info_dict)
            cleaned_title = re.sub(r'[<>:"/\\|?*]', '', info_dict.get('title', 'audio'))
            output_filename = os.path.join("audio", f"{cleaned_title}.mp3")
            os.system(f'{ffmpeg_path} -i "{downloaded_file}" -vn -ar 44100 -ac 2 -b:a 192k "{output_filename}"')
            if os.path.exists(downloaded_file):
                os.remove(downloaded_file)
            if 'thumbnail' in info_dict and info_dict['thumbnail']:
                thumbnail_url = info_dict['thumbnail']
                try:
                    response = requests.get(thumbnail_url)
                    if response.status_code == 200:
                        os.makedirs("Pochette_album", exist_ok=True)
                        thumbnail_path = os.path.join("Pochette_album", f"{cleaned_title}.jpg")
                        with open(thumbnail_path, "wb") as f:
                            f.write(response.content)
                except Exception as e:
                    print(f"Erreur téléchargement miniature : {e}")
            messagebox.showinfo("Succès", f"Téléchargement terminé:\n{output_filename}")
            status_label.configure(text="Prêt")
            update_library_view()
            update_playlists_view()
        except Exception as e:
            messagebox.showerror("Erreur", f"Une erreur est survenue: {str(e)}")
            status_label.configure(text="Erreur")
        finally:
            download_youtube_button.configure(state=ctk.NORMAL)
            progress_bar.pack_forget()
            download_percentage_label.pack_forget()
            progress_bar.set(0)

    threading.Thread(target=download_thread).start()

def update_progress(d):
    """Met à jour la barre de progression durant le téléchargement."""
    if d['status'] == 'downloading':
        percent_str = re.search(r'(\d+\.\d+)%', d['_percent_str'])
        if percent_str:
            percent = float(percent_str.group(1))
            progress_bar.set(percent / 100)
            download_percentage_label.configure(text=f"{int(percent)}%")
            root.update_idletasks()

def find_ffmpeg():
    """Retourne le chemin vers FFmpeg s'il est disponible."""
    if os.system("ffmpeg -version") == 0:
        return "ffmpeg"
    common_path = r"C:\ffmpeg\bin\ffmpeg.exe"
    if os.path.exists(common_path):
        return common_path
    return None

def play_audio_by_filename(filename, playlist_name=None):
    """
    Joue un fichier audio et gère l'affichage de la pochette, la lecture et la mise à jour de la playlist.
    
    :param filename: Nom du fichier audio
    :param playlist_name: Nom de la playlist si applicable
    """
    global audio_duration, playback_start_time, total_paused_duration, is_paused, cover_img_global, current_playlist, current_playlist_index
    selected_file = os.path.join("audio", filename)
    try:
        pygame.mixer.music.load(selected_file)
    except Exception as e:
        messagebox.showerror("Erreur", f"Impossible de lire le fichier : {e}")
        return
    pygame.mixer.music.play()
    pygame.mixer.music.set_endevent(pygame.USEREVENT)
    update_now_playing(filename)
    playback_start_time = time.time()
    total_paused_duration = 0
    last_pause_time = None
    is_paused = False
    current_time_label.configure(text="0:00")
    
    # Récupération de la durée audio
    if MP3 is not None:
        try:
            audio = MP3(selected_file)
            audio_duration = audio.info.length
            total_time_label.configure(text=format_time(audio_duration))
        except Exception:
            audio_duration = 0
            total_time_label.configure(text="0:00")
    else:
        audio_duration = 0
        total_time_label.configure(text="0:00")
    
    # Affichage de la pochette
    base_name = os.path.splitext(filename)[0]
    cover_path = os.path.join("Pochette_album", f"{base_name}.jpg")
    if not os.path.exists(cover_path):
        img = create_default_cover(size=(300,300))
    else:
        img = Image.open(cover_path)
    try:
        cover_clear = ImageOps.fit(img, (300,300), method=Image.LANCZOS, centering=(0.5,0.5))
        bg_size = (340,340)
        blurred = cover_clear.resize(bg_size).filter(ImageFilter.GaussianBlur(radius=10))
        composite = blurred.copy()
        offset = ((bg_size[0]-cover_clear.width)//2, (bg_size[1]-cover_clear.height)//2)
        composite.paste(cover_clear, offset)
        final_img = add_rounded_corners(composite, radius=30)
        cover_img_global = ctk.CTkImage(light_image=final_img, size=(340,340))
        cover_label.configure(image=cover_img_global)
    except Exception as e:
        print(f"Erreur chargement cover : {e}")

    update_library_highlight()
    add_to_history(filename)
    update_favorites_view()
    update_playlists_view()

    # Mise à jour de la playlist active si applicable
    if playlist_name is not None:
        current_playlist = playlist_name
        playlist_tracks = app_data["playlists"].get(playlist_name, [])
        try:
            current_playlist_index = playlist_tracks.index(filename)
        except ValueError:
            current_playlist_index = 0
    else:
        current_playlist = None

def update_now_playing(file):
    """Met à jour l'affichage du morceau en cours et affiche une notification."""
    now_playing_label.configure(text=f"En cours de lecture : {file}")
    notify(f"Lancement de {file}")

def update_library_highlight():
    """Met en évidence le fichier audio actuellement lu dans la bibliothèque."""
    current_file = now_playing_label.cget("text").replace("En cours de lecture : ", "")
    for file, widget in library_title_widgets.items():
        widget.configure(text_color="#1cd061" if file == current_file else "white")

def pause_audio():
    """Permet de mettre en pause ou reprendre la lecture."""
    global is_paused, last_pause_time, total_paused_duration
    if pygame.mixer.music.get_busy() or is_paused:
        if not is_paused:
            pygame.mixer.music.pause()
            is_paused = True
            last_pause_time = time.time()
            pause_btn.configure(image=play_img)
        else:
            pygame.mixer.music.unpause()
            is_paused = False
            if last_pause_time is not None:
                total_paused_duration += time.time() - last_pause_time
            last_pause_time = None
            pause_btn.configure(image=pause_img)
    else:
        messagebox.showwarning("Aucune lecture", "Aucun fichier audio en lecture.")

def next_track():
    """Passe au morceau suivant dans la playlist ou dans la bibliothèque."""
    global current_playlist, current_playlist_index
    if current_playlist:
        playlist_tracks = app_data["playlists"].get(current_playlist, [])
        if not playlist_tracks:
            return
        current_playlist_index = (current_playlist_index + 1) % len(playlist_tracks)
        next_file = playlist_tracks[current_playlist_index]
        play_audio_by_filename(next_file, current_playlist)
    else:
        audio_files = sorted([f for f in os.listdir("audio") if f.endswith(".mp3")])
        current_title = now_playing_label.cget("text").replace("En cours de lecture : ", "")
        try:
            current_index = audio_files.index(current_title)
        except ValueError:
            current_index = -1
        next_index = (current_index + 1) % len(audio_files)
        play_audio_by_filename(audio_files[next_index])

def previous_track():
    """Reprend le morceau précédent dans la playlist ou la bibliothèque."""
    global current_playlist, current_playlist_index
    if current_playlist:
        playlist_tracks = app_data["playlists"].get(current_playlist, [])
        if not playlist_tracks:
            return
        current_playlist_index = (current_playlist_index - 1) % len(playlist_tracks)
        prev_file = playlist_tracks[current_playlist_index]
        play_audio_by_filename(prev_file, current_playlist)
    else:
        audio_files = sorted([f for f in os.listdir("audio") if f.endswith(".mp3")])
        current_title = now_playing_label.cget("text").replace("En cours de lecture : ", "")
        try:
            current_index = audio_files.index(current_title)
        except ValueError:
            current_index = 0
        previous_index = (current_index - 1) % len(audio_files)
        play_audio_by_filename(audio_files[previous_index])

def toggle_loop():
    """Active ou désactive la boucle de lecture."""
    global loop_enabled
    loop_enabled = not loop_enabled
    loop_btn.configure(image=loop_img if loop_enabled else no_loop_img)

def shuffle_playlist():
    """Lit un fichier audio aléatoire parmi ceux disponibles."""
    audio_files = sorted([f for f in os.listdir("audio") if f.endswith(".mp3")])
    random.shuffle(audio_files)
    if audio_files:
        play_audio_by_filename(audio_files[0])

def set_volume(value):
    """Ajuste le volume de la lecture."""
    volume = float(value) / 100
    pygame.mixer.music.set_volume(volume)

def check_music_end():
    """Surveille la fin de la lecture pour passer au morceau suivant ou relancer en boucle."""
    for event in pygame.event.get():
        if event.type == pygame.USEREVENT:
            if loop_enabled:
                play_audio_by_filename(now_playing_label.cget("text").replace("En cours de lecture : ", ""), current_playlist)
            else:
                next_track()
    root.after(100, check_music_end)

def on_canvas_click(event):
    """Permet de chercher dans la piste en cliquant sur la barre de progression."""
    global playback_start_time, total_paused_duration, is_paused
    canvas_width = progress_canvas.winfo_width()
    new_fraction = event.x / canvas_width
    new_time = new_fraction * audio_duration
    try:
        pygame.mixer.music.play(start=new_time)
    except Exception as e:
        print("La recherche n'est peut-être pas supportée :", e)
    playback_start_time = time.time() - new_time
    total_paused_duration = 0
    is_paused = False
    pause_btn.configure(image=pause_img)

def update_canvas_progress():
    """Met à jour la barre de progression de la lecture."""
    if playback_start_time and audio_duration > 0:
        elapsed = (last_pause_time - playback_start_time - total_paused_duration) if (is_paused and last_pause_time) else (time.time() - playback_start_time - total_paused_duration)
        if elapsed < 0:
            elapsed = 0
        fraction = elapsed / audio_duration if audio_duration > 0 else 0
        fraction = min(fraction, 1)
        canvas_width = progress_canvas.winfo_width()
        progress_canvas.delete("progress")
        round_rectangle(progress_canvas, 0, 0, fraction * canvas_width, 8, radius=6, fill="#1cd061", outline="", tag="progress")
        current_time_label.configure(text=format_time(elapsed))
    root.after(1000, update_canvas_progress)

def format_time(seconds):
    """Formate un temps en secondes au format minutes:secondes."""
    minutes = int(seconds // 60)
    sec = int(seconds % 60)
    return f"{minutes}:{sec:02d}"

# --- Gestion de la bibliothèque ---

def update_library_view():
    """Actualise l'affichage de la bibliothèque audio."""
    for widget in library_scrollable_frame.winfo_children():
        widget.destroy()
    library_items.clear()
    library_title_widgets.clear()

    if not os.path.exists("audio"):
        return
    all_files = sorted([f for f in os.listdir("audio") if f.endswith(".mp3")])
    query = search_entry.get().lower()
    filtered_files = []
    for f in all_files:
        match = query in f.lower()
        tags = app_data["tags"].get(f, {})
        tag_match = any(query in str(v).lower() for v in tags.values())
        if match or tag_match or query == "":
            filtered_files.append(f)
    for file in filtered_files:
        base_name = os.path.splitext(file)[0]
        cover_path = os.path.join("Pochette_album", f"{base_name}.jpg")
        if not os.path.exists(cover_path):
            img = create_default_cover(size=(60,60))
        else:
            try:
                img = Image.open(cover_path)
            except Exception:
                img = create_default_cover(size=(60,60))
        try:
            cover_clear = ImageOps.fit(img, (60,60), method=Image.LANCZOS, centering=(0.5,0.5))
            bg_size = (70,70)
            blurred = cover_clear.resize(bg_size).filter(ImageFilter.GaussianBlur(radius=5))
            composite = blurred.copy()
            offset = ((bg_size[0]-cover_clear.width)//2, (bg_size[1]-cover_clear.height)//2)
            composite.paste(cover_clear, offset)
            final_img = add_rounded_corners(composite, radius=10)
            cover_ctk = ctk.CTkImage(light_image=final_img, size=(70,70))
        except Exception as e:
            print(f"Erreur chargement cover pour {file} : {e}")
            cover_ctk = ctk.CTkImage(light_image=create_default_cover(size=(60,60)), size=(70,70))
        
        item_frame = ctk.CTkFrame(library_scrollable_frame, fg_color="#2b2b2b", corner_radius=10)
        item_frame.pack(fill=ctk.X, padx=10, pady=5)
        img_label = ctk.CTkLabel(item_frame, image=cover_ctk, text="")
        img_label.image = cover_ctk
        img_label.pack(side=ctk.LEFT, padx=10, pady=10)
        title_frame = ctk.CTkFrame(item_frame, fg_color="#2b2b2b")
        title_frame.pack(side=ctk.LEFT, padx=10)
        title_label = ctk.CTkLabel(title_frame, text=file, font=("Arial", 12, "bold"), text_color="white")
        title_label.pack(anchor="w")
        fav_btn = ctk.CTkButton(item_frame, text="☆", width=30, fg_color="#1cd061", command=lambda f=file: toggle_favorite(f))
        fav_btn.pack(side=ctk.RIGHT, padx=10)
        # Lancement de la lecture au clic sur l'item
        item_frame.bind("<Button-1>", lambda e, filename=file: play_audio_by_filename(filename))
        img_label.bind("<Button-1>", lambda e, filename=file: play_audio_by_filename(filename))
        title_label.bind("<Button-1>", lambda e, filename=file: play_audio_by_filename(filename))
        library_items[file] = item_frame
        library_title_widgets[file] = title_label

def toggle_favorite(filename):
    """Ajoute ou retire un fichier des favoris."""
    favoris = app_data["playlists"].setdefault("Favoris", [])
    if filename in favoris:
        favoris.remove(filename)
        notify(f"{filename} retiré des favoris")
    else:
        favoris.append(filename)
        notify(f"{filename} ajouté aux favoris")
    save_app_data(app_data)
    update_library_view()
    update_favorites_view()

# --- Gestion de l'historique ---

def update_history_view():
    """Affiche l'historique des lectures."""
    for widget in history_scroll.winfo_children():
        widget.destroy()
    
    for entry in reversed(app_data["history"]):
        frame = ctk.CTkFrame(history_scroll)
        frame.pack(fill=ctk.X, pady=2)
        timestamp = datetime.fromisoformat(entry["timestamp"]).strftime("%d/%m %H:%M")
        label = ctk.CTkLabel(frame, text=f"{timestamp} - {entry['file']}")
        label.pack(side=ctk.LEFT, padx=5)
        label.bind("<Button-1>", lambda e, f=entry['file']: play_audio_by_filename(f))

def add_to_history(filename):
    """Ajoute une lecture à l'historique et limite celui-ci à 50 entrées."""
    app_data["history"].append({
        "file": filename,
        "timestamp": datetime.now().isoformat()
    })
    if len(app_data["history"]) > 50:
        app_data["history"] = app_data["history"][-50:]
    save_app_data(app_data)
    update_history_view()

# --- Gestion des favoris ---

def update_favorites_view():
    """Actualise l'affichage des favoris."""
    for widget in favorites_scroll.winfo_children():
        widget.destroy()
    fav_files = app_data["playlists"].get("Favoris", [])
    if not fav_files:
        tk.Label(favorites_scroll, text="Aucun favori.", bg="#2b2b2b", fg="white").pack(pady=10)
        return
    for file in fav_files:
        frame = ctk.CTkFrame(favorites_scroll, fg_color="#2b2b2b", corner_radius=5)
        frame.pack(fill=ctk.X, padx=10, pady=5)
        lbl = tk.Label(frame, text=file, bg="#2b2b2b", fg="white")
        lbl.pack(anchor="w", padx=10)
        lbl.bind("<Button-1>", lambda e, f=file: play_audio_by_filename(f, "Favoris"))

# --- Recherche dans la bibliothèque ---

def search_library(event=None):
    """Filtre la bibliothèque selon la requête saisie."""
    query = search_entry.get().lower()
    for file, frame in library_items.items():
        match = query in file.lower()
        tags = app_data["tags"].get(file, {})
        tag_match = any(query in str(v).lower() for v in tags.values())
        if match or tag_match or query == "":
            frame.pack(fill=ctk.X, padx=10, pady=5)
        else:
            frame.pack_forget()

# --- Paramètres d'affichage (Mode sombre/clair) ---

def toggle_mode():
    """Change le mode d'affichage et sauvegarde le paramètre."""
    if appearance_switch.get():
        ctk.set_appearance_mode("dark")
        app_data["settings"]["theme"] = "Dark"
    else:
        ctk.set_appearance_mode("light")
        app_data["settings"]["theme"] = "Light"
    save_app_data(app_data)

# --- Création de playlist ---

def create_playlist():
    """Ouvre une fenêtre modale pour créer une nouvelle playlist."""
    dialog = ctk.CTkToplevel(root)
    dialog.title("Créer une Playlist")
    dialog.geometry("300x150")
    dialog.resizable(False, False)
    dialog.grab_set()  # Rend la fenêtre modale

    label = ctk.CTkLabel(dialog, text="Nom de la nouvelle playlist :", font=("Arial", 12, "bold"))
    label.pack(pady=(20, 10), padx=20)

    entry = ctk.CTkEntry(dialog, placeholder_text="Nom de la playlist", width=200)
    entry.pack(pady=10, padx=20)

    def submit():
        name = entry.get().strip()
        if not name:
            messagebox.showerror("Erreur", "Veuillez entrer un nom valide.", parent=dialog)
            return
        if name in app_data["playlists"]:
            messagebox.showerror("Erreur", "Une playlist avec ce nom existe déjà.", parent=dialog)
            return
        app_data["playlists"][name] = []
        save_app_data(app_data)
        refresh_playlist_buttons()
        notify(f"Playlist '{name}' créée.")
        dialog.destroy()

    submit_btn = ctk.CTkButton(dialog, text="Créer", command=submit, fg_color="#1cd061", hover_color="#16a34a")
    submit_btn.pack(pady=10)
    entry.focus()

# --- Interface graphique principale ---

root = ctk.CTk()
root.title("Spotify-like Player")
root.geometry(app_data["settings"].get("window_size", "950x900"))
root.state(app_data["settings"].get("window_state", "zoomed"))

# Chargement de l'icône
icon_path = "img/icon.ico"
try:
    icon = Image.open(icon_path)
    photo = ImageTk.PhotoImage(icon)
    root.iconphoto(True, photo)
except Exception as e:
    print(f"Erreur chargement icône : {e}")

# Cadre principal et Tabview
main_frame = ctk.CTkFrame(root)
main_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

tabview = ctk.CTkTabview(main_frame)
tabview.pack(fill=ctk.BOTH, expand=True)
tabview.add("Téléchargement")
tabview.add("Bibliothèque")
tabview.add("Playlists")
tabview.add("Historique")
tabview.add("Favoris")
tabview.add("Paramètres")

# --- Onglet Téléchargement ---
download_frame = tabview.tab("Téléchargement")
ctk.CTkLabel(download_frame, text="Téléchargement YouTube", font=('Arial', 12, 'bold')).pack(anchor=ctk.W, pady=(10,0))
url_frame = ctk.CTkFrame(download_frame)
url_frame.pack(fill=ctk.X, pady=5, padx=10)
youtube_url_entry = ctk.CTkEntry(url_frame, width=60)
youtube_url_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True)
paste_url_button = ctk.CTkButton(url_frame, text="Coller", command=paste_url, fg_color="#1cd061")
paste_url_button.pack(side=ctk.LEFT, padx=5)
download_youtube_button = ctk.CTkButton(download_frame, text="Télécharger", command=download_youtube, fg_color="#1cd061")
download_youtube_button.pack(pady=10)
progress_bar = ctk.CTkProgressBar(download_frame, orientation="horizontal", mode="determinate")
download_percentage_label = ctk.CTkLabel(download_frame, text="0%", font=("Arial", 10))
status_label = ctk.CTkLabel(download_frame, text="Prêt", text_color="green")
status_label.pack(pady=5)

# --- Onglet Bibliothèque ---
library_frame = tabview.tab("Bibliothèque")
search_frame = ctk.CTkFrame(library_frame)
search_frame.pack(fill=ctk.X, padx=10, pady=5)
search_entry = ctk.CTkEntry(search_frame, placeholder_text="Rechercher...")
search_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True)
search_entry.bind("<KeyRelease>", search_library)
library_scrollable_frame = ctk.CTkScrollableFrame(library_frame, width=800, height=300)
library_scrollable_frame.pack(fill=ctk.BOTH, expand=True, padx=10, pady=10)
update_library_view()

# --- Onglet Playlists ---
playlists_frame = tabview.tab("Playlists")
playlist_left_frame = ctk.CTkFrame(playlists_frame, width=250, fg_color="#1a1a1a", corner_radius=10)
playlist_left_frame.pack(side=ctk.LEFT, fill=ctk.Y, padx=10, pady=(10,5))
playlist_right_frame = ctk.CTkFrame(playlists_frame, fg_color="#1a1a1a", corner_radius=10)
playlist_right_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=10, pady=(10,5))

# Bouton de création de playlist
btn_create_playlist = ctk.CTkButton(playlist_left_frame, text="Créer Playlist", command=lambda: create_playlist(), fg_color="#1cd061")
btn_create_playlist.pack(padx=10, pady=(10,5), fill=ctk.X)

# Zone de scroll pour les playlists
playlist_buttons_frame = ctk.CTkScrollableFrame(playlist_left_frame, height=300, fg_color="#1a1a1a")
playlist_buttons_frame.pack(padx=10, pady=5, fill=ctk.BOTH, expand=True)

def refresh_playlist_buttons():
    """Rafraîchit l'affichage des boutons de playlists."""
    for widget in playlist_buttons_frame.winfo_children():
        widget.destroy()

# --- Gestion des boutons de playlists ---

def refresh_playlist_buttons():
    """Crée un bouton pour chaque playlist existante."""
    for widget in playlist_buttons_frame.winfo_children():
        widget.destroy()
    for name in app_data["playlists"].keys():
        btn = ctk.CTkButton(
            playlist_buttons_frame,
            text=name,
            fg_color="#333333",
            hover_color="#444444",
            text_color="white",
            command=lambda n=name: show_playlist_contents_modern(n)
        )
        btn.pack(padx=5, pady=5, fill=ctk.X)

def update_playlists_view():
    """Mets à jour l'affichage des playlists (pour compatibilité)."""
    refresh_playlist_buttons()

# --- Affichage du contenu d'une playlist ---

playlist_contents_frame = ctk.CTkFrame(playlist_right_frame, fg_color="#1a1a1a", corner_radius=10)
playlist_contents_frame.pack(padx=10, pady=10, fill=ctk.BOTH, expand=True)

def show_playlist_contents_modern(playlist_name):
    """
    Affiche le contenu de la playlist sélectionnée dans le cadre dédié.
    
    :param playlist_name: Nom de la playlist à afficher.
    """
    global current_playlist_selected
    current_playlist_selected = playlist_name
    # Réinitialisation du cadre de contenu
    for widget in playlist_contents_frame.winfo_children():
        widget.destroy()
    # En-tête avec le titre de la playlist et le bouton pour lancer la lecture
    header_frame = ctk.CTkFrame(playlist_contents_frame, fg_color="#1a1a1a")
    header_frame.pack(fill=ctk.X, pady=10)
    lbl_title = ctk.CTkLabel(
        header_frame,
        text=playlist_name,
        font=("Arial", 16, "bold"),
        text_color="white",
        fg_color="#1a1a1a"
    )
    lbl_title.pack(side=ctk.LEFT, padx=10)
    btn_play = ctk.CTkButton(
        header_frame,
        text="Lire la playlist",
        fg_color="#1cd061",
        command=lambda: play_playlist(playlist_name)
    )
    btn_play.pack(side=ctk.RIGHT, padx=10)
    # Affichage des morceaux
    songs = app_data["playlists"].get(playlist_name, [])
    if not songs:
        lbl_info = ctk.CTkLabel(
            playlist_contents_frame,
            text="Playlist vide.",
            text_color="white",
            fg_color="#1a1a1a"
        )
        lbl_info.pack(pady=20)
    else:
        for file in songs:
            frame = ctk.CTkFrame(playlist_contents_frame, fg_color="#2b2b2b", corner_radius=5)
            frame.pack(fill=ctk.X, padx=5, pady=5)
            lbl = ctk.CTkLabel(frame, text=file, text_color="white", fg_color="#2b2b2b")
            lbl.pack(side=ctk.LEFT, padx=10, pady=5)
            btn_remove = ctk.CTkButton(
                frame,
                text="Supprimer",
                width=80,
                fg_color="#cc3333",
                text_color="white",
                command=lambda f=file, pl=playlist_name: remove_from_playlist(pl, f)
            )
            btn_remove.pack(side=ctk.RIGHT, padx=10, pady=5)
            # Lancer le morceau en cliquant sur l'item
            frame.bind("<Button-1>", lambda e, f=file, pl=playlist_name: play_audio_by_filename(f, pl))
            lbl.bind("<Button-1>", lambda e, f=file, pl=playlist_name: play_audio_by_filename(f, pl))

def remove_from_playlist(playlist_name, file):
    """
    Retire un morceau de la playlist et met à jour l'affichage.
    
    :param playlist_name: Nom de la playlist.
    :param file: Nom du fichier à retirer.
    """
    if file in app_data["playlists"].get(playlist_name, []):
        app_data["playlists"][playlist_name].remove(file)
        save_app_data(app_data)
        refresh_playlist_buttons()
        show_playlist_contents_modern(playlist_name)
        notify(f"{file} retiré de {playlist_name}")

def play_playlist(playlist_name):
    """
    Lance la lecture de la playlist en jouant le premier morceau.
    
    :param playlist_name: Nom de la playlist à jouer.
    """
    songs = app_data["playlists"].get(playlist_name, [])
    if songs:
        play_audio_by_filename(songs[0], playlist_name)
    else:
        messagebox.showwarning("Playlist vide", "Cette playlist est vide.")

def add_current_to_playlist_modern():
    """
    Ajoute le morceau actuellement lu à la playlist sélectionnée.
    """
    global current_playlist_selected
    if not current_playlist_selected:
        messagebox.showwarning("Aucune sélection", "Sélectionnez une playlist.")
        return
    current_file = now_playing_label.cget("text").replace("En cours de lecture : ", "")
    if current_file:
        app_data["playlists"].setdefault(current_playlist_selected, [])
        if current_file not in app_data["playlists"][current_playlist_selected]:
            app_data["playlists"][current_playlist_selected].append(current_file)
            save_app_data(app_data)
            show_playlist_contents_modern(current_playlist_selected)
            notify(f"{current_file} ajouté à {current_playlist_selected}")

btn_add_current_modern = ctk.CTkButton(
    playlist_left_frame,
    text="Ajouter morceau en cours",
    command=add_current_to_playlist_modern,
    fg_color="#1cd061"
)
btn_add_current_modern.pack(padx=10, pady=(5,10), fill=ctk.X)

# Rafraîchissement initial des boutons de playlists
refresh_playlist_buttons()

# --- Onglet Historique ---
history_frame = tabview.tab("Historique")
history_scroll = ctk.CTkScrollableFrame(history_frame)
history_scroll.pack(fill=ctk.BOTH, expand=True)

# --- Onglet Favoris ---
favorites_frame = tabview.tab("Favoris")
favorites_scroll = ctk.CTkScrollableFrame(favorites_frame)
favorites_scroll.pack(fill=ctk.BOTH, expand=True)
update_favorites_view()

# --- Onglet Paramètres ---
settings_frame = tabview.tab("Paramètres")
ctk.CTkLabel(settings_frame, text="Paramètres", font=("Arial", 14, "bold")).pack(pady=10)
appearance_switch = ctk.CTkSwitch(settings_frame, text="Mode Sombre", command=toggle_mode)
appearance_switch.pack(pady=5)

# --- Cadre du Player ---
player_frame = ctk.CTkFrame(root)
player_frame.pack(side=ctk.BOTTOM, fill=ctk.X, padx=20, pady=10)

cover_frame = ctk.CTkFrame(player_frame, width=340, height=340, corner_radius=30)
cover_frame.pack(pady=10)
cover_label = ctk.CTkLabel(cover_frame, text="", width=340, height=340)
cover_label.pack()

audio_progress_frame = ctk.CTkFrame(player_frame)
audio_progress_frame.pack(fill=ctk.X, pady=5)
current_time_label = ctk.CTkLabel(audio_progress_frame, text="0:00")
current_time_label.pack(side=ctk.LEFT, padx=(5, 10))
progress_canvas = tk.Canvas(audio_progress_frame, width=400, height=8, bg='#1C1C1C', highlightthickness=0)
progress_canvas.pack(side=ctk.LEFT, fill=ctk.X, expand=True)
round_rectangle(progress_canvas, 0, 0, 0, 8, radius=6, fill="#1cd061", outline="", tag="progress")
progress_canvas.bind("<Button-1>", on_canvas_click)
total_time_label = ctk.CTkLabel(audio_progress_frame, text="0:00")
total_time_label.pack(side=ctk.LEFT, padx=(10, 5))

control_frame = ctk.CTkFrame(player_frame)
control_frame.pack(pady=10)
icon_size = 30
play_img = ctk.CTkImage(Image.open("img/play.png").resize((icon_size, icon_size)))
pause_img = ctk.CTkImage(Image.open("img/pause.png").resize((icon_size, icon_size)))
next_img = ctk.CTkImage(Image.open("img/next.png").resize((icon_size, icon_size)))
prev_img = ctk.CTkImage(Image.open("img/previous.png").resize((icon_size, icon_size)))
loop_img = ctk.CTkImage(Image.open("img/loop.png").resize((icon_size, icon_size)))
no_loop_img = ctk.CTkImage(Image.open("img/no_loop.png").resize((icon_size, icon_size)))
shuffle_img = ctk.CTkImage(Image.open("img/shuffle.png").resize((icon_size, icon_size)))

prev_btn = ctk.CTkButton(control_frame, image=prev_img, text="", command=previous_track, fg_color="#1cd061")
prev_btn.grid(row=0, column=0, padx=5)
pause_btn = ctk.CTkButton(control_frame, image=pause_img, text="", command=pause_audio, fg_color="#1cd061")
pause_btn.grid(row=0, column=1, padx=5)
next_btn = ctk.CTkButton(control_frame, image=next_img, text="", command=next_track, fg_color="#1cd061")
next_btn.grid(row=0, column=2, padx=5)
loop_btn = ctk.CTkButton(control_frame, image=loop_img, text="", command=toggle_loop, fg_color="#1cd061")
loop_btn.grid(row=0, column=3, padx=5)
shuffle_btn = ctk.CTkButton(control_frame, image=shuffle_img, text="", command=shuffle_playlist, fg_color="#1cd061")
shuffle_btn.grid(row=0, column=4, padx=5)

volume_frame = ctk.CTkFrame(control_frame)
volume_frame.grid(row=0, column=5, padx=20)
volume_scale = ctk.CTkSlider(volume_frame, from_=0, to=100, command=set_volume, width=100)
volume_scale.set(50)
volume_scale.pack(side=ctk.RIGHT)

now_playing_label = ctk.CTkLabel(player_frame, text="Aucune lecture en cours", text_color="gray")
now_playing_label.pack(pady=5)

# --- Raccourcis clavier ---
root.bind("<space>", lambda e: pause_audio())
root.bind("<Right>", lambda e: next_track())
root.bind("<Left>", lambda e: previous_track())

# Actualisations et boucle principale
update_library_view()
update_playlists_view()
check_music_end()
update_canvas_progress()
root.mainloop()
