import streamlit as st
from music21 import *
import copy
from fractions import Fraction
import tempfile
import os

# ==========================================
# CONFIGURAZIONE PAGINA E STILE
# ==========================================
st.set_page_config(page_title="Orchestratore Ibrido Pro", layout="wide")

st.markdown("""
    <style>
    .reportview-container .main .block-container { padding-top: 2rem; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #ff4b4b; color: white; border: none; font-weight: bold; }
    .stButton>button:hover { background-color: #ff3333; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# FUNZIONI DI SUPPORTO E FISICA STRUMENTALE
# ==========================================

def get_octave_shift(ps, strumento):
    min_ps, max_ps = 0, 127
    if strumento == "Violino I": min_ps, max_ps = 55, 96      
    elif strumento == "Violino II": min_ps, max_ps = 55, 84   
    elif strumento == "Viola": min_ps, max_ps = 48, 79         
    elif strumento == "Violoncello": min_ps, max_ps = 36, 67   
    
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

def calcola_shift_blocco(pitches, strumento):
    if not pitches: return 0
    min_ps_strum, max_ps_strum = 0, 127
    if strumento == "Violino I": min_ps_strum, max_ps_strum = 55, 96      
    elif strumento == "Violino II": min_ps_strum, max_ps_strum = 55, 84   
    elif strumento == "Viola": min_ps_strum, max_ps_strum = 48, 79         
    elif strumento == "Violoncello": min_ps_strum, max_ps_strum = 36, 67 
    
    shift = 0
    min_p = min(pitches)
    while min_p + shift < min_ps_strum: shift += 12
    max_p = max(pitches)
    while max_p + shift > max_ps_strum and (min_p + shift - 12) >= min_ps_strum: shift -= 12
    return shift

def is_strumento_libero(misura, check_offset):
    for n in misura.notes:
        if getattr(n.duration, 'isGrace', False) or n.quarterLength == 0: continue
        inizio = float(n.offset)
        fine = inizio + float(n.quarterLength)
        if inizio <= check_offset < fine - 0.001: return False
    return True

def copia_proprieta(sorgente, destinazione):
    if hasattr(sorgente, 'tie') and sorgente.tie: 
        destinazione.tie = copy.deepcopy(sorgente.tie)
    if hasattr(sorgente, 'articulations') and sorgente.articulations:
        nuove_art = []
        for art in sorgente.articulations:
            if not isinstance(art, articulations.Fingering):
                nuove_art.append(copy.deepcopy(art))
        if nuove_art: destinazione.articulations = nuove_art

def rileva_unisono(m_dx, m_sx):
    if not m_dx or not m_sx: return False
    notes_dx = [n for n in m_dx.flatten().notes if n.quarterLength > 0 and not getattr(n.duration, 'isGrace', False)]
    notes_sx = [n for n in m_sx.flatten().notes if n.quarterLength > 0 and not getattr(n.duration, 'isGrace', False)]
    if not notes_dx or not notes_sx: return False
    pc_dx = {}
    for n in notes_dx:
        off = float(n.offset)
        if off not in pc_dx: pc_dx[off] = set()
        pcs = [p.ps % 12 for p in (n.pitches if hasattr(n, 'pitches') else [n.pitch])]
        pc_dx[off].update(pcs)
    pc_sx = {}
    for n in notes_sx:
        off = float(n.offset)
        if off not in pc_sx: pc_sx[off] = set()
        pcs = [p.ps % 12 for p in (n.pitches if hasattr(n, 'pitches') else [n.pitch])]
        pc_sx[off].update(pcs)
    common_offsets = set(pc_dx.keys()).intersection(set(pc_sx.keys()))
    if len(common_offsets) == 0: return False
    match_count = sum(1 for off in common_offsets if pc_dx[off].intersection(pc_sx[off]))
    if match_count / max(len(pc_dx), len(pc_sx)) >= 0.8: return True
    return False

def is_consecutivo(n1, n2):
    return abs(float(n2.offset) - float(n1.offset + n1.quarterLength)) < 0.001

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
    if is_dx_melodia:
        if avg_dx < avg_sx: is_melodia_bassa = True
    else:
        if avg_sx < avg_dx: is_melodia_bassa = True

    return is_dx_melodia, is_melodia_bassa

def arrangia_pattern_sinistra(tutte_note, cassetti, num, strum_pattern):
    if not tutte_note: return []
    
    s_b = strum_pattern[0]
    s_m = strum_pattern[1]
    s_h = strum_pattern[2] if len(strum_pattern) > 2 else None
    
    durate_singole = [n.quarterLength for n in tutte_note if isinstance(n, note.Note) and not getattr(n.duration, 'isGrace', False) and n.quarterLength > 0]
    if not durate_singole: return tutte_note
    min_dur = min(durate_singole)
    
    singole_veloci = [n for n in tutte_note if isinstance(n, note.Note) and not getattr(n.duration, 'isGrace', False) and abs(n.quarterLength - min_dur) < 0.001]
    
    usate = set()
    
    if len(singole_veloci) >= 3:
        singole_veloci.sort(key=lambda x: float(x.offset))
        octave_jumps = sum(1 for i in range(len(singole_veloci)-1) if abs(singole_veloci[i].pitch.ps - singole_veloci[i+1].pitch.ps) == 12)
        
        if octave_jumps >= len(singole_veloci) // 2 and octave_jumps > 0:
            low_p = min(n.pitch.ps for n in singole_veloci)
            high_p = max(n.pitch.ps for n in singole_veloci)
            
            low_note_ref = next(n for n in singole_veloci if n.pitch.ps == low_p)
            high_note_ref = next(n for n in singole_veloci if n.pitch.ps == high_p)
            
            for nj in singole_veloci:
                n_c = copy.deepcopy(low_note_ref); n_c.offset = nj.offset; n_c.duration = copy.deepcopy(nj.duration)
                copia_proprieta(nj, n_c); n_c = applica_limiti_fisici(n_c, s_b); cassetti[s_b][num].insert(n_c.offset, n_c)
                
                n_v = copy.deepcopy(high_note_ref); n_v.offset = nj.offset; n_v.duration = copy.deepcopy(nj.duration)
                copia_proprieta(nj, n_v); n_v = applica_limiti_fisici(n_v, s_m); cassetti[s_m][num].insert(n_v.offset, n_v)
                
                if s_h: 
                    n_v2 = copy.deepcopy(high_note_ref); n_v2.offset = nj.offset; n_v2.duration = copy.deepcopy(nj.duration)
                    copia_proprieta(nj, n_v2); n_v2 = applica_limiti_fisici(n_v2, s_h); cassetti[s_h][num].insert(n_v2.offset, n_v2)
                usate.add(id(nj))
            
            return [n for n in tutte_note if id(n) not in usate]

    note_by_offset = {}
    for n in singole_veloci:
        off = float(n.offset)
        if off not in note_by_offset: note_by_offset[off] = []
        note_by_offset[off].append(n)
        
    unique_offsets = sorted(note_by_offset.keys())
    
    def find_closest(target):
        for off in unique_offsets:
            if abs(off - target) < 0.001: return off
        return None

    i = 0
    while i < len(unique_offsets):
        o1 = unique_offsets[i]
        pattern_found = False
        
        for j in range(i+1, min(i+4, len(unique_offsets))):
            dur = unique_offsets[j] - o1
            if dur <= 0 or dur > 1.0: continue
            
            o2, o3, o4 = o1 + dur, o1 + 2*dur, o1 + 3*dur
            real_o2, real_o3, real_o4 = find_closest(o2), find_closest(o3), find_closest(o4)
            
            if real_o2 is not None and real_o3 is not None and real_o4 is not None:
                n1 = min(note_by_offset[o1], key=lambda x: x.pitch.ps)
                n2 = min(note_by_offset[real_o2], key=lambda x: x.pitch.ps)
                n3 = min(note_by_offset[real_o3], key=lambda x: x.pitch.ps)
                n4 = min(note_by_offset[real_o4], key=lambda x: x.pitch.ps)
                
                p1, p2, p3, p4 = n1.pitch.ps, n2.pitch.ps, n3.pitch.ps, n4.pitch.ps
                
                is_alberti = (p1 < p3 and p3 < p2 and p2 == p4)
                is_tremolo = (p2 == p4 and p1 < p2 and p3 < p4 and not is_alberti)
                is_arpeggio = (p1 < p2 and p2 < p3 and p3 < p4)
                
                if is_alberti or is_tremolo or is_arpeggio:
                    if is_alberti:
                        n_cello = copy.deepcopy(n1)
                        n_cello = applica_limiti_fisici(n_cello, s_b)
                        cassetti[s_b][num].insert(o1, n_cello)
                        usate.add(id(n1))
                        
                        for nj, real_off in zip([n2, n3, n4], [real_o2, real_o3, real_o4]):
                            n_viola = copy.deepcopy(nj); n_viola.quarterLength = dur; n_viola.pitch = copy.deepcopy(n3.pitch) 
                            n_viola = applica_limiti_fisici(n_viola, s_m); cassetti[s_m][num].insert(real_off, n_viola)
                            
                            if s_h:
                                n_vln2 = copy.deepcopy(nj); n_vln2.quarterLength = dur; n_vln2.pitch = copy.deepcopy(n2.pitch) 
                                n_vln2 = applica_limiti_fisici(n_vln2, s_h); cassetti[s_h][num].insert(real_off, n_vln2)
                            usate.add(id(nj))
                            
                    elif is_tremolo:
                        durata_doppia = dur * 2 
                        n_c1 = copy.deepcopy(n1); n_c1.quarterLength = durata_doppia; n_c1 = applica_limiti_fisici(n_c1, s_b); cassetti[s_b][num].insert(o1, n_c1)
                        n_c2 = copy.deepcopy(n3); n_c2.quarterLength = durata_doppia; n_c2 = applica_limiti_fisici(n_c2, s_b); cassetti[s_b][num].insert(real_o3, n_c2)
                        
                        if s_h:
                            n_v1 = copy.deepcopy(n1); n_v1.quarterLength = durata_doppia; n_v1 = applica_limiti_fisici(n_v1, s_m); cassetti[s_m][num].insert(o1, n_v1)
                            n_v2 = copy.deepcopy(n3); n_v2.quarterLength = durata_doppia; n_v2 = applica_limiti_fisici(n_v2, s_m); cassetti[s_m][num].insert(real_o3, n_v2)
                            shift_vln2 = calcola_shift_blocco([p1, p2, p3, p4], s_h)
                            for nj, real_off in zip([n1, n2, n3, n4], [o1, real_o2, real_o3, real_o4]):
                                n_vln2 = copy.deepcopy(nj); n_vln2.quarterLength = dur; n_vln2.pitch.octave += (shift_vln2 // 12)
                                cassetti[s_h][num].insert(real_off, n_vln2)
                                usate.add(id(nj))
                        else:
                            shift_vla = calcola_shift_blocco([p1, p2, p3, p4], s_m)
                            for nj, real_off in zip([n1, n2, n3, n4], [o1, real_o2, real_o3, real_o4]):
                                n_vla = copy.deepcopy(nj); n_vla.quarterLength = dur; n_vla.pitch.octave += (shift_vla // 12)
                                cassetti[s_m][num].insert(real_off, n_vla)
                                usate.add(id(nj))
                            
                    elif is_arpeggio:
                        n_basso = copy.deepcopy(n1)
                        n_basso = applica_limiti_fisici(n_basso, s_b)
                        cassetti[s_b][num].insert(o1, n_basso)
                        usate.add(id(n1))
                        
                        note_per_cello, note_per_alti = [], []
                        for nj in [n2, n3, n4]:
                            if nj.pitch.ps < 48: note_per_cello.append(nj)
                            else: note_per_alti.append(nj)
                        
                        for nj, real_off in zip([n2, n3, n4], [real_o2, real_o3, real_o4]):
                            if nj in note_per_cello:
                                n_c = copy.deepcopy(nj); n_c.quarterLength = dur
                                n_c = applica_limiti_fisici(n_c, s_b)
                                cassetti[s_b][num].insert(real_off, n_c)
                                usate.add(id(nj))
                            else:
                                shift_vla = calcola_shift_blocco([n.pitch.ps for n in note_per_alti], s_m)
                                n_vla = copy.deepcopy(nj); n_vla.quarterLength = dur; n_vla.pitch.octave += (shift_vla // 12)
                                cassetti[s_m][num].insert(real_off, n_vla)
                                
                                if s_h:
                                    shift_vln2 = calcola_shift_blocco([n.pitch.ps for n in note_per_alti], s_h)
                                    n_vln2 = copy.deepcopy(nj); n_vln2.quarterLength = dur; n_vln2.pitch.octave += (shift_vln2 // 12)
                                    cassetti[s_h][num].insert(real_off, n_vln2)
                                usate.add(id(nj))
                    
                    pattern_found = True
                    break 
            
        if pattern_found:
            idx4 = unique_offsets.index(real_o4)
            i = idx4 + 1
        else:
            i += 1
            
    note_rimaste = [n for n in tutte_note if id(n) not in usate]
    return note_rimaste

# ==========================================
# LAYOUT STREAMLIT PRINCIPALE
# ==========================================

col_main, col_right = st.columns([7, 3], gap="large")

# --- COLONNA DESTRA (Spiegazioni) ---
with col_right:
    st.header("📖 Come Funziona")
    st.info("Motore Ibrido v3: **Polifonia Perfetta & Spazzatrice**")
    
    st.markdown("""
    Questo strumento trasforma il pianoforte in quartetto d'archi usando **regole musicali e fisiche**:
    
    * **Priorità Melodia:** Il 'Sarto' identifica le voci principali e protegge le linee melodiche dagli incastri.
    * **Detector Unisono:** Riconosce i raddoppi percussivi e li orchestra per l'intero quartetto.
    * **Filtro Polifonico:** Preserva i 'Double Stops' ma non spezza le legature lunghe.
    * **Spazzatrice Assoluta:** Rimuove i fastidiosi avvisi di *4/4* ripetuti o *cambi chiave* invisibili nelle misure interne del file esportato, garantendo un rendering pulito.
    """)

    

    st.divider()
    st.subheader("💬 Feedback & Supporto")
    with st.form("feedback"):
        st.write("Aiutami a migliorare l'algoritmo!")
        commento = st.text_area("Suggerimenti o bug trovati:")
        if st.form_submit_button("Invia Feedback"):
            st.toast("Feedback registrato! Grazie.")
    
    st.markdown("<br>", unsafe_allow_html=True)
    st.link_button("☕ Offrimi un Caffè su Ko-Fi", "ko-fi.com/wagnard")


# --- COLONNA CENTRALE (App) ---
with col_main:
    st.title("🎻 Orchestratore Ibrido Definitivo")
    st.write("Carica un file **MusicXML (.mxl / .xml)**. Il motore arrangerà automaticamente il brano per Quartetto D'Archi.")
    
    uploaded_file = st.file_uploader("Seleziona il tuo spartito", type=['mxl', 'xml'])
    
    with st.expander("⚙️ Parametri Avanzati (Il Sarto)"):
        ENABLE_DOUBLE_STOPS = st.checkbox("Abilita Double Stops (Corde doppie per strumenti singoli)", value=True)
        KEEP_ORIGINAL = st.checkbox("Includi pianoforte originale (Modalità Sicura) nel file esportato", value=True)

    if uploaded_file is not None:
        if st.button("🚀 Avvia Orchestrazione"):
            with st.status("🎼 Inizio lavorazione...", expanded=True) as status:
                try:
                    # -- 1. CARICAMENTO --
                    st.write("Lettura del file in corso...")
                    
                    # Estrae l'estensione originale (.xml o .mxl) dal file caricato
                    estensione = os.path.splitext(uploaded_file.name)[1].lower()
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix=estensione) as tmp_in:
                        tmp_in.write(uploaded_file.getvalue())
                        tmp_path = tmp_in.name
                    
                    partitura_originale = converter.parse(tmp_path)
                    parti_orig = partitura_originale.getElementsByClass(stream.Part)
                    misure_destra = list(parti_orig[0].getElementsByClass(stream.Measure))
                    misure_sinistra = list(parti_orig[1].getElementsByClass(stream.Measure))

                    # -- 2. ESTRAZIONE E ORCHESTRAZIONE --
                    st.write("Analisi polifonica ed estrazione dei pattern...")
                    cassetti = {"Violino I": {}, "Violino II": {}, "Viola": {}, "Violoncello": {}}

                    for m_dx_orig, m_sx_orig in zip(misure_destra, misure_sinistra):
                        num = m_dx_orig.number
                        for strum in cassetti: cassetti[strum][num] = stream.Measure(number=num)
                        
                        if rileva_unisono(m_dx_orig, m_sx_orig):
                            for el in m_dx_orig.flatten().getElementsByClass(['Note', 'Chord']):
                                altezze = sorted(el.pitches) if hasattr(el, 'pitches') and el.pitches else []
                                if not altezze and hasattr(el, 'pitch'): altezze = [el.pitch]
                                if not altezze: continue 
                                    
                                p_top = altezze[-1]
                                distribuzione = [("Violino I", 0), ("Violino II", -1), ("Viola", -2), ("Violoncello", -3)]
                                for strum, shift_ottava in distribuzione:
                                    n_tutti = note.Note(p_top.nameWithOctave)
                                    n_tutti.duration = copy.deepcopy(el.duration)
                                    n_tutti.pitch.octave += shift_ottava
                                    copia_proprieta(el, n_tutti)
                                    n_tutti = applica_limiti_fisici(n_tutti, strum)
                                    cassetti[strum][num].insert(el.offset, n_tutti)
                            continue 
                            
                        is_dx_melodia, is_melodia_bassa = analizza_misure(m_dx_orig, m_sx_orig)
                        
                        fonte_melodia = m_dx_orig if is_dx_melodia else m_sx_orig
                        fonte_accomp = m_sx_orig if is_dx_melodia else m_dx_orig
                        
                        if is_melodia_bassa:
                            strum_melodia = "Violoncello"
                            s_bass, s_mid, s_hi = "Viola", "Violino II", "Violino I"
                        else:
                            strum_melodia = "Violino I"
                            s_bass, s_mid, s_hi = "Violoncello", "Viola", "Violino II"
                            
                        info_offset = {}
                        
                        # 1. ESTRAZIONE MELODIA Principale
                        if fonte_melodia:
                            offset_dict = {}
                            note_abbellimento = []
                            for el in fonte_melodia.flatten().getElementsByClass(['Note', 'Chord']):
                                if getattr(el.duration, 'isGrace', False) or el.quarterLength == 0: note_abbellimento.append(el)
                                else:
                                    off = float(el.offset)
                                    if off not in offset_dict: offset_dict[off] = []
                                    offset_dict[off].append(el)
                                    
                            for el_grace in note_abbellimento:
                                altezze = sorted(el_grace.pitches) if hasattr(el_grace, 'pitches') and el_grace.pitches else []
                                if not altezze and hasattr(el_grace, 'pitch'): altezze = [el_grace.pitch]
                                if not altezze: continue 
                                p_top = altezze[0] if is_melodia_bassa else altezze[-1]
                                nota_grace = note.Note(p_top.nameWithOctave)
                                nota_grace.duration = copy.deepcopy(el_grace.duration) 
                                copia_proprieta(el_grace, nota_grace)
                                nota_grace = applica_limiti_fisici(nota_grace, strum_melodia)
                                cassetti[strum_melodia][num].insert(el_grace.offset, nota_grace)
                                
                            melody_busy_until = -1.0 
                            for off in sorted(offset_dict.keys()):
                                if off not in info_offset: info_offset[off] = {'melodia': None, 'basso': None, 'scarti': []}
                                elementi_qui = offset_dict[off]
                                pitches_qui = []
                                for el in elementi_qui:
                                    altezze = sorted(el.pitches) if hasattr(el, 'pitches') and el.pitches else []
                                    if not altezze and hasattr(el, 'pitch'): altezze = [el.pitch]
                                    for p in altezze: pitches_qui.append((p, el))
                                
                                if not pitches_qui: continue 
                                pitches_qui.sort(key=lambda x: x[0].ps, reverse=not is_melodia_bassa) 
                                
                                if off >= melody_busy_until - 0.001:
                                    p_top, el_top = pitches_qui[0]
                                    nota_mel = note.Note(p_top.nameWithOctave) 
                                    nota_mel.duration = copy.deepcopy(el_top.duration)
                                    copia_proprieta(el_top, nota_mel)
                                    nota_mel = applica_limiti_fisici(nota_mel, strum_melodia)
                                    cassetti[strum_melodia][num].insert(off, nota_mel)
                                    melody_busy_until = off + el_top.quarterLength
                                    info_offset[off]['melodia'] = p_top.ps
                                    
                                    for p, el_orig in pitches_qui[1:]:
                                        if p.ps % 12 == p_top.ps % 12: 
                                            strum_raddoppio = s_bass if is_melodia_bassa else s_hi
                                            nota_doub = note.Note(p.nameWithOctave)
                                            nota_doub.duration = copy.deepcopy(el_orig.duration)
                                            copia_proprieta(el_orig, nota_doub)
                                            nota_doub = applica_limiti_fisici(nota_doub, strum_raddoppio)
                                            cassetti[strum_raddoppio][num].insert(off, nota_doub)
                                            continue 
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

                        # 2. ESTRAZIONE VOCI E ACCOMPAGNAMENTO
                        if fonte_accomp:
                            tutte_note_acc = list(fonte_accomp.flatten().notes)
                            durate = [n.quarterLength for n in tutte_note_acc if not getattr(n.duration, 'isGrace', False) and n.quarterLength > 0]
                            min_dur = min(durate) if durate else 1.0
                            
                            ci_sono_scarti_melodia = any(len(v['scarti']) > 0 for v in info_offset.values())
                            voci_indipendenti = [n for n in tutte_note_acc if not getattr(n.duration, 'isGrace', False) and n.quarterLength >= min_dur * 2.0]
                            
                            strum_pattern = [s_bass, s_mid, s_hi]
                            if voci_indipendenti or ci_sono_scarti_melodia:
                                strum_pattern = [s_bass, s_mid] 
                                
                            note_accomp_restanti = arrangia_pattern_sinistra(tutte_note_acc, cassetti, num, strum_pattern)
                            
                            offset_dict_acc = {}
                            grace_acc = []
                            for el in note_accomp_restanti:
                                if getattr(el.duration, 'isGrace', False) or el.quarterLength == 0: grace_acc.append(el); continue
                                off = float(el.offset)
                                if off not in offset_dict_acc: offset_dict_acc[off] = []
                                offset_dict_acc[off].append(el)
                                
                            strum_accomp_principale = s_hi if is_melodia_bassa else s_bass
                            
                            for el_grace in grace_acc:
                                 altezze = sorted(el_grace.pitches) if hasattr(el_grace, 'pitches') and el_grace.pitches else []
                                 if not altezze and hasattr(el_grace, 'pitch'): altezze = [el_grace.pitch]
                                 if not altezze: continue 
                                 p_prin = altezze[-1] if is_melodia_bassa else altezze[0]
                                 nota_grace = note.Note(p_prin.nameWithOctave)
                                 nota_grace.duration = copy.deepcopy(el_grace.duration)
                                 copia_proprieta(el_grace, nota_grace)
                                 nota_grace = applica_limiti_fisici(nota_grace, strum_accomp_principale)
                                 cassetti[strum_accomp_principale][num].insert(el_grace.offset, nota_grace)
                                 
                            accomp_busy_until = -1.0
                            for off in sorted(offset_dict_acc.keys()):
                                if off not in info_offset: info_offset[off] = {'melodia': None, 'basso': None, 'scarti': []}
                                elementi_qui = offset_dict_acc[off]
                                pitches_qui = []
                                for el in elementi_qui:
                                    altezze = sorted(el.pitches) if hasattr(el, 'pitches') and el.pitches else []
                                    if not altezze and hasattr(el, 'pitch'): altezze = [el.pitch]
                                    for p in altezze: pitches_qui.append((p, el))
                                
                                if not pitches_qui: continue 
                                pitches_qui.sort(key=lambda x: x[0].ps, reverse=is_melodia_bassa) 
                                
                                if off >= accomp_busy_until - 0.001 and is_strumento_libero(cassetti[strum_accomp_principale][num], off):
                                    p_prin, el_prin = pitches_qui[0]
                                    nota_acc = note.Note(p_prin.nameWithOctave) 
                                    nota_acc.duration = copy.deepcopy(el_prin.duration)
                                    copia_proprieta(el_prin, nota_acc)
                                    nota_acc = applica_limiti_fisici(nota_acc, strum_accomp_principale)
                                    cassetti[strum_accomp_principale][num].insert(off, nota_acc)
                                    accomp_busy_until = off + el_prin.quarterLength
                                    info_offset[off]['basso'] = p_prin.ps
                                    
                                    for p, el_orig in pitches_qui[1:]:
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

                        # 3. IL SARTO
                        for off in sorted(info_offset.keys()):
                            dati = info_offset[off]
                            lista_note = dati['scarti']
                            if not lista_note: continue
                            
                            lista_note.sort(key=lambda x: x.pitch.ps, reverse=True)
                            
                            strum_accomp_disponibili = [s_hi, s_mid, s_bass]
                            liberi_reali = [s for s in strum_accomp_disponibili if is_strumento_libero(cassetti[s][num], off)]
                                    
                            if not liberi_reali: continue 
                                
                            if len(lista_note) == 1:
                                nota_da_raddoppiare = lista_note[0]
                                for target in liberi_reali:
                                    sc_fixed = copy.deepcopy(nota_da_raddoppiare) 
                                    sc_fixed = applica_limiti_fisici(sc_fixed, target)
                                    cassetti[target][num].insert(off, sc_fixed)
                            else:
                                if not ENABLE_DOUBLE_STOPS:
                                    for i, sc in enumerate(lista_note):
                                        if i < len(liberi_reali): 
                                            target = liberi_reali[i]
                                            sc_fixed = copy.deepcopy(sc)
                                            sc_fixed = applica_limiti_fisici(sc_fixed, target)
                                            cassetti[target][num].insert(off, sc_fixed)
                                else:
                                    assegnazioni = {strum: [] for strum in liberi_reali}
                                    note_disponibili = lista_note.copy()

                                    for strum in liberi_reali:
                                        if not note_disponibili: break
                                        nota_1 = note_disponibili.pop(0)
                                        nota_1_fixed = copy.deepcopy(nota_1)
                                        nota_1_fixed = applica_limiti_fisici(nota_1_fixed, strum)
                                        assegnazioni[strum].append(nota_1_fixed)
                                        
                                        if len(note_disponibili) > len(liberi_reali) - liberi_reali.index(strum) - 1:
                                            partner_idx = -1
                                            for i, nota_2 in enumerate(note_disponibili):
                                                nota_2_test = copy.deepcopy(nota_2)
                                                nota_2_test = applica_limiti_fisici(nota_2_test, strum)
                                                intervallo = abs(nota_1_fixed.pitch.ps - nota_2_test.pitch.ps)
                                                if 3 <= intervallo <= 12:
                                                    partner_idx = i
                                                    break
                                            
                                            if partner_idx != -1:
                                                nota_2 = note_disponibili.pop(partner_idx)
                                                nota_2_fixed = copy.deepcopy(nota_2)
                                                nota_2_fixed = applica_limiti_fisici(nota_2_fixed, strum)
                                                assegnazioni[strum].append(nota_2_fixed)

                                    for strum, note_ass in assegnazioni.items():
                                        if not note_ass: continue
                                        if len(note_ass) == 1: cassetti[strum][num].insert(off, note_ass[0])
                                        else:
                                            c = chord.Chord([n.pitch for n in note_ass])
                                            c.duration = copy.deepcopy(note_ass[0].duration)
                                            copia_proprieta(note_ass[0], c)
                                            cassetti[strum][num].insert(off, c)

                    # -- 3. ASSEMBLAGGIO FINALE E PULIZIA (Spazzatrice) --
                    st.write("Applicazione Spazzatrice e frazionamento pause...")
                    partitura_finale = stream.Score()

                    nuovo_metadato = metadata.Metadata()
                    titolo_estratto = None
                    for tb in partitura_originale.getElementsByClass('TextBox'):
                        if tb.content and not tb.content.endswith('.xml'):
                            titolo_estratto = tb.content; break 
                    if partitura_originale.metadata:
                        nuovo_metadato.composer = partitura_originale.metadata.composer
                        if not titolo_estratto:
                            if partitura_originale.metadata.movementName: titolo_estratto = partitura_originale.metadata.movementName
                            elif partitura_originale.metadata.title and not partitura_originale.metadata.title.endswith('.xml'): titolo_estratto = partitura_originale.metadata.title
                    if titolo_estratto: nuovo_metadato.title = titolo_estratto
                    partitura_finale.metadata = nuovo_metadato

                    dati_strumenti = [
                        ("Violino I", clef.TrebleClef(), instrument.Violin()),
                        ("Violino II", clef.TrebleClef(), instrument.Violin()),
                        ("Viola", clef.AltoClef(), instrument.Viola()),
                        ("Violoncello", clef.BassClef(), instrument.Violoncello())
                    ]

                    for nome, clef_obj, inst_obj in dati_strumenti:
                        p = stream.Part()
                        p.id = nome; p.partName = nome
                        p.insert(0, inst_obj)
                        
                        numeri_misure = sorted(cassetti[nome].keys())
                        for num in numeri_misure:
                            m = cassetti[nome][num]
                            m_orig_dx = parti_orig[0].measure(num)
                            m_orig_sx = parti_orig[1].measure(num)
                            if m_orig_dx:
                                if hasattr(m_orig_dx, 'paddingLeft'): m.paddingLeft = m_orig_dx.paddingLeft
                                if hasattr(m_orig_dx, 'paddingRight'): m.paddingRight = m_orig_dx.paddingRight
                            
                            if num == numeri_misure[0]:
                                m.insert(0, copy.deepcopy(clef_obj))
                                if m_orig_dx:
                                    for ks in m_orig_dx.getElementsByClass(key.KeySignature): m.insert(ks.offset, copy.deepcopy(ks))
                                    for ts in m_orig_dx.getElementsByClass(meter.TimeSignature): m.insert(ts.offset, copy.deepcopy(ts))
                            
                            if nome == "Violino I" and m_orig_dx:
                                for t in m_orig_dx.getElementsByClass(['MetronomeMark', 'TextExpression']): m.insert(t.offset, copy.deepcopy(t))
                            dinamiche_unite = {}
                            if m_orig_dx:
                                for d in m_orig_dx.getElementsByClass('Dynamic'): dinamiche_unite[d.offset] = d
                            if m_orig_sx:
                                for d in m_orig_sx.getElementsByClass('Dynamic'):
                                    if d.offset not in dinamiche_unite: dinamiche_unite[d.offset] = d
                            for off, d in dinamiche_unite.items(): m.insert(off, copy.deepcopy(d))
                            
                            try:
                                m.makeNotation(inPlace=True, bestClef=False)
                            except: pass
                            
                            target_len = Fraction(0)
                            if m_orig_dx: target_len = max(target_len, Fraction(m_orig_dx.quarterLength))
                            if m_orig_sx: target_len = max(target_len, Fraction(m_orig_sx.quarterLength))
                            if target_len == 0: target_len = Fraction(4) 
                            
                            occupati = []
                            for n in m.notes:
                                if n.quarterLength > 0 and not getattr(n.duration, 'isGrace', False):
                                    occupati.append([Fraction(n.offset), Fraction(n.offset) + Fraction(n.quarterLength)])
                                    
                            occupati.sort(key=lambda x: x[0])
                            merged = []
                            for start, end in occupati:
                                if not merged:
                                    merged.append([start, end])
                                else:
                                    last = merged[-1]
                                    if start <= last[1]:
                                        last[1] = max(last[1], end)
                                    else:
                                        merged.append([start, end])
                                        
                            current_time = Fraction(0)
                            for start, end in merged:
                                if start > current_time:
                                    r = note.Rest()
                                    r.quarterLength = float(start - current_time)
                                    r.style.hideObjectOnPrint = False 
                                    m.insert(float(current_time), r)
                                current_time = end
                                
                            if current_time < target_len:
                                r = note.Rest()
                                r.quarterLength = float(target_len - current_time)
                                r.style.hideObjectOnPrint = False
                                m.insert(float(current_time), r)
                                
                            # 🧹 SPAZZATRICE ASSOLUTA
                            if num != numeri_misure[0]:
                                for ts in list(m.getElementsByClass(meter.TimeSignature)): m.remove(ts)
                                for c in list(m.getElementsByClass(clef.Clef)): m.remove(c)
                                
                            p.append(m)
                        partitura_finale.insert(0, p)

                    if KEEP_ORIGINAL:
                        st.write("Aggiunta del pianoforte originale...")
                        partitura_riferimento = converter.parse(tmp_path)
                        for p_ref in partitura_riferimento.getElementsByClass(stream.Part): 
                            for m_ref in p_ref.getElementsByClass(stream.Measure):
                                if m_ref.number != numeri_misure[0]:
                                    for ts in list(m_ref.getElementsByClass(meter.TimeSignature)): m_ref.remove(ts)
                            partitura_finale.insert(0, p_ref)

                    # -- 4. ESPORTAZIONE --
                    out_path = tempfile.mktemp(suffix=".mxl")
                    partitura_finale.write('mxl', fp=out_path)
                    
                    status.update(label="✅ Elaborazione completata!", state="complete", expanded=False)
                    
                    with open(out_path, "rb") as f:
                        st.download_button(
                            label="📥 Scarica Spartito Orchestrato (.mxl)",
                            data=f,
                            file_name="orchestrazione_ibrida.mxl",
                            mime="application/vnd.recordare.musicxml+xml"
                        )
                    
                    os.remove(tmp_path)

                except Exception as e:
                    st.error(f"Si è verificato un errore durante l'elaborazione: {e}")