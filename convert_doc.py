import os
import sys
import subprocess

# 1. Asegurar que la librería markdown esté instalada
try:
    import markdown
except ImportError:
    print("Instalando librería markdown...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "markdown"])
    import markdown

def convert_md_to_pdf():
    md_path = r"C:\Users\alepe\.gemini\antigravity\scratch\tensiometro_extract\DOCUMENTACION.md"
    html_path = r"C:\Users\alepe\.gemini\antigravity\scratch\tensiometro_extract\temp_doc.html"
    pdf_path = r"C:\Users\alepe\.gemini\antigravity\scratch\tensiometro_extract\DOCUMENTACION.pdf"
    artifact_pdf_path = r"C:\Users\alepe\.gemini\antigravity\brain\a2dff3a4-2419-4df1-b0d5-ead2f08f3c8b\technical_documentation.pdf"

    if not os.path.exists(md_path):
        print(f"Error: No se encontró el archivo Markdown en {md_path}")
        return

    # Leer el archivo Markdown
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # Convertir Markdown a HTML con extensiones de tablas y bloques de código
    html_body = markdown.markdown(md_text, extensions=['extra', 'tables', 'fenced_code'])

    # Estilos CSS profesionales inspirados en la identidad del Gobierno de México y reporte clínico
    css_styles = """
    @page {
        size: A4;
        margin: 2.5cm 2cm 2.5cm 2cm;
        @bottom-right {
            content: counter(page);
            font-family: 'Segoe UI', sans-serif;
            font-size: 9pt;
            color: #666;
        }
    }
    body {
        font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Arial, sans-serif;
        color: #2b2b2b;
        line-height: 1.6;
        font-size: 11pt;
    }
    h1 {
        color: #621132; /* Guinda */
        font-size: 24pt;
        border-bottom: 3px solid #B38E5D; /* Dorado */
        padding-bottom: 8px;
        margin-top: 0;
        margin-bottom: 24px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    h2 {
        color: #621132;
        font-size: 16pt;
        border-bottom: 1.5px solid #D4C19C;
        padding-bottom: 5px;
        margin-top: 30px;
        margin-bottom: 15px;
        page-break-after: avoid;
    }
    h3 {
        color: #2b2b2b;
        font-size: 12.5pt;
        margin-top: 20px;
        margin-bottom: 8px;
        page-break-after: avoid;
    }
    p {
        margin-top: 0;
        margin-bottom: 12px;
        text-align: justify;
    }
    ul, ol {
        margin-top: 0;
        margin-bottom: 15px;
        padding-left: 20px;
    }
    li {
        margin-bottom: 6px;
    }
    code {
        font-family: 'Consolas', 'Courier New', monospace;
        background-color: #f5f5f5;
        padding: 2px 5px;
        border-radius: 3px;
        font-size: 9.5pt;
        color: #c7254e;
    }
    pre {
        background-color: #f9f9f9;
        border: 1px solid #e1e1e1;
        border-left: 4px solid #621132;
        padding: 12px 16px;
        border-radius: 4px;
        overflow-x: auto;
        margin-top: 10px;
        margin-bottom: 20px;
        page-break-inside: avoid;
    }
    pre code {
        background-color: transparent;
        padding: 0;
        border-radius: 0;
        color: #333;
        font-size: 9pt;
        white-space: pre-wrap;
    }
    table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 15px;
        margin-bottom: 25px;
        page-break-inside: avoid;
        font-size: 9.5pt;
    }
    th, td {
        border: 1px solid #d4c19c;
        padding: 10px 12px;
        text-align: left;
    }
    th {
        background-color: #621132;
        color: #ffffff;
        font-weight: 600;
        text-transform: uppercase;
        font-size: 9pt;
        letter-spacing: 0.5px;
    }
    tr:nth-child(even) {
        background-color: #fcfbfa;
    }
    blockquote {
        margin: 15px 0;
        padding: 10px 20px;
        background-color: #fbf9f6;
        border-left: 4px solid #B38E5D;
        color: #555;
        font-style: italic;
    }
    .badge-mex {
        display: inline-block;
        padding: 4px 10px;
        background-color: #f4efe7;
        color: #621132;
        border: 1px solid #B38E5D;
        border-radius: 4px;
        font-size: 8.5pt;
        font-weight: bold;
        margin-bottom: 20px;
    }
    hr {
        border: 0;
        height: 1px;
        background: #d4c19c;
        margin: 30px 0;
    }
    /* Estilos específicos para diagramas de texto o links */
    a {
        color: #621132;
        text-decoration: none;
        font-weight: bold;
    }
    a:hover {
        text-decoration: underline;
    }
    """

    # Ensamblar el documento HTML completo
    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Documentación Técnica</title>
    <style>
        {css_styles}
    </style>
</head>
<body>
    <div class="badge-mex">🇲🇽 GOBIERNO DE MÉXICO • SECRETARÍA DE SALUD</div>
    {html_body}
</body>
</html>
"""

    # Escribir el archivo HTML temporal
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print("HTML temporal generado con éxito.")

    # 4. Invocar MS Edge en modo headless para imprimir a PDF
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
        import shutil
        shutil.copy2(pdf_path, artifact_pdf_path)
        print(f"PDF copiado al directorio de artefactos: {artifact_pdf_path}")

        # Limpiar archivo HTML temporal
        if os.path.exists(html_path):
            os.remove(html_path)
            
    except subprocess.CalledProcessError as e:
        print(f"Error al ejecutar Edge para imprimir a PDF: {e}")

if __name__ == "__main__":
    convert_md_to_pdf()
