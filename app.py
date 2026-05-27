import streamlit as st
import networkx as nx
from pyvis.network import Network
import requests
import tempfile
import os
from collections import deque
from rapidfuzz import process, fuzz

st.set_page_config(page_title="🕸️ RedBibliográfica", layout="wide", page_icon="📚")
st.title("🕸️ RedBibliográfica: Mapeo de Referencias Cruzadas")
st.caption("Explora la red de citas, resalta conexiones y diferencia lo que ya tienes de lo externo.")

# 🔹 CACHE API (1 hora)
@st.cache_data(ttl=600, show_spinner=False)
def fetch_citations(doi: str) -> list:
    # Pedimos title, authors y year
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=title,references.title,references.authors,references.year"
    try:
        res = requests.get(url, timeout=25)
        if res.status_code == 404:
            st.warning(f"🔍 DOI no encontrado: {doi}")
            return []
        elif res.status_code == 429:
            st.warning("⏳ Límite de API. Espera 1 min.")
            return []
        res.raise_for_status()
        data = res.json()
        refs = data.get("references") or []
        
        results = []
        for r in refs:
            # Extraer autores y año
            authors = [a.get('name') for a in (r.get('authors') or []) if a.get('name')]
            first_author = authors[0].split()[-1] if authors else (r.get('title','').split()[0] if r.get('title') else "?")
            year = r.get('year') or "?"
            
            # Construir referencia completa para el tooltip
            full_ref = f"{', '.join(authors[:3])}{' et al.' if len(authors)>3 else ''} ({year}). {r.get('title', '')}"
            
            results.append({
                'title': r.get('title', ''),
                'label': f"{first_author} {year}",
                'full_ref': full_ref
            })
        return results
    except Exception as e:
        st.error(f"❌ Error: {e}")
        return []
# 🔹 FUZZY MATCHING
def is_in_library(api_title, local_list, threshold):
    if not local_list: return False
    q = api_title.lower()
    if q in local_list: return True
    match = process.extractOne(q, local_list, scorer=fuzz.token_sort_ratio)
    return match is not None and match[1] >= threshold

# 🔹 CONSTRUCTOR DEL GRAFO
def build_graph(root_dois, depth, local_raw, threshold):
    G = nx.DiGraph()
    local_clean = [t.strip().lower() for t in local_raw.replace("\n", ",").split(",") if t.strip()]

    # Nodo raíz
    for doi in root_dois:
        nid = f"DOI:{doi}"
        G.add_node(nid, title=doi, level=0, is_local=True, connections=0, label=doi[:15], full_ref=f"DOI: {doi}")

    queue = deque([(f"DOI:{d}", 0) for d in root_dois])
    visited = set(f"DOI:{d}" for d in root_dois)
    
    pbar = st.progress(0.0)
    status = st.empty()

    while queue:
        node, d = queue.popleft()
        if d >= depth: continue
        
        status.text(f"🔍 Explorando nivel {d+1}...")
        doi = node.replace("DOI:", "")
        for ref in fetch_citations(doi):
            nid = f"TITLE:{ref['title']}"
            local = is_in_library(ref['title'], local_clean, threshold)
            
            # Si el nodo no existe, lo creamos con la etiqueta "Apellido Año"
            if nid not in G:
                G.add_node(nid, title=ref['title'], level=d+1, is_local=local, connections=0, 
                           label=ref['label'], full_ref=ref['full_ref'])
            
            G.add_edge(node, nid)
            G.nodes[node]["connections"] += 1
            G.nodes[nid]["connections"] += 1
            
            if nid not in visited and d + 1 < depth:
                visited.add(nid)
                queue.append((nid, d + 1))
        pbar.progress(min(1.0, len(visited) / max(len(visited), 20)))

    pbar.progress(1.0)
    status.text("✅ Grafo listo. Generando visualización...")
    return G

