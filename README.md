# GeoTables

A PySide6 desktop app for creating, organizing, and exporting geometry proof tables.

## Features

- Create and organize geometry proof tables
- Automatic row numbering
- Templates for common justifications
- Automatic special-character replacement
- Superscript and subscript formatting
- Customizable grid, background, and text colors
- Export tables to PDF
- Combine exported PDFs
- Save and open `.geotable` files
- Mathematical notation support
- Custom mathematical fonts

## Text Formatting

GeoTables supports automatic special-character replacement, superscripts, and subscripts.

### Special Characters

GeoTables can automatically replace character combinations with mathematical symbols.

The available replacements are defined in `backend.py`.

You can also use:

**File → Replace special chars**

to apply special-character replacement to the selected cell.

### Superscript

Type `^` to enter superscript mode.

For example:

    x^2

The `2` will be displayed as a superscript.

### Subscript

Type `_` to enter subscript mode.

For example:

    H_2

The `2` will be displayed as a subscript.

### Ending Superscript or Subscript

A character from the configured `script_end_chars` set ends the current superscript or subscript mode.

For example, if `)` is a script-ending character:

    x^2)y

The `2` is displayed as superscript, while `)y` returns to normal text.

Superscript and subscript can therefore be written inline without a closing `^` or `_`.

## Requirements

- Python
- PySide6
- pypdf

Install the dependencies with:

    pip install -r requirements.txt

## Running from Source

    python app.py

## Project Structure

    GeoTables/
    ├── app.py
    ├── backend.py
    ├── justifications.json
    ├── config.json
    ├── requirements.txt
    ├── assets/
    └── licenses/

### `app.py`

Contains the main GeoTables application and user interface.

### `backend.py`

Contains the application's backend logic, including text processing and special-character replacement.

### `justifications.json`

Contains the available geometry justifications, templates, and related data.

### `config.json`

Contains the default application configuration.

### `assets/`

Contains application assets such as fonts and icons.

### `licenses/`

Contains licenses and notices for bundled third-party assets.

## Fonts

GeoTables uses mathematical fonts to support mathematical notation and symbols.

The licenses and notices for bundled fonts can be found in the `licenses/` directory.

## GeoTable Files

GeoTables uses the `.geotable` file format to save tables.

A `.geotable` file contains the table's data and relevant settings in a JSON-based format.

## PDF Export

GeoTables can export tables as PDF files.

It can also combine multiple exported PDFs into a single PDF.

