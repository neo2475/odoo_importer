# 🧾 Proyecto: Importación Automática de Albaranes PDF a Odoo v14

Este proyecto permite **automatizar todo el flujo de importación de albaranes de proveedores** desde archivos PDF hasta la creación de órdenes de compra en **Odoo v14**, usando una arquitectura extensible por adaptadores y un pipeline unificado.

---

## ✨ Características Principales

* ✅ Descarga automática de **PDFs desde Gmail** (filtrados por etiquetas configurables).
* 📄 Conversión de PDFs a **CSV normalizado**.
* 🛒 Creación automática de **órdenes de compra en Odoo** vía **API XML-RPC**.
* 🧹 Sistema de **adaptadores modulares** para cada proveedor.
* 🔍 Detección automática del proveedor desde el contenido del PDF.
* 🗓️ Posibilidad de procesamiento en bloque de múltiples PDFs.
* 📦 Preparado para escalar con nuevos proveedores (solo añadir adaptador).
* 🦥 Logging centralizado en consola y fichero `logs/app.log`.

---

## 🏗️ Estructura del Proyecto

```plaintext
.
├── adapters/             # Adaptadores por proveedor
│   ├── base.py          # Clase base para adaptadores
│   ├── gpautomocion.py  # Adaptador Grupo Peña Automoción
│   ├── varona.py        # Adaptador Varona
│   ├── michelin.py      # Adaptador Michelin
│   └── __init__.py      # Registro automático de adaptadores
├── core/                 # Módulos centrales
│   ├── csv_writer.py     # Generador de CSV
│   ├── gmail_downloader.py # Descarga de PDFs desde Gmail
│   ├── logger.py         # Configuración de logs
│   ├── normalize.py      # Normalización de datos
│   ├── odoo_importer.py  # Conector a Odoo vía XML-RPC
│   └── pdf.py            # Extracción de texto de PDFs
├── inbox/               # PDFs descargados (no versionar)
├── out/                 # CSVs generados (no versionar)
├── logs/                # Archivos de log (no versionar)
├── cli.py               # CLI principal para lanzar procesos
├── .env                 # Configuración local (no versionar)
├── .env.example         # Plantilla de configuración
├── .gitignore
├── pyproject.toml       # Configuración del entorno
└── README.md
```

---

## ⚙️ Configuración

1. Clona el repositorio y copia la configuración de entorno:

   ```bash
   cp .env.example .env
   ```

2. Rellena el `.env` con:

   * Credenciales de conexión a **Odoo**:

     * `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_PASSWORD`
   * Datos de acceso a **Gmail IMAP**:

     * `GMAIL_USER`, `GMAIL_PASSWORD` (contraseña de aplicación)
   * Etiquetas IMAP de Gmail:

     * `GMAIL_LABELS="Albaranes compra Varona,Albaranes compra gpautomocion,..."`

3. Instala dependencias:

   ```bash
   uv sync
   ```

---

## ▶️ Uso del pipeline

### 1. Descargar PDFs desde Gmail

```bash
uv run python cli.py --fetch-mail --inbox ./inbox
```

### 2. Procesar PDFs y generar CSVs

```bash
uv run python cli.py ./inbox --out ./out
```

### 3. Procesar e importar directamente a Odoo

```bash
uv run python cli.py ./inbox --out ./out --import
```

> Los PDFs ya procesados se mueven automáticamente a `processed/` para evitar duplicados.

---

## 📝 Logs y depuración

* Los logs se imprimen por pantalla y también se guardan en:

  ```bash
  logs/app.log
  ```

* Puedes ajustar el nivel de logs en `.env`:

  ```env
  LOG_LEVEL=DEBUG  # O INFO, WARNING, ERROR
  ```

---

## 🧪 Tests

Puedes lanzar los tests con:

```bash
pytest
```

---

## 📌 Buenas prácticas

* ❌ **Nunca subas tus credenciales ni PDFs al repositorio**.

* Añade nuevos adaptadores en `adapters/` con:

  ```python
  @register
  class NuevoProveedor(BaseAdapter):
      key = "nombre_clave"
      ...
  ```

* Revisa `logs/app.log` si algo falla.

* Si cambias la estructura de un proveedor, actualiza su adaptador.

---

## 📊 Proveedores Soportados

* ✅ Varona
* ✅ Grupo Peña Automoción
* ✅ Michelin
* ➕ Se pueden añadir nuevos en minutos.

---

✍️ **Autor**: Enrique
🛠️ Soporte técnico y desarrollo por IA colaborativa.
📦 Proyecto escalable y listo para automatización total del backoffice.
