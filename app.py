import streamlit as st
import networkx as nx
from pyvis.network import Network
import requests
import tempfile
import os
import urllib.parse
from collections import deque
from rapidfuzz import process, fuzz

st.set_page_config(page_title="🕸️ RedBibliográfica", layout="wide", page_icon="📚")
st.title("🕸️ RedBibliográfica: Mapeo de Referencias Cruzadas")
st.caption("Explora la red de citas, resalta conexiones y diferencia lo que ya tienes de lo externo.")

# 🔹 CACHE API (1 hora)
@st.cache_data(ttl=300, show_spinner=False)
def fetch_citations(doi: str) -> list:
    doi_clean = doi.strip().strip('/')
    doi_encoded = urllib.parse.quote(doi_clean, safe='')
    
    headers = {
        "User-Agent": "RedBibliografica/1.0 (mailto:tu@email.com)",
        "Accept": "application/json"
    }

    # 1️⃣ Intento: Crossref
    url = f"https://api.crossref.org/works/{doi_encoded}"
    try:
        res = requests.get(url, headers=headers, timeout=30)
        if res.status_code == 200:
            data = res.json().get("message", {})
            refs = data.get("reference", [])
            
            if not refs:
                st.info(f"ℹ️ Paper sin referencias indexadas.")
                return []
            
            results = []
            for idx, r in enumerate(refs[:50]):  # Limitar a 50 refs para evitar timeout
                # Extraer título - Crossref tiene múltiples formatos
                title = ""
                if "unstructured" in r and r["unstructured"]:
                    title = r["unstructured"]
                elif "article-title" in r:
                    at = r["article-title"]
                    title = at[0] if isinstance(at, list) else at
                elif "journal-title" in r:
                    jt = r["journal-title"]
                    title = jt[0] if isinstance(jt, list) else jt
                
                if not title:
                    title = f"Referencia {idx+1}"
                
                # Extraer autores
                authors = []
                if "author" in r:
                    for a in r["author"]:
                        family = a.get("family", "")
                        given = a.get("given", "")
                        if family:
                            authors.append(f"{family} {given}".strip())
                
                # Obtener primera inicial del primer autor
                first_author = "?"
                if authors:
                    first_author = authors[0].split()[0] if authors[0] else "?"
                elif title:
                    # Fallback: primera palabra del título
                    first_author = title.split()[0][:1].upper()
                
                # Extraer año
                year = r.get("year", "")
                if not year:
                    for key in ["published-print", "published-online", "created"]:
                        if key in r:
                            date_parts = r[key].get("date-parts", [[None]])
                            if date_parts and date_parts[0] and date_parts[0][0]:
                                year = str(date_parts[0][0])
                                break
                
                if not year:
                    year = "?"
                
                # Construir referencia completa
                author_str = ""
                if authors:
                    if len(authors) == 1:
                        author_str = authors[0]
                    elif len(authors) == 2:
                        author_str = f"{authors[0]} y {authors[1]}"
                    else:
                        author_str = f"{authors[0]} et al."
                
                full_ref = f"{author_str} ({year}). {title}".strip()
                
                results.append({
                    'title': title.strip(), 
                    'label': f"{first_author} {year}", 
                    'full_ref': full_ref
                })
            
            st.success(f"✅ {len(results)} referencias obtenidas de Crossref")
            return results
            
        elif res.status_code in [403, 429]:
            st.warning("⏳ Límite de API Crossref. Espera 60s.")
            return []
            
    except Exception as e:
        st.warning(f"⚠️ Error Crossref: {e}. Intentando fallback...")

    # 2️⃣ Fallback: Semantic Scholar
    url_ss = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi_encoded}?fields=title,references.title,references.authors,references.year"
    try:
        res = requests.get(url_ss, headers=headers, timeout=25)
        if res.status_code == 200:
            data = res.json()
            refs = data.get("references") or []
            if not refs:
                st.info("ℹ️ Sin referencias en Semantic Scholar.")
                return []
            
            results = []
            for r in refs[:50]:
                authors = [a.get('name', '') for a in (r.get('authors') or []) if a.get('name')]
                first = authors[0].split()[-1] if authors else "?"
                year = r.get('year') or "?"
                title = r.get('title', 'Sin título')
                
                author_str = f"{', '.join(authors[:3])}{' et al.' if len(authors)>3 else ''}"
                full_ref = f"{author_str} ({year}). {title}"
                
                results.append({
                    'title': title, 
                    'label': f"{first} {year}", 
                    'full_ref': full_ref
                })
            
            st.success(f"✅ {len(results)} referencias de Semantic Scholar")
            return results
        else:
            st.warning(f"🌐 Semantic Scholar: código {res.status_code}")
            return []
    except Exception as e:
        st.error(f"❌ Error de red: {e}")
        return []
        
# 🔹 FUZZY MATCHING
def is_in_library(api_title, local_list, threshold):
    if not local_list: return False
    q = api_title.lower()
    if q in local_list: return True
    # token_set_ratio es mucho mejor para "Título largo" vs "Autor (Año). Título largo"
    match = process.extractOne(q, local_list, scorer=fuzz.token_set_ratio)
    return match is not None and match[1] >= threshold