# 🔹 RENDER HTML + JS INTERACTIVO
def render(G):
    net = Network(height="850px", width="100%", directed=True, bgcolor="#0f172a", font_color="#e2e8f0")
    for n, d in G.nodes(data=True):
        size = max(10, min(d["connections"] * 6 + 10, 60))
        color = "#10b981" if d["is_local"] else "#ef4444"
        
        net.add_node(n, 
                     label=d["label"], 
                     size=size, 
                     color=color,
                     title=f"📖 {d['full_ref']}\n🔗 Conexiones: {d['connections']}\n📁 En biblio: {'SÍ ✅' if d['is_local'] else 'NO ❌'}")
                     
    for u, v in G.edges(): net.add_edge(u, v, color="#475569", width=1.5)
    
    net.set_options('{"physics":{"stabilization":{"iterations":150}},"interaction":{"hover":true}}')
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
    net.save_graph(tmp.name)
    
    js = """<script>
    document.addEventListener("DOMContentLoaded", () => {
        setTimeout(() => {
            const c = document.getElementById("mynetwork");
            if (c?.visNetwork) {
                const net = c.visNetwork;
                net.on("click", p => {
                    if (p.nodes.length) {
                        const conn = net.getConnectedNodes(p.nodes[0]);
                        const edges = net.getConnectedEdges(p.nodes[0]);
                        net.setSelection({nodes:[p.nodes[0],...conn], edges}, {highlightEdges:true});
                    } else net.setSelection({nodes:[],edges:[]});
                });
            }
        }, 1000);
    });
    </script>"""
    with open(tmp.name, "r", encoding="utf-8") as f: html = f.read()
    with open(tmp.name, "w", encoding="utf-8") as f: f.write(html.replace("</body>", js + "</body>"))
    return tmp.name

# 🟢 INTERFAZ DE USUARIO
def parse_biblio_input(file, text_input):
    """Combina y limpia entradas de archivo + texto manual"""
    items = []
    if file is not None:
        try:
            content = file.read().decode("utf-8")
            items = [line.strip() for line in content.replace("\n", ",").split(",") if line.strip()]
        except Exception:
            st.warning("⚠️ No se pudo leer el archivo. Usa UTF-8 o TXT/CSV.")
    if text_input:
        items += [t.strip() for t in text_input.replace("\n", ",").split(",") if t.strip()]
    return ", ".join(set(items))  # Deduplica automáticamente

with st.form("input_form"):
    dois = st.text_area("🔗 DOIs iniciales (separados por coma o salto de línea):", placeholder="10.1038/s41586-020-2003-2")
    depth = st.slider("📏 Profundidad de exploración:", 1, 3, 2)
    
    col1, col2 = st.columns([1, 2])
    with col1:
        uploaded_file = st.file_uploader("📂 Sube tu biblio local (.txt/.csv)", type=["txt", "csv"])
    with col2:
        local_text = st.text_area("✍️ O pega títulos/DOIs manuales:", placeholder="Opcional. Se sumará al archivo si subes uno.")
        
    thresh = st.slider("🎯 Sensibilidad fuzzy (%):", 70, 100, 85)
    run = st.form_submit_button("🚀 Generar Red", type="primary")

if run:
    doi_list = [d.strip().rstrip(",") for d in dois.replace("\n", ",").split(",") if d.strip()]
    local_combined = parse_biblio_input(uploaded_file, local_text)
    
    if not doi_list:
        st.error("❌ Ingresa al menos un DOI.")
    else:
        G = build_graph(doi_list, depth, local_combined, thresh)
        if G.number_of_nodes() < 2:
            st.warning("⚠️ No se encontraron citas. Verifica los DOIs o baja la profundidad.")
        else:
            path = render(G)
            with open(path, "r", encoding="utf-8") as f:
                st.components.v1.html(f.read(), height=880, scrolling=True)
            st.success("✅ Red generada. Haz clic en un nodo para resaltar sus conexiones.")
            st.caption("🟢 En tu biblioteca | 🔴 Externa | Tamaño = Nº de conexiones cruzadas")
