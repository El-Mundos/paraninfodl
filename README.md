# paraninfodl

Descarga libros de [ebooks.paraninfo.es](https://ebooks.paraninfo.es) como PDF.

## Instalación

```bash
# Clonar / copiar el proyecto
cd paraninfo

# Crear entorno virtual
python3 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Instalar dependencias
pip install -r requirements.txt
python3 -m playwright install chromium
```

## Uso

```bash
python3 paraninfo_dl.py <url_del_libro>
```

**Ejemplo:**

```bash
python3 paraninfo_dl.py https://ebooks.paraninfo.es/reader/lenguajes-de-marcas-y-sistemas-de-gestion-de-informacion-2a-edicion-2025
```

## Cómo funciona

1. **Primera vez:** abre una ventana del navegador para que hagas login con Google. La sesión se guarda en `~/.paraninfo_session.json`.
2. **Siguientes veces:** usa la sesión guardada automáticamente. Si expira, vuelve a pedir login.
3. Descarga y descifra todas las páginas del libro.
4. Genera un PDF con todas las páginas.

## Notas

- El PDF se guarda en el directorio actual con el nombre del libro.
- La sesión guardada contiene tus cookies — no la compartas.
- Solo funciona con libros a los que tengas acceso con tu cuenta.
