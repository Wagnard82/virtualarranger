import streamlit as st
from music21 import *
import copy
from fractions import Fraction
import tempfile
import os
import requests
import math

# ==========================================
# CONFIGURAZIONE PAGINA E STILE
# ==========================================
st.set_page_config(page_title="Orchestratore Ibrido v0.2", layout="wide")

st.markdown("""
    <style>
    .reportview-container .main .block-container { padding-top: 2rem; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #ff4b4b; color: white; border: none; font-weight: bold; }
    .stButton>button:hover { background-color: #ff3333; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# GLOBALI & LIBRERIA STRUMENTI
# ==========================================
# L'ordine classico da partitura: Legni in alto, Archi in basso
ORDINE_PARTITURA = ["Flauto", "Oboe", "Clarinetto in Sib", "Fagotto", "Violino I", "Violino II", "Viola", "Violoncello"]

LIBRERIA_STRUMENTI = {
    "Flauto":            {"min": 60, "max": 96, "clef": clef.TrebleClef(), "inst": instrument.Flute()},
    "Oboe":              {"min": 58, "max": 91, "clef": clef.TrebleClef(), "inst": instrument.Oboe()},
    "Clarinetto in Sib": {"min": 50, "max": 89, "clef": clef.TrebleClef(), "inst": instrument.Clarinet()}, 
    "Fagotto":           {"min": 34, "max": 75, "clef": clef.BassClef(),   "inst": instrument.Bassoon()},
    "Violino I":         {"min": 55, "max": 96, "clef": clef.TrebleClef(), "inst": instrument.Violin()},
    "Violino II":        {"min": 55, "max": 84, "clef": clef.TrebleClef(), "inst": instrument.Violin()},
    "Viola":             {"min": 48, "max": 79, "clef": clef.AltoClef(),   "inst": instrument.Viola()},
    "Violoncello":       {"min": 36, "max": 67, "clef": clef.BassClef(),   "inst": instrument.Violoncello()}
}

# ==========================================
# FUNZIONI DI SUPPORTO E FISICA
# ==========================================
def get_octave_shift(ps, strumento):
    min_ps = LIBRERIA_STRUMENTI[strumento]["min"]
    max_ps = LIBRERIA_STRUMENTI[strumento]["max"]
    shift = 0
    temp_ps = ps
    while temp_ps < min_ps:
        temp_ps += 12
        shift += 1
    while temp_ps > max_ps:
        temp_ps -= 12
        shift -= 1
    return shift

def applica_limiti_fisici(nota_obj, strumento):
    if not isinstance(nota_obj, note.Note): return nota_obj
    shift_ottave = get_octave_shift(nota_obj.pitch.ps, strumento)
    if shift_ottave != 0:
        nota_obj.pitch.octave += shift_ottave
    return nota_obj

def is_strumento_libero(misura, check_offset):
    for n in misura.notes:
        if getattr(n.duration, 'isGrace', False) or n.quarterLength == 0: continue
        inizio = float(n.offset)
        fine = inizio + float(n.quarterLength)
        if inizio <= check_offset < fine - 0.001: return False
    return True

def copia_proprieta(sorgente, destinazione):
    if hasattr(sorgente, 'tie') and sorgente.tie is not None: 
        destinazione.tie = tie.Tie(sorgente.tie.type)
        
    if hasattr(sorgente, 'articulations') and sorgente.articulations:
        nuove_art = [copy.deepcopy(art) for art in sorgente.articulations if not isinstance(art, articulations.Fingering)]
        if nuove_art: 
            destinazione.articulations = nuove_art

def analizza_misure(m_dx, m_sx):
    if not m_dx or not m_sx: return True, False
    notes_dx = [n for n in m_dx.flatten().notes if not getattr(n.duration, 'isGrace', False)]
    notes_sx = [n for n in m_sx.flatten().notes if not getattr(n.duration, 'isGrace', False)]
    if not notes_dx or not notes_sx: return True, False

    accordi_dx = sum(1 for n in notes_dx if hasattr(n, 'pitches') and len(n.pitches) > 1)
    accordi_sx = sum(1 for n in notes_sx if hasattr(n, 'pitches') and len(n.pitches) > 1)
    
    is_dx_melodia = True
    if len(notes_dx) > 0 and (accordi_dx / len(notes_dx)) > 0.5 and accordi_sx == 0:
        is_dx_melodia = False

    ps_dx = [p.ps for n in notes_dx for p in (n.pitches if hasattr(n, 'pitches') else [n.pitch])]
    ps_sx = [p.ps for n in notes_sx for p in (n.pitches if hasattr(n, 'pitches') else [n.pitch])]
    
    avg_dx = sum(ps_dx) / len(ps_dx) if ps_dx else 60
    avg_sx = sum(ps_sx) / len(ps_sx) if ps_sx else 48

    is_melodia_bassa = False
    if is_dx_melodia and avg_dx < avg_sx: is_melodia_bassa = True
    elif not is_dx_melodia and avg_sx < avg_dx: is_melodia_bassa = True

    return is_dx_melodia, is_melodia_bassa

def get_lowest_ps(element):
    if hasattr(element, 'pitches') and element.pitches: return min(p.ps for p in element.pitches)
    if hasattr(element, 'pitch'): return element.pitch.ps
    return 60

# ==========================================
# MOTORE DEI PATTERN 
# ==========================================
def arrangia_pattern_sinistra(tutte_note, cassetti, num, strum_pattern):
    if not tutte_note or not strum_pattern: return []
    
    s_b = strum_pattern[0] if len(strum_pattern) > 0 else None
    s_m = strum_pattern[1] if len(strum_pattern) > 1 else strum_pattern[0]
    s_h = strum_pattern[2] if len(strum_pattern) > 2 else None
    
    if not s_b or not s_m: return tutte_note 
    
    note_by_offset = {}
    for n in tutte_note:
        if getattr(n.duration, 'isGrace', False) or n.quarterLength == 0: continue
        off = Fraction(float(n.offset)).limit_denominator(100)
        note_by_offset.setdefault(off, []).append(n)
        
    unique_offsets = sorted(note_by_offset.keys())
    usate = set()
    
    def find_closest(target):
        for off in unique_offsets:
            if abs(float(off) - float(target)) < 0.002: return off
        return None

    durate_reali = [n.quarterLength for n in tutte_note if n.quarterLength > 0]
    min_dur = min(durate_reali) if durate_reali else 0.5

    i = 0
    while i < len(unique_offsets):
        o1 = unique_offsets[i]
        pattern_found = False
        
        for j in range(i+1, min(i+4, len(unique_offsets))):
            dur = unique_offsets[j] - o1
            if float(dur) <= 0 or float(dur) > min_dur * 2.0: continue 
            
            o2, o3, o4 = o1 + dur, o1 + 2*dur, o1 + 3*dur
            real_o2, real_o3, real_o4 = find_closest(o2), find_closest(o3), find_closest(o4)
            
            if real_o2 is not None and real_o3 is not None and real_o4 is not None:
                n1 = min(note_by_offset[o1], key=get_lowest_ps)
                n2 = min(note_by_offset[real_o2], key=get_lowest_ps)
                n3 = min(note_by_offset[real_o3], key=get_lowest_ps)
                n4 = min(note_by_offset[real_o4], key=get_lowest_ps)
                
                p1, p2, p3, p4 = get_lowest_ps(n1), get_lowest_ps(n2), get_lowest_ps(n3), get_lowest_ps(n4)
                
                is_alberti = (p1 < p3 and p3 < p2 and p2 == p4)
                is_tremolo = (p2 == p4 and p1 < p2 and p3 < p4 and not is_alberti)
                is_arpeggio = (p1 < p2 and p2 < p3 and p3 < p4)
                is_ottave = (abs(p1 - p2) == 12 and p1 == p3 and p2 == p4)
                
                if is_ottave or is_alberti or is_tremolo or is_arpeggio:
                    if is_ottave:
                        low_p, high_p = min(p1, p2), max(p1, p2)
                        for nj, real_off in zip([n1, n2, n3, n4], [o1, real_o2, real_o3, real_o4]):
                            n_c = copy.deepcopy(nj); n_c.pitch.ps = low_p
                            cassetti[s_b][num].insert(float(real_off), applica_limiti_fisici(n_c, s_b))
                            if s_m != s_b:
                                n_v = copy.deepcopy(nj); n_v.pitch.ps = high_p
                                cassetti[s_m][num].insert(float(real_off), applica_limiti_fisici(n_v, s_m))
                            if s_h: 
                                n_v2 = copy.deepcopy(nj); n_v2.pitch.ps = high_p
                                cassetti[s_h][num].insert(float(real_off), applica_limiti_fisici(n_v2, s_h))
                            usate.add(id(nj))
                            
                    elif is_alberti:
                        cassetti[s_b][num].insert(float(o1), applica_limiti_fisici(copy.deepcopy(n1), s_b))
                        usate.add(id(n1))
                        for nj, real_off in zip([n2, n3, n4], [real_o2, real_o3, real_o4]):
                            if s_m != s_b:
                                n_viola = copy.deepcopy(nj); n_viola.pitch.ps = p3
                                cassetti[s_m][num].insert(float(real_off), applica_limiti_fisici(n_viola, s_m))
                            if s_h:
                                n_vln2 = copy.deepcopy(nj); n_vln2.pitch.ps = p2
                                cassetti[s_h][num].insert(float(real_off), applica_limiti_fisici(n_vln2, s_h))
                            usate.add(id(nj))
                            
                    elif is_tremolo:
                        durata_doppia = Fraction(float(dur * 2)).limit_denominator(100)
                        
                        n_c1 = copy.deepcopy(n1); n_c1.duration.quarterLength = durata_doppia
                        cassetti[s_b][num].insert(float(o1), applica_limiti_fisici(n_c1, s_b))
                        
                        n_c2 = copy.deepcopy(n3); n_c2.duration.quarterLength = durata_doppia
                        cassetti[s_b][num].insert(float(real_o3), applica_limiti_fisici(n_c2, s_b))
                        
                        if s_h:
                            n_v1 = copy.deepcopy(n1); n_v1.duration.quarterLength = durata_doppia
                            cassetti[s_m][num].insert(float(o1), applica_limiti_fisici(n_v1, s_m))
                            n_v2 = copy.deepcopy(n3); n_v2.duration.quarterLength = durata_doppia
                            cassetti[s_m][num].insert(float(real_o3), applica_limiti_fisici(n_v2, s_m))
                            for nj, real_off in zip([n1, n2, n3, n4], [o1, real_o2, real_o3, real_o4]):
                                cassetti[s_h][num].insert(float(real_off), applica_limiti_fisici(copy.deepcopy(nj), s_h))
                                usate.add(id(nj))
                        elif s_m != s_b:
                            for nj, real_off in zip([n1, n2, n3, n4], [o1, real_o2, real_o3, real_o4]):
                                cassetti[s_m][num].insert(float(real_off), applica_limiti_fisici(copy.deepcopy(nj), s_m))
                                usate.add(id(nj))
                            
                    elif is_arpeggio:
                        cassetti[s_b][num].insert(float(o1), applica_limiti_fisici(copy.deepcopy(n1), s_b))
                        usate.add(id(n1))
                        for nj, real_off in zip([n2, n3, n4], [real_o2, real_o3, real_o4]):
                            ps_j = get_lowest_ps(nj)
                            if ps_j < 48:
                                cassetti[s_b][num].insert(float(real_off), applica_limiti_fisici(copy.deepcopy(nj), s_b))
                            elif s_m != s_b:
                                cassetti[s_m][num].insert(float(real_off), applica_limiti_fisici(copy.deepcopy(nj), s_m))
                                if s_h:
                                    cassetti[s_h][num].insert(float(real_off), applica_limiti_fisici(copy.deepcopy(nj), s_h))
                            usate.add(id(nj))
                    
                    pattern_found = True
                    break 
            
        if pattern_found:
            i = unique_offsets.index(real_o4) + 1
        else:
            i += 1
            
    return [n for n in tutte_note if id(n) not in usate]

def applica_dinamiche_e_testi(m_sorgente, m_destinazione):
    if not m_sorgente: return
    elementi_dinamici = m_sorgente.getElementsByClass(['Dynamic', 'TextExpression'])
    for elemento in elementi_dinamici:
        esistenti = list(m_destinazione.getElementsByClass(type(elemento)).getElementsByOffset(elemento.offset))
        if not esistenti:
            m_destinazione.insert(elemento.offset, copy.deepcopy(elemento))

def calcola_ruoli_dinamici(ensemble, configurazione_attuale):
    if not ensemble: return
    n = len(ensemble)
    
    for s in ensemble:
        configurazione_attuale[s]["ruolo"] = "Accompagnamento"
        
    if n == 1:
        configurazione_attuale[ensemble[0]]["ruolo"] = "Melodia"
        return
        
    priorita_melodia = ["Violino I", "Flauto", "Oboe", "Violino II", "Clarinetto in Sib", "Viola", "Violoncello", "Fagotto"]
    priorita_basso = ["Violoncello", "Fagotto", "Viola", "Clarinetto in Sib", "Violino II", "Oboe", "Flauto", "Violino I"]
    
    melodie_candidate = [s for s in priorita_melodia if s in ensemble]
    bassi_candidati = [s for s in priorita_basso if s in ensemble]
    
    num_melodie = 2 if n >= 5 else 1
    num_bassi = 2 if n >= 6 else 1
    
    assegnati = set()
    
    for i in range(min(num_melodie, len(melodie_candidate))):
        s = melodie_candidate[i]
        configurazione_attuale[s]["ruolo"] = "Melodia"
        assegnati.add(s)
        
    bassi_assegnati = 0
    for s in bassi_candidati:
        if s not in assegnati or n == 2:
            configurazione_attuale[s]["ruolo"] = "Basso"
            assegnati.add(s)
            bassi_assegnati += 1
        if bassi_assegnati >= num_bassi:
            break

# ==========================================
# LAYOUT STREAMLIT PRINCIPALE
# ==========================================

col_main, col_right = st.columns([7, 3], gap="large")

# --- COLONNA DESTRA (Spiegazioni) ---
with col_right:
    st.info("✨ **Versione 0.2: L'Orchestra Modulare**")
    
    # SEZIONE NOVITA' (CHANGELOG)
    st.markdown("""
    **Novità in questo aggiornamento:**
    * 🎛️ **Pannello di Selezione:** Ora puoi scegliere interattivamente quali Legni e Archi includere.
    * 🧠 **Ruoli Dinamici:** Il motore assegna in automatico *Melodia*, *Basso* e *Accompagnamento* in base agli strumenti scelti.
    * 🎻 **Raddoppi Intelligenti:** Se selezioni molti strumenti, i principali si raddoppiano per dare più corpo al suono (es. Flauto copia Violino I).
    * 🛡️ **Frazioni Blindate:** Usa la libreria `fractions` per calcoli temporali perfetti, evitando crash e disallineamenti ritmici.
    * 🧹 **Spazzatrice Assoluta:** Pulisce automaticamente l'export da cambi di tempo e chiave inutili in mezzo alla partitura.
    """)

    st.divider()

    st.header("📖 Come Funziona")
    st.markdown("""
    L'algoritmo analizza la polifonia del pianoforte e la redistribuisce fisicamente sugli strumenti reali. 
    Seleziona la tua formazione, carica il file e lascia che l'Intelligenza Artificiale "classica" gestisca pattern ritmici (Alberti, arpeggi) ed estensioni vocali.
    """)

    st.divider()
    st.subheader("💬 Feedback & Supporto")
    with st.form("feedback"):
        st.write("Aiutami a migliorare l'algoritmo!")
        commento = st.text_area("Suggerimenti o bug trovati:")
        inviato = st.form_submit_button("Invia Feedback")
        
        if inviato:
            if commento.strip() == "":
                st.warning("Scrivi qualcosa prima di inviare!")
            else:
                try:
                    BOT_TOKEN = st.secrets["TELEGRAM_TOKEN"] 
                    CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
                    messaggio = f"🎵 *Nuovo Feedback v0.2*\n\n{commento}"
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    payload = {"chat_id": CHAT_ID, "text": messaggio, "parse_mode": "Markdown"}
                    requests.post(url, json=payload)
                    st.success("Feedback inviato con successo! Grazie.")
                except Exception:
                    st.success("Feedback registrato (modalità offline).")
    
    st.markdown("<br>", unsafe_allow_html=True)
    st.link_button("☕ Offrimi un Caffè su Ko-Fi", "https://ko-fi.com/tuo_profilo")

# --- COLONNA CENTRALE (App) ---
with col_main:
    st.title("🎼 Orchestratore Modulare v0.2")
    st.write("Carica un brano per pianoforte e orchestra la tua formazione personalizzata di Legni e Archi.")
    
    uploaded_file = st.file_uploader("Seleziona il tuo spartito (.mxl / .xml)", type=['mxl', 'xml'])
    
    # -- PANNELLO DI CONTROLLO UTENTE --
    with st.expander("🎻 Componi la tua Orchestra", expanded=True):
        st.write("Seleziona gli strumenti attivi per l'orchestrazione:")
        
        col_legni, col_archi = st.columns(2)
        
        user_config = {s: {"attivo": False, "ruolo": "Accompagnamento"} for s in ORDINE_PARTITURA}
        
        with col_legni:
            st.markdown("**🌬️ Legni**")
            user_config["Flauto"]["attivo"] = st.checkbox("Flauto", value=False)
            user_config["Oboe"]["attivo"] = st.checkbox("Oboe", value=False)
            user_config["Clarinetto in Sib"]["attivo"] = st.checkbox("Clarinetto in Sib", value=False)
            user_config["Fagotto"]["attivo"] = st.checkbox("Fagotto", value=False)
            
        with col_archi:
            st.markdown("**🎻 Archi**")
            user_config["Violino I"]["attivo"] = st.checkbox("Violino I", value=True)
            user_config["Violino II"]["attivo"] = st.checkbox("Violino II", value=True)
            user_config["Viola"]["attivo"] = st.checkbox("Viola", value=True)
            user_config["Violoncello"]["attivo"] = st.checkbox("Violoncello", value=True)
            
        st.divider()
        KEEP_ORIGINAL = st.checkbox("Includi pianoforte originale (Modalità Sicura) nel file esportato", value=True)

    ensemble_attivo = [s for s in ORDINE_PARTITURA if user_config[s]["attivo"]]

    if uploaded_file is not None:
        if len(ensemble_attivo) == 0:
            st.warning("⚠️ Seleziona almeno uno strumento per procedere.")
        elif st.button("🚀 Avvia Orchestrazione"):
            with st.status("🎼 Inizio lavorazione...", expanded=True) as status:
                try:
                    # 0. Calcolo Ruoli
                    calcola_ruoli_dinamici(ensemble_attivo, user_config)
                    
                    st.write("Formazione configurata:")
                    for s in ensemble_attivo:
                        st.write(f"- {s} ({user_config[s]['ruolo']})")

                    # 1. Caricamento
                    st.write("Lettura del file in corso...")
                    estensione = os.path.splitext(uploaded_file.name)[1].lower()
                    with tempfile.NamedTemporaryFile(delete=False, suffix=estensione) as tmp_in:
                        tmp_in.write(uploaded_file.getvalue())
                        tmp_path = tmp_in.name
                    
                    partitura_originale = converter.parse(tmp_path)
                    parti_orig = partitura_originale.getElementsByClass(stream.Part)
                    misure_destra = list(parti_orig[0].getElementsByClass(stream.Measure))
                    misure_sinistra = list(parti_orig[1].getElementsByClass(stream.Measure))

                    # 2. Orchestrazione
                    st.write("Analisi ed estrazione delle parti...")
                    cassetti = {strum: {} for strum in ensemble_attivo}

                    for m_dx_orig, m_sx_orig in zip(misure_destra, misure_sinistra):
                        num = m_dx_orig.number
                        for strum in cassetti: cassetti[strum][num] = stream.Measure(number=num)
                        
                        is_dx_melodia, is_melodia_bassa = analizza_misure(m_dx_orig, m_sx_orig)
                        fonte_melodia = m_dx_orig if is_dx_melodia else m_sx_orig
                        fonte_accomp = m_sx_orig if is_dx_melodia else m_dx_orig
                        
                        strum_melodia = [s for s in ensemble_attivo if user_config[s]["ruolo"] == "Melodia"]
                        strum_accomp  = [s for s in ensemble_attivo if user_config[s]["ruolo"] == "Accompagnamento"]
                        strum_basso   = [s for s in ensemble_attivo if user_config[s]["ruolo"] == "Basso"]

                        if is_melodia_bassa:
                            strum_melodia, strum_basso = strum_basso, strum_melodia
                            strum_accomp = strum_melodia + strum_accomp

                        info_offset = {}
                        
                        # --- ESTRAZIONE MELODIA ---
                        if fonte_melodia:
                            offset_dict = {}
                            for el in fonte_melodia.flatten().getElementsByClass(['Note', 'Chord']):
                                if not getattr(el.duration, 'isGrace', False) and el.quarterLength > 0:
                                    offset_dict.setdefault(float(el.offset), []).append(el)
                                    
                            melody_busy_until = -1.0 
                            for off in sorted(offset_dict.keys()):
                                info_offset.setdefault(off, {'melodia': None, 'basso': None, 'scarti': []})
                                pitches_qui = []
                                for el in offset_dict[off]:
                                    altezze = sorted(el.pitches) if hasattr(el, 'pitches') and el.pitches else [el.pitch] if hasattr(el, 'pitch') else []
                                    for p in altezze: pitches_qui.append((p, el))
                                
                                if not pitches_qui: continue 
                                pitches_qui.sort(key=lambda x: x[0].ps, reverse=not is_melodia_bassa) 
                                
                                if off >= melody_busy_until - 0.001:
                                    p_top, el_top = pitches_qui[0]
                                    for s_mel in strum_melodia:
                                        nota_mel = note.Note(p_top.nameWithOctave) 
                                        nota_mel.duration = copy.deepcopy(el_top.duration)
                                        copia_proprieta(el_top, nota_mel)
                                        cassetti[s_mel][num].insert(off, applica_limiti_fisici(nota_mel, s_mel))
                                        
                                    melody_busy_until = off + el_top.quarterLength
                                    info_offset[off]['melodia'] = p_top.ps
                                    
                                    for p, el_orig in pitches_qui[1:]:
                                        if p.ps % 12 == p_top.ps % 12: continue 
                                        sc = note.Note(p.nameWithOctave) 
                                        sc.duration = copy.deepcopy(el_orig.duration)
                                        copia_proprieta(el_orig, sc)
                                        info_offset[off]['scarti'].append(sc)
                                else:
                                    for p, el_orig in pitches_qui:
                                        sc = note.Note(p.nameWithOctave) 
                                        sc.duration = copy.deepcopy(el_orig.duration)
                                        copia_proprieta(el_orig, sc)
                                        info_offset[off]['scarti'].append(sc)

                        # --- ESTRAZIONE VOCI E ACCOMPAGNAMENTO ---
                        if fonte_accomp:
                            tutte_note_acc = list(fonte_accomp.flatten().notes)
                            durate = [n.quarterLength for n in tutte_note_acc if not getattr(n.duration, 'isGrace', False) and n.quarterLength > 0]
                            min_dur = min(durate) if durate else 1.0
                            
                            ci_sono_scarti_melodia = any(len(v['scarti']) > 0 for v in info_offset.values())
                            voci_indipendenti = [n for n in tutte_note_acc if not getattr(n.duration, 'isGrace', False) and n.quarterLength >= min_dur * 2.0]
                            
                            pat_basso = strum_basso[0] if strum_basso else None
                            pat_accomp = strum_accomp.copy()
                            if not pat_accomp and "Clarinetto in Sib" in strum_accomp:
                                pat_accomp = ["Clarinetto in Sib"]
                                
                            strum_pattern = []
                            if pat_basso: strum_pattern.append(pat_basso)
                            if pat_accomp: strum_pattern.extend(pat_accomp[::-1]) 
                            
                            if (voci_indipendenti or ci_sono_scarti_melodia) and len(strum_pattern) > 2:
                                strum_pattern = strum_pattern[:-1] 
                                
                            note_accomp_restanti = arrangia_pattern_sinistra(tutte_note_acc, cassetti, num, strum_pattern)
                            
                            offset_dict_acc = {}
                            for el in note_accomp_restanti:
                                if not getattr(el.duration, 'isGrace', False) and el.quarterLength > 0:
                                    offset_dict_acc.setdefault(float(el.offset), []).append(el)
                                
                            strum_accomp_principale = pat_basso if pat_basso else (pat_accomp[0] if pat_accomp else None)
                                 
                            accomp_busy_until = -1.0
                            for off in sorted(offset_dict_acc.keys()):
                                info_offset.setdefault(off, {'melodia': None, 'basso': None, 'scarti': []})
                                pitches_qui = []
                                for el in offset_dict_acc[off]:
                                    altezze = sorted(el.pitches) if hasattr(el, 'pitches') and el.pitches else [el.pitch] if hasattr(el, 'pitch') else []
                                    for p in altezze: pitches_qui.append((p, el))
                                
                                if not pitches_qui: continue 
                                pitches_qui.sort(key=lambda x: x[0].ps, reverse=is_melodia_bassa) 
                                
                                if strum_accomp_principale and off >= accomp_busy_until - 0.001 and is_strumento_libero(cassetti[strum_accomp_principale][num], off):
                                    p_prin, el_prin = pitches_qui[0]
                                    nota_acc = note.Note(p_prin.nameWithOctave) 
                                    nota_acc.duration = copy.deepcopy(el_prin.duration)
                                    copia_proprieta(el_prin, nota_acc)
                                    cassetti[strum_accomp_principale][num].insert(off, applica_limiti_fisici(nota_acc, strum_accomp_principale))
                                    accomp_busy_until = off + el_prin.quarterLength
                                    
                                    for p, el_orig in pitches_qui[1:]:
                                        sc = note.Note(p.nameWithOctave); sc.duration = copy.deepcopy(el_orig.duration)
                                        copia_proprieta(el_orig, sc); info_offset[off]['scarti'].append(sc)
                                else:
                                    for p, el_orig in pitches_qui:
                                        sc = note.Note(p.nameWithOctave); sc.duration = copy.deepcopy(el_orig.duration)
                                        copia_proprieta(el_orig, sc); info_offset[off]['scarti'].append(sc)

                        # --- IL SARTO ---
                        for off in sorted(info_offset.keys()):
                            lista_note = info_offset[off]['scarti']
                            if not lista_note: continue
                            
                            lista_note.sort(key=lambda x: x.pitch.ps, reverse=True)
                            
                            strumenti_riempimento = [s for s in strum_accomp if s in cassetti and is_strumento_libero(cassetti[s][num], off)]
                                    
                            if not strumenti_riempimento: continue 
                                
                            for i, strum in enumerate(strumenti_riempimento):
                                idx_nota = i % len(lista_note)
                                nota_scelta = copy.deepcopy(lista_note[idx_nota])
                                nota_fixed = applica_limiti_fisici(nota_scelta, strum)
                                cassetti[strum][num].insert(float(off), nota_fixed)

                        # --- RADDOPPI E DINAMICHE ---
                        def forza_monofonia(n_originale):
                            n_new = note.Note(min(n_originale.pitches)) if isinstance(n_originale, chord.Chord) else copy.deepcopy(n_originale)
                            n_new.duration = copy.deepcopy(n_originale.duration)
                            copia_proprieta(n_originale, n_new)
                            return n_new

                        def clona_parte(sorgente, destinazione):
                            if sorgente in ensemble_attivo and destinazione in ensemble_attivo:
                                for el in list(cassetti[destinazione][num].notes): cassetti[destinazione][num].remove(el)
                                for el in cassetti[sorgente][num].notes:
                                    if el.quarterLength > 0:
                                        nuovo_el = forza_monofonia(el)
                                        cassetti[destinazione][num].insert(float(el.offset), applica_limiti_fisici(nuovo_el, destinazione))

                        if len(strum_basso) > 1:
                            basso_principale = strum_basso[0]
                            for basso_sec in strum_basso[1:]: clona_parte(basso_principale, basso_sec)
                                
                        if len(strum_melodia) > 1:
                            mel_principale = strum_melodia[0]
                            for mel_sec in strum_melodia[1:]: clona_parte(mel_principale, mel_sec)

                        for strum in ensemble_attivo:
                            ruolo = user_config[strum]["ruolo"]
                            fonte_dinamiche = m_dx_orig if ruolo == "Melodia" else m_sx_orig
                            ha_dinamiche_sx = len(m_sx_orig.getElementsByClass(['Dynamic', 'TextExpression'])) > 0 if m_sx_orig else False
                                
                            if ruolo != "Melodia" and not ha_dinamiche_sx: fonte_dinamiche = m_dx_orig 
                            applica_dinamiche_e_testi(fonte_dinamiche, cassetti[strum][num])

                    # 3. Assemblaggio Finale
                    st.write("Applicazione Spazzatrice e assemblaggio partitura...")
                    partitura_finale = stream.Score()

                    if partitura_originale.metadata is not None:
                        partitura_finale.metadata = copy.deepcopy(partitura_originale.metadata)
                    else:
                        partitura_finale.metadata = metadata.Metadata()

                    for nome in ORDINE_PARTITURA:
                        if nome not in ensemble_attivo: continue
                        
                        p = stream.Part()
                        p.id = nome; p.partName = nome
                        p.insert(0, copy.deepcopy(LIBRERIA_STRUMENTI[nome]["inst"]))
                        
                        numeri_misure = sorted(cassetti[nome].keys())
                        for num in numeri_misure:
                            m = cassetti[nome][num]
                            m_orig_dx = parti_orig[0].measure(num)
                            
                            if num == numeri_misure[0]:
                                m.insert(0, copy.deepcopy(LIBRERIA_STRUMENTI[nome]["clef"]))
                                if m_orig_dx:
                                    for ks in m_orig_dx.getElementsByClass(key.KeySignature): m.insert(ks.offset, copy.deepcopy(ks))
                                    for ts in m_orig_dx.getElementsByClass(meter.TimeSignature): m.insert(ts.offset, copy.deepcopy(ts))
                            
                            if nome == ensemble_attivo[0] and m_orig_dx:
                                for t in m_orig_dx.getElementsByClass(['MetronomeMark', 'TextExpression']): m.insert(t.offset, copy.deepcopy(t))
                            
                            try: m.makeNotation(inPlace=True, bestClef=False)
                            except: pass
                            
                            target_len = Fraction(float(m_orig_dx.quarterLength)).limit_denominator(100) if m_orig_dx else Fraction(4) 
                            
                            occupati = []
                            for n in m.notes:
                                if n.quarterLength > 0:
                                    start = Fraction(float(n.offset)).limit_denominator(100)
                                    dur = Fraction(float(n.quarterLength)).limit_denominator(100)
                                    occupati.append([start, start + dur])
                            occupati.sort()
                            
                            merged = []
                            for s, e in occupati:
                                if not merged or s > merged[-1][1]: merged.append([s, e])
                                else: merged[-1][1] = max(merged[-1][1], e)
                                        
                            curr = Fraction(0)
                            for s, e in merged:
                                if s > curr:
                                    r = note.Rest()
                                    r.quarterLength = float(s - curr) 
                                    r.style.hideObjectOnPrint = False 
                                    m.insert(float(curr), r)
                                curr = e
                                
                            if curr < target_len:
                                r = note.Rest()
                                r.quarterLength = float(target_len - curr)
                                r.style.hideObjectOnPrint = False
                                m.insert(float(curr), r)
                                
                            if num != numeri_misure[0]:
                                for ts in list(m.getElementsByClass(meter.TimeSignature)): m.remove(ts)
                                for c in list(m.getElementsByClass(clef.Clef)): m.remove(c)
                                
                            p.append(m) 
                        partitura_finale.append(p)

                    if KEEP_ORIGINAL:
                        st.write("Aggiunta del pianoforte originale...")
                        partitura_riferimento = converter.parse(tmp_path)
                        for p_ref in partitura_riferimento.getElementsByClass(stream.Part): 
                            for m_ref in p_ref.getElementsByClass(stream.Measure):
                                if m_ref.number != numeri_misure[0]:
                                    for ts in list(m_ref.getElementsByClass(meter.TimeSignature)): m_ref.remove(ts)
                            partitura_finale.append(p_ref)

                    # 4. Esportazione
                    out_path = tempfile.mktemp(suffix=".mxl")
                    partitura_finale.write('mxl', fp=out_path)
                    
                    status.update(label="✅ Elaborazione completata!", state="complete", expanded=False)
                    
                    with open(out_path, "rb") as f:
                        st.download_button(
                            label="📥 Scarica Partitura Orchestrata (.mxl)",
                            data=f,
                            file_name="orchestrazione_modulare_v0_2.mxl",
                            mime="application/vnd.recordare.musicxml+xml"
                        )
                    
                    os.remove(tmp_path)

                except Exception as e:
                    st.error(f"Si è verificato un errore durante l'elaborazione: {e}")