# 🔹 CONSTRUCTOR DEL GRAFO
def build_graph(root_dois, depth, local_list, threshold):
    G = nx.DiGraph()
    local_clean = [t.strip().lower() for t in local_list if t.strip()]

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
    
    debug_count = 0
    for n, d in G.nodes(data=True):
        size = max(10, min(d["connections"] * 6 + 10, 60))
        color = "#10b981" if d["is_local"] else "#ef4444"
        
        # Validar que existan los datos
        label_text = d.get("label", "Sin etiqueta")
        full_ref = d.get("full_ref", "Sin referencia completa")
        
        # Debug: mostrar primeros 3 nodos
        if debug_count < 3:
            st.write(f"🔍 Nodo {debug_count}: label='{label_text[:30]}...', full_ref='{full_ref[:50]}...'")
            debug_count += 1
        
        # Escapar caracteres especiales para HTML
        full_ref_safe = full_ref.replace('"', '&quot;').replace("'", "&#39;").replace('\n', ' ')
        
        net.add_node(n, 
                     label=label_text, 
                     size=size, 
                     color=color,
                     title=full_ref_safe)

    for u, v in G.edges(): 
        net.add_edge(u, v, color="#475569", width=1.5)
    
    net.set_options('{"physics":{"stabilization":{"iterations":150}},"interaction":{"hover":true}}')
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
    net.save_graph(tmp.name)
    
    # 🔌 Panel lateral persistente
    js_panel = """
    <style>
        #info-panel {
            position: fixed; right: 20px; top: 80px; width: 350px; max-height: 85vh;
            background: #1e293b; color: #f1f5f9; padding: 20px; border-radius: 8px;
            border: 1px solid #334155; font-size: 13px; z-index: 1000; overflow-y: auto;
            box-shadow: 0 10px 15px -3px rgba(0,0,0,0.5); display: none;
        }
        #info-panel b { color: #10b981; font-size: 14px; display: block; margin-bottom: 10px; }
        .copy-btn {
            margin-top: 15px; padding: 8px 12px; background: #3b82f6; color: white;
            border: none; border-radius: 4px; cursor: pointer; font-size: 12px; width: 100%;
        }
        .copy-btn:hover { background: #2563eb; }
    </style>
    <div id="info-panel"></div>
    <script>
    document.addEventListener("DOMContentLoaded", () => {
        setTimeout(() => {
            const container = document.getElementById("mynetwork");
            if (container?.visNetwork) {
                const net = container.visNetwork;
                const panel = document.getElementById("info-panel");
                
                net.on("click", p => {
                    if (p.nodes.length > 0) {
                        const nodeId = p.nodes[0];
                        const nodeData = net.body.data.nodes.get(nodeId);
                        
                        if (nodeData && nodeData.title) {
                            panel.style.display = "block";
                            const refText = nodeData.title;
                            panel.innerHTML = `<b>📖 Referencia:</b><br><br>${refText}
                                <button class="copy-btn" onclick="navigator.clipboard.writeText('${refText.replace(/'/g, "\\'")}'); this.innerText='✅ Copiado!'; setTimeout(()=>this.innerText='📋 Copiar Referencia', 2000)">📋 Copiar Referencia</button>`;
                            
                            const conn = net.getConnectedNodes(nodeId);
                            const edges = net.getConnectedEdges(nodeId);
                            net.setSelection({nodes:[nodeId, ...conn], edges}, {highlightEdges:true});
                        }
                    } else {
                        panel.style.display = "none";
                        net.setSelection({nodes:[],edges:[]});
                    }
                });
            }
        }, 1200);
    });
    </script>
    """
    with open(tmp.name, "r", encoding="utf-8") as f: html = f.read()
    with open(tmp.name, "w", encoding="utf-8") as f: f.write(html.replace("</body>", js_panel + "</body>"))
    return tmp.name

# 🟢 INTERFAZ DE USUARIO
def parse_biblio_input(file, text_input):
    items = []
    if file is not None:
        try:
            content = file.read().decode("utf-8")
            items = [line.strip() for line in content.splitlines() if line.strip()]
        except Exception:
            st.warning("⚠️ No se pudo leer el archivo.")
    if text_input:
        items += [line.strip() for line in text_input.splitlines() if line.strip()]
    return items

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
    doi_list = [d.strip().rstrip(',') for d in dois.replace('\n', ',').split(',') if d.strip()]
    # Limpieza de prefijos comunes
    doi_list = [d.lstrip('DOI:').lstrip('https://doi.org/').strip() for d in doi_list]
    local_list = parse_biblio_input(uploaded_file, local_text)
    
    if not doi_list:
        st.error("❌ Ingresa al menos un DOI.")
        st.stop()

    st.info("🔎 Verificando DOI principal...")
    test_refs = fetch_citations(doi_list[0])
    
    if not test_refs:
        st.warning("⚠️ No se obtuvieron citas. La API puede estar saturada. Espera 30s y reintenta, o prueba otro DOI.")
        st.stop()
        
    st.success("✅ DOI válido. Construyendo red...")
    G = build_graph(doi_list, depth, local_list, thresh)
    
    if G.number_of_nodes() < 2:
        st.warning("⚠️ El DOI existe, pero no tiene referencias indexadas en este nivel de profundidad.")
    else:
        path = render(G)
        with open(path, "r", encoding="utf-8") as f:
            st.components.v1.html(f.read(), height=880, scrolling=True)
        st.success("✅ Red generada. Haz clic en un nodo para ver el panel persistente.")
        st.caption("🟢 En tu biblioteca | 🔴 Externa | Tamaño = Nº de conexiones cruzadas")
