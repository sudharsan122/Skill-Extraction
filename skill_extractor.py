# app.py
import streamlit as st
import os
import re
import json
import time
import tempfile
from typing import Tuple, List, Optional

# ---------- Dependencies used by original script (pdfplumber, python-docx) ----------
try:
    import pdfplumber
except Exception:
    pdfplumber = None

try:
    import docx
except Exception:
    docx = None

# ---------- Minimal Gemini client wrapper (optional) ----------
# We'll try to import the same client used in your script. If missing or key missing,
# the app will fallback to the local keyword scan.
GEMINI_CLIENT = None
try:
    from google import genai as _genai
    GEMINI_CLIENT = _genai
except Exception:
    GEMINI_CLIENT = None

# ---------- KEY RETRIEVAL ----------
def get_gemini_key() -> str:
    # 1) Streamlit Cloud secrets (secrets.toml)
    try:
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass
    # 2) Environment variable
    if os.getenv("GEMINI_API_KEY"):
        return os.getenv("GEMINI_API_KEY")
    return ""

# ---------- Text extraction (copied/adapted from your script) ----------
def extract_text_from_pdf(path: str) -> str:
    if pdfplumber is None:
        raise RuntimeError("Install pdfplumber: pip install pdfplumber")
    parts = []
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages:
            parts.append(p.extract_text() or "")
    return re.sub(r'\s+', ' ', " ".join(parts)).strip()

def extract_text_from_docx(path: str) -> str:
    if docx is None:
        raise RuntimeError("Install python-docx: pip install python-docx")
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs if p.text and p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text and cell.text.strip():
                    parts.append(cell.text.strip())
    return re.sub(r'\s+', ' ', " ".join(parts)).strip()

