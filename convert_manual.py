import os
import sys
import subprocess
import shutil

try:
    import markdown
except ImportError:
    print("Instalando librería markdown...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "markdown"])
    import markdown

def convert_manual_to_pdf():
    md_path = r"C:\Users\alepe\.gemini\antigravity\scratch\tensiometro_extract\MANUAL_TECNICO.md"
    html_path = r"C:\Users\alepe\.gemini\antigravity\scratch\tensiometro_extract\temp_manual.html"
    pdf_path = r"C:\Users\alepe\.gemini\antigravity\scratch\tensiometro_extract\MANUAL_TECNICO.pdf"
    artifact_pdf_path = r"C:\Users\alepe\.gemini\antigravity\brain\a2dff3a4-2419-4df1-b0d5-ead2f08f3c8b\manual_tecnico.pdf"

    if not os.path.exists(md_path):
        print(f"Error: No se encontró el archivo Markdown en {md_path}")
        return

    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # Reemplazar marcas de salto de página
    md_text = md_text.replace(r"\newpage", '<div class="page-break"></div>')

    # Convertir Markdown a HTML
    html_body = markdown.markdown(md_text, extensions=['extra', 'tables', 'fenced_code', 'toc'])

    css_styles = """
    @page {
        size: A4;
        margin: 2.5cm 2.0cm 2.5cm 2.0cm;
        @bottom-right {
            content: counter(page);
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 9pt;
            color: #555;
        }
    }
    body {
        font-family: 'Segoe UI', Arial, sans-serif;
        color: #222222;
        line-height: 1.5;
        font-size: 10.5pt;
    }
    .page-break {
        page-break-before: always;
    }
    h1 {
        font-size: 18pt;
        color: #1a365d;
        margin-top: 25px;
        margin-bottom: 12px;
        font-weight: 700;
        text-align: center;
    }
    h2 {
        font-size: 13.5pt;
        color: #2b6cb0;
        margin-top: 15px;
        margin-bottom: 10px;
        font-weight: 700;
    }
    h3 {
        font-size: 11pt;
        color: #2d3748;
        margin-top: 14px;
        margin-bottom: 6px;
        font-weight: 600;
    }
    p {
        margin-top: 0;
        margin-bottom: 8px;
        text-align: justify;
    }
    ul, ol {
        margin-top: 0;
        margin-bottom: 12px;
        padding-left: 20px;
    }
    li {
        margin-bottom: 4px;
    }
    pre {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-left: 3px solid #3182ce;
        padding: 10px 12px;
        border-radius: 4px;
        overflow-x: auto;
        margin-top: 6px;
        margin-bottom: 16px;
        page-break-inside: auto;
    }
    pre code {
        background-color: transparent;
        padding: 0;
        border-radius: 0;
        color: #1a202c;
        font-family: 'Consolas', 'Courier New', monospace;
        font-size: 8.5pt;
        line-height: 1.35;
        white-space: pre-wrap;
        word-break: break-all;
    }
    code {
        font-family: 'Consolas', 'Courier New', monospace;
        background-color: #edf2f7;
        padding: 2px 4px;
        border-radius: 3px;
        font-size: 9pt;
        color: #c53030;
    }
    hr {
        border: 0;
        height: 1px;
        background: #e2e8f0;
        margin: 20px 0;
    }
    """

    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Manual Técnico - Documentación de Código</title>
    <style>
        {css_styles}
    </style>
</head>
<body>
    {html_body}
</body>
</html>
"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print("HTML temporal generado con éxito.")

    edge_executable = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if not os.path.exists(edge_executable):
        print("Error: No se encontró Microsoft Edge en la ruta especificada.")
        return

    print("Generando PDF mediante Microsoft Edge Headless...")
    command = [
        edge_executable,
        "--headless",
        "--disable-gpu",
        f"--print-to-pdf={pdf_path}",
        html_path
    ]

    try:
        subprocess.run(command, check=True)
        print(f"PDF generado con éxito en: {pdf_path}")
        
        # Copiar al directorio de artefactos
        shutil.copy2(pdf_path, artifact_pdf_path)
        print(f"PDF copiado al directorio de artefactos: {artifact_pdf_path}")

        # Limpiar archivo temporal
        if os.path.exists(html_path):
            os.remove(html_path)
            
    except subprocess.CalledProcessError as e:
        print(f"Error al ejecutar Edge para imprimir a PDF: {e}")

if __name__ == "__main__":
    convert_manual_to_pdf()