def extract_text_from_txt(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    return re.sub(r'\s+', ' ', raw.decode('utf-8', errors='ignore')).strip()

def extract_text(path: str) -> str:
    p = path.lower()
    if p.endswith(".pdf"):
        return extract_text_from_pdf(path)
    if p.endswith(".docx"):
        return extract_text_from_docx(path)
    if p.endswith(".txt"):
        return extract_text_from_txt(path)
    raise ValueError("Unsupported file type. Supported: .pdf, .docx, .txt")

# ---------- Local keyword list (same as your script) ----------
BASE_KEYWORDS = [
    "c", "c++", "c#", "python", "java", "javascript", "typescript", "go", "rust", "ruby", "php", "scala", "kotlin", "swift", "r",
    "react", "angular", "vue", "next.js", "svelte", "html", "css", "sass", "tailwind",
    "node.js", "express", "django", "flask", "spring boot", "spring", "laravel", "asp.net",
    "sql", "postgresql", "mysql", "mongodb", "redis", "oracle", "mssql", "cassandra",
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform", "ansible", "helm",
    "jenkins", "github actions", "gitlab-ci", "circleci",
    "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch", "xgboost", "lightgbm", "nlp", "opencv", "spacy",
    "spark", "hadoop", "etl", "airflow",
    "embedded linux", "yocto", "petalinux", "u-boot", "device tree", "kernel", "linux kernel", "bsp",
    "arm", "raspberry pi", "stm32", "nxp", "imx", "qualcomm",
    "i2c", "spi", "uart", "gpio", "pcie", "usb", "ethernet", "can", "i2s",
    "board bring-up", "firmware", "bootloader", "driver development", "kernel drivers", "device drivers",
    "git", "gdb", "cmake", "make", "gcc", "clang", "vivado", "quartus", "jtag",
    "linux", "bash", "shell", "systemd", "sysvinit", "excel", "tableau", "power bi", "docker-compose"
]

# ---------- Normalization logic (adapted) ----------
NORMALIZE_MAP = {
    "react.js": "react",
    "reactjs": "react",
    "nodejs": "node.js",
    "node js": "node.js",
    "powerbi": "power bi",
    "u boot": "u-boot",
    "u_boot": "u-boot",
    "device-tree": "device tree",
    "embedded c": "c",
    "c plus plus": "c++",
    "cplusplus": "c++",
    "usb 3 0": "usb 3.0",
    "usb3.0": "usb 3.0",
    "wi fi": "wi-fi",
    "wi-fi": "wi-fi",
    "i 2 c": "i2c",
    "i 2 s": "i2s",
    "yocto project": "yocto",
    "petalinux sdk": "petalinux",
    "system verilog": "systemverilog",
    "devops": "devops",
    "microcontrollers": "microcontroller"
}

def normalize_token(tok: str) -> str:
    if not tok:
        return tok
    s = tok.strip().lower()
    s = re.sub(r'[\_\t]+', ' ', s)
    s = re.sub(r'\s*[\/\\]+\s*', ' / ', s)
    s = s.replace(',', ' ')
    s = re.sub(r'\s+', ' ', s)
    for k, v in NORMALIZE_MAP.items():
        if s == k or k in s:
            s = s.replace(k, v)
    s = re.sub(r'(?<!\d)\.(?!\d)', ' ', s)
    s = s.strip()
    s = re.sub(r'\busb\s+3\s+0\b', 'usb 3.0', s)
    s = re.sub(r'\s+', ' ', s)
    if s == 'node':
        s = 'node.js'
    if s == 'reactjs':
        s = 'react'
    return s

def dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

# ---------- Categorization sets (same categories) ----------
LANGUAGES = {"c", "c++", "c#", "python", "java", "javascript", "typescript", "go", "rust", "ruby", "php", "scala", "kotlin", "swift", "r"}
TOOLS = {"git", "gdb", "cmake", "make", "gcc", "clang", "vivado", "quartus", "jtag", "docker", "helm", "ansible"}
PROTOCOLS = {"i2c", "spi", "uart", "gpio", "pcie", "usb", "ethernet", "can", "i2s", "wi-fi", "wifi", "lte", "bluetooth"}
PLATFORMS = {"embedded linux", "yocto", "petalinux", "u-boot", "raspberry pi", "stm32", "arm", "nxp", "imx", "xilinx zynq", "xilinx rfsoc", "xilinx mpsoc"}
DRIVERS = {"kernel drivers", "device drivers", "driver development", "bootloader", "board bring-up", "bsp", "firmware", "kernel", "linux kernel"}
OTHER_HINTS = {"linux", "bash", "shell", "systemd", "sysvinit", "excel", "tableau", "power bi", "etl", "spark", "hadoop"}

def categorize_skill(skill: str) -> str:
    low = skill.lower()
    if low in LANGUAGES:
        return "languages"
    if low in TOOLS:
        return "tools"
    if low in PROTOCOLS:
        return "protocols"
    if low in PLATFORMS:
        return "platforms"
    if low in DRIVERS:
        return "drivers"
    if low in OTHER_HINTS:
        return "other"
    if any(k in low for k in ("driver", "kernel", "bootloader", "bsp", "board bring-up", "firmware")):
        return "drivers"
    if any(k in low for k in ("linux", "embedded", "yocto", "petalinux", "u-boot")):
        return "platforms"
    if any(k in low for k in ("aws", "azure", "gcp", "docker", "kubernetes", "terraform", "ansible")):
        return "tools"
    if any(k in low for k in ("i2c", "spi", "uart", "gpio", "usb", "ethernet", "can", "i2s", "bluetooth", "wi-fi")):
        return "protocols"
    if any(k in low for k in ("python", "java", "c++", "c#", "javascript", "typescript", "go", "rust")):
        return "languages"
    return "other"

# ---------- Gemini prompt builder (same rules) ----------
def build_skills_prompt(resume_text: str, max_chars=15000) -> str:
    if len(resume_text) > max_chars:
        resume_text = resume_text[:max_chars]
    prompt = f'''
You are an extractor. Given the resume text below, return ONLY a single JSON object:

{{"skills": [<list of canonical short skill strings>] }}

Rules:
- Return skill tokens like "python", "c++", "embedded linux", "device tree", "u-boot", "yocto", "i2c", "spi", "git".
- Normalize common variants (react.js -> react, node js -> node.js, powerbi -> power bi).
- Deduplicate and return only skills actually mentioned in the resume.
- Do NOT include company names, addresses, or long descriptive sentences.
- Output EXACTLY one JSON object and nothing else.

Resume:
\"\"\"{resume_text}\"\"\"
'''.strip()
    return prompt

def call_gemini_for_skills(resume_text: str, api_key: str, max_retries=2) -> Tuple[Optional[List[str]], str]:

    if not api_key or GEMINI_CLIENT is None:
        return None, ""
    prompt = build_skills_prompt(resume_text)
    try:
        client = GEMINI_CLIENT.Client(api_key=api_key)
    except Exception:
        # some installs use genai.Client(...) or google.generativeai, try alternative
        try:
            GEMINI_CLIENT.configure(api_key=api_key)
            client = GEMINI_CLIENT
        except Exception:
            return None, ""
    for attempt in range(max_retries + 1):
        try:
            resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            raw = resp.text if hasattr(resp, "text") else str(resp)
            m = re.search(r'\{.*\}', raw, flags=re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    skills = obj.get("skills", [])
                    if isinstance(skills, list):
                        return skills, raw
                except Exception:
                    pass
            arr_match = re.search(r'\[\s*([^\]]+?)\s*\]', raw, flags=re.DOTALL)
            if arr_match:
                items = re.findall(r'["\']([^"\']+)["\']', arr_match.group(0))
                if items:
                    return items, raw
            return None, raw
        except Exception as e:
            if attempt < max_retries:
                time.sleep(0.6*(attempt+1))
                continue
            return None, "<error: {}>".format(e)

# ---------- Processing a single resume file ----------
def process_resume_file(path: str, api_key: str):
    text = extract_text(path)

    skills_raw, raw_model = call_gemini_for_skills(text, api_key)
    if skills_raw is None:
        # fallback local keyword scanner
        found = []
        text_low = text.lower()
        for kw in BASE_KEYWORDS:
            if re.search(r'\b' + re.escape(kw) + r'\b', text_low):
                found.append(kw)
        skills_raw = found

    normalized = []
    for s in skills_raw:
        if not isinstance(s, str):
            continue
        tok = normalize_token(s)
        if not tok:
            continue
        normalized.append(tok)

    normalized = dedupe_preserve_order(normalized)

    final = []
    for s in normalized:
        s2 = s.strip()
        for k, v in NORMALIZE_MAP.items():
            if s2 == k or k in s2:
                s2 = s2.replace(k, v)
        s2 = re.sub(r'\s+', ' ', s2).strip()
        final.append(s2)
    final = dedupe_preserve_order(final)

    categories = {"languages": [], "tools": [], "protocols": [], "platforms": [], "drivers": [], "other": []}
    for s in final:
        cat = categorize_skill(s)
        categories[cat].append(s)

    return {
        "all_skills": final,
        "categories": categories,
        "raw_model_output_preview": (raw_model or "")[:1000],
        "text_snippet": text[:2000]
    }

# ---------- Streamlit UI ----------
st.set_page_config(page_title="Skill Extractor", layout="wide")
st.title("Resume Skill Extractor (PDF, DOCX, TXT)")

st.markdown(
    """
Upload one or more resumes (.pdf, .docx, .txt).  
The app will attempt to use the Gemini model if a key is set in `st.secrets` or the `GEMINI_API_KEY` environment variable.
If no valid key/client is available, the app falls back to a local keyword-based extraction.
"""
)

uploaded = st.file_uploader("Upload resumes", type=["pdf", "docx", "txt"], accept_multiple_files=True)

api_key = get_gemini_key()
if api_key:
    st.success("Gemini API key found (will attempt model extraction).")
else:
    st.info("No Gemini API key found — falling back to local keyword scanner. To enable model-based extraction, add GEMINI_API_KEY to Streamlit Secrets or an environment variable.")

if uploaded:
    results = {}
    with st.spinner("Processing..."):
        for f in uploaded:
            # save to temp file because pdfplumber / docx need file path
            suffix = os.path.splitext(f.name)[1].lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(f.read())
                tmp_path = tmp.name
            try:
                out = process_resume_file(tmp_path, api_key)
                results[f.name] = out
            except Exception as e:
                results[f.name] = {"error": str(e)}
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    # show results per file
    for fname, out in results.items():
        st.header(fname)
        if "error" in out:
            st.error(out["error"])
            continue
        cols = st.columns([1, 2])
        with cols[0]:
            st.subheader("All skills")
            if out["all_skills"]:
                st.write(out["all_skills"])
            else:
                st.write("No skills found.")
            st.subheader("Categories")
            for cat, items in out["categories"].items():
                st.markdown(f"**{cat.title()} ({len(items)})**")
                if items:
                    st.write(items)
                else:
                    st.write("_none_")
        with cols[1]:
            st.subheader("Model raw preview / snippet")
            st.code(out.get("raw_model_output_preview", "(none)"))
            st.subheader("Text snippet (first 2k chars)")
            st.text(out.get("text_snippet", ""))
else:
    st.info("Upload one or more resume files to start.")